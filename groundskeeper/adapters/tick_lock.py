"""Host-local mutual exclusion for scheduled automation ticks."""

from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import TextIO


class TickAlreadyRunningError(RuntimeError):
    """Another process on this host owns the automation tick."""


class TickLockError(RuntimeError):
    """The host-local lock path cannot be opened safely."""


class TickLock:
    """Advisory lock released automatically on process exit."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: TextIO | None = None

    def __enter__(self) -> TickLock:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a+", encoding="utf-8")
        except OSError as error:
            self._handle = None
            raise TickLockError(
                f"could not open host-local tick lock {self._path}: {error}"
            ) from error
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._handle.close()
            self._handle = None
            raise TickAlreadyRunningError(
                "another tick is running on this host"
            ) from error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
