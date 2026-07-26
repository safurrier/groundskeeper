"""Locked file-backed state for host-local scheduled automation."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import TextIO


class SchedulerStateError(RuntimeError):
    """Scheduled automation state cannot be interpreted or persisted safely."""


def _fsync_directory(path: Path) -> None:
    """Persist directory entries created or replaced directly below path."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, directory_flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _create_directory_chain_durably(path: Path) -> None:
    """Create missing directories and persist each new parent entry."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


class DailyQuotaLedger:
    """Persist task reservations consumed on one local calendar day."""

    def __init__(self, path: Path, day: date, limit: int) -> None:
        if limit <= 0:
            raise ValueError("daily attempt limit must be positive")
        self.path = path
        self.day = day
        self.limit = limit
        self.consumed = 0
        self._lock_file: TextIO | None = None

    def __enter__(self) -> DailyQuotaLedger:
        try:
            _create_directory_chain_durably(self.path.parent)
            lock_path = self.path.with_suffix(self.path.suffix + ".lock")
            self._lock_file = lock_path.open("a+", encoding="utf-8")
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
            self._load()
            self._persist()
        except (OSError, json.JSONDecodeError, SchedulerStateError) as error:
            self._release()
            if isinstance(error, SchedulerStateError):
                raise
            raise SchedulerStateError(
                f"cannot safely open daily quota ledger {self.path}: {error}"
            ) from error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()

    @property
    def remaining(self) -> int:
        """Return task reservations still available today."""
        return max(0, self.limit - self.consumed)

    def consume(self) -> None:
        """Durably reserve one available task attempt."""
        if self.remaining <= 0:
            return
        self.consumed += 1
        self._persist()

    def refund(self) -> None:
        """Refund one reservation after a proven non-attempt result."""
        if self.consumed <= 0:
            raise SchedulerStateError("cannot refund an unreserved daily attempt")
        self.consumed -= 1
        self._persist()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise SchedulerStateError(
                f"cannot safely read daily quota ledger {self.path}: {error}"
            ) from error
        if not isinstance(payload, dict) or set(payload) != {"date", "consumed"}:
            raise SchedulerStateError(
                f"daily quota ledger has invalid schema: {self.path}"
            )
        stored_date = payload["date"]
        stored = payload["consumed"]
        if not isinstance(stored_date, str):
            raise SchedulerStateError(
                f"daily quota ledger has invalid date: {self.path}"
            )
        try:
            parsed_date = date.fromisoformat(stored_date)
        except ValueError as error:
            raise SchedulerStateError(
                f"daily quota ledger has invalid date: {self.path}"
            ) from error
        if parsed_date > self.day:
            raise SchedulerStateError(
                f"daily quota ledger date is in the future: {self.path}"
            )
        if not isinstance(stored, int) or isinstance(stored, bool) or stored < 0:
            raise SchedulerStateError(
                f"daily quota ledger has invalid consumed count: {self.path}"
            )
        if parsed_date == self.day:
            self.consumed = stored

    def _persist(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(
            {"date": self.day.isoformat(), "consumed": self.consumed}
        ).encode()
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        except OSError as error:
            raise SchedulerStateError(
                f"cannot safely persist daily quota ledger {self.path}: {error}"
            ) from error

    def _release(self) -> None:
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None
