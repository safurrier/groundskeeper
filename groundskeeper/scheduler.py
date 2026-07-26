"""Application lifecycle for host-local scheduled automation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from groundskeeper.adapters.execution_source import (
    ExecutionSource,
    ExecutionSourceManager,
    resolve_execution_path,
    validate_execution_tree,
)
from groundskeeper.adapters.process import ProcessClient
from groundskeeper.adapters.scheduler_state import DailyQuotaLedger
from groundskeeper.adapters.tick_lock import TickLock


class SchedulerError(RuntimeError):
    """A scheduled run cannot proceed without violating its contract."""


@dataclass(frozen=True)
class ScheduledTick:
    """Normalized result from one internal automation tick."""

    automation: str
    status: str
    exit_code: int
    task: dict[str, object] | None
    detail: str | None = None
    pull_request_url: str | None = None
    consumed_attempt: bool = False
    operator_detail: str | None = None


@dataclass(frozen=True)
class ScheduledRunRequest:
    """Typed policy for one invocation by a host scheduler."""

    schedule_id: str
    source_repository_path: Path
    source_ref: str
    source_refresh: str
    config_path: Path
    names: tuple[str, ...]
    daily_attempt_limit: int
    rotation: int
    day: date


@dataclass(frozen=True)
class ScheduledRun:
    """Complete deterministic result of one scheduled invocation."""

    request: ScheduledRunRequest
    execution_source: ExecutionSource
    consumed_today: int
    runs: tuple[ScheduledTick, ...]


PreflightScheduled = Callable[[Path, tuple[str, ...]], list[str]]
TickScheduled = Callable[[Path, str], ScheduledTick]


class ScheduledAutomationService:
    """Own source pinning, locks, preflight, quota, and tick ordering."""

    def __init__(self, state_root: Path, process: ProcessClient) -> None:
        self._state_root = state_root
        self._process = process

    def run(
        self,
        request: ScheduledRunRequest,
        *,
        preflight: PreflightScheduled,
        tick: TickScheduled,
    ) -> ScheduledRun:
        """Execute a request from one immutable source snapshot."""
        manager = ExecutionSourceManager(
            self._process,
            request.source_repository_path,
            request.source_ref,
            request.source_refresh,
            self._state_root,
        )
        common_dir = manager.common_dir()
        with TickLock(self._schedule_lock_path(request.schedule_id)):
            # Keep the source lock for the complete run. The managed worktree is
            # a bounded reusable lease and cannot advance while callbacks read it.
            with TickLock(self._source_lock_path(common_dir)):
                source = manager.prepare()
                config_path = resolve_execution_path(source.path, request.config_path)
                if not config_path.is_file():
                    raise SchedulerError(
                        f"scheduled automation config does not exist: {config_path}"
                    )
                validate_execution_tree(source.path, config_path.parent / "skills")
                selected_names = preflight(config_path, request.names)
                with DailyQuotaLedger(
                    self._quota_path(request.schedule_id),
                    request.day,
                    request.daily_attempt_limit,
                ) as quota:
                    runs = run_scheduled_automations(
                        selected_names,
                        quota,
                        rotation=request.rotation,
                        tick=lambda name: tick(config_path, name),
                    )
                    consumed = quota.consumed
        return ScheduledRun(request, source, consumed, tuple(runs))

    def _source_lock_path(self, git_common_dir: Path) -> Path:
        """Serialize all fetch and worktree mutations in one Git common dir."""
        source_key = hashlib.sha256(str(git_common_dir.resolve()).encode()).hexdigest()[
            :16
        ]
        return self._state_root / "locks" / f"execution-source-{source_key}.lock"

    def _schedule_lock_path(self, schedule_id: str) -> Path:
        return self._state_root / "locks" / f"schedule-{schedule_id}.lock"

    def _quota_path(self, schedule_id: str) -> Path:
        return self._state_root / "schedules" / schedule_id / "daily-quota.json"


def run_scheduled_automations(
    names: list[str],
    ledger: DailyQuotaLedger,
    *,
    rotation: int,
    tick: Callable[[str], ScheduledTick],
) -> list[ScheduledTick]:
    """Run sequential ticks under one crash-safe shared daily quota."""
    if not names or ledger.remaining <= 0:
        return []
    offset = rotation % len(names)
    active = names[offset:] + names[:offset]
    results: list[ScheduledTick] = []

    while active and ledger.remaining > 0:
        next_pass: list[str] = []
        for name in active:
            if ledger.remaining <= 0:
                break
            ledger.consume()
            result = tick(name)
            reserved = True
            if result.status in {"no-work", "not-claimed"}:
                ledger.refund()
                reserved = False
            elif result.status in {"review", "deferred", "blocked"} and not isinstance(
                result.task, dict
            ):
                result = replace(
                    result,
                    status="error",
                    exit_code=2,
                    detail="task status omitted its task object",
                )

            normalized = replace(result, consumed_attempt=reserved)
            results.append(normalized)
            if reserved and normalized.status in {"review", "blocked"}:
                next_pass.append(name)
        active = next_pass
    return results
