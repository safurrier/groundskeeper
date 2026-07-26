from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from groundskeeper.adapters.scheduler_state import (
    DailyQuotaLedger,
    SchedulerStateError,
)
from groundskeeper.scheduler import (
    ScheduledTick,
    run_scheduled_automations,
)


def test_reserved_non_attempt_is_refunded(tmp_path: Path) -> None:
    ledger = DailyQuotaLedger(tmp_path / "quota.json", date(2026, 7, 26), 1)

    with ledger:
        runs = run_scheduled_automations(
            ["one"],
            ledger,
            rotation=0,
            tick=lambda name: ScheduledTick(name, "no-work", 0, None),
        )

    assert [(run.status, run.consumed_attempt) for run in runs] == [("no-work", False)]
    assert ledger.consumed == 0


def test_reserved_failure_remains_consumed(tmp_path: Path) -> None:
    ledger = DailyQuotaLedger(tmp_path / "quota.json", date(2026, 7, 26), 1)

    with ledger:
        runs = run_scheduled_automations(
            ["one"],
            ledger,
            rotation=0,
            tick=lambda name: ScheduledTick(name, "error", 2, None),
        )

    assert runs[0].consumed_attempt is True
    assert ledger.consumed == 1


def test_scheduler_rotates_and_requeues_until_quota(tmp_path: Path) -> None:
    ledger = DailyQuotaLedger(tmp_path / "quota.json", date(2026, 7, 26), 2)
    calls: list[str] = []

    def tick(name: str) -> ScheduledTick:
        calls.append(name)
        if name == "two":
            return ScheduledTick(name, "no-work", 0, None)
        return ScheduledTick(name, "review", 0, {"id": str(len(calls))})

    with ledger:
        runs = run_scheduled_automations(
            ["one", "two", "three"],
            ledger,
            rotation=1,
            tick=tick,
        )

    assert calls == ["two", "three", "one"]
    assert [run.automation for run in runs] == calls
    assert [run.consumed_attempt for run in runs] == [False, True, True]
    assert ledger.consumed == 2


def test_daily_quota_resets_on_a_new_day(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    with DailyQuotaLedger(path, date(2026, 7, 26), 1) as first:
        first.consume()

    with DailyQuotaLedger(path, date(2026, 7, 27), 1) as second:
        assert second.consumed == 0


def test_lower_limit_does_not_erase_same_day_consumption(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    with DailyQuotaLedger(path, date(2026, 7, 26), 5) as first:
        for _ in range(5):
            first.consume()

    with DailyQuotaLedger(path, date(2026, 7, 26), 1) as lowered:
        assert lowered.consumed == 5
        assert lowered.remaining == 0

    with DailyQuotaLedger(path, date(2026, 7, 26), 5) as restored:
        assert restored.consumed == 5
        assert restored.remaining == 0


@pytest.mark.parametrize(
    "payload",
    [
        '{"date":null,"consumed":0}',
        '{"date":"not-a-date","consumed":0}',
        '{"date":"2026-07-27","consumed":0}',
        '{"date":"2026-07-26"}',
    ],
)
def test_invalid_or_future_ledger_date_fails_closed(
    tmp_path: Path, payload: str
) -> None:
    path = tmp_path / "quota.json"
    path.write_text(payload)

    with pytest.raises(SchedulerStateError):
        with DailyQuotaLedger(path, date(2026, 7, 26), 1):
            pass


def test_state_path_failure_is_translated(tmp_path: Path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied")

    with pytest.raises(SchedulerStateError):
        with DailyQuotaLedger(parent_file / "daily-quota.json", date(2026, 7, 26), 1):
            pass


def test_quota_persist_fsyncs_file_before_replace_and_directory_after(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    path = tmp_path / "quota.json"

    from groundskeeper.adapters import scheduler_state

    real_replace = scheduler_state.os.replace
    with (
        patch.object(
            scheduler_state.os,
            "fsync",
            side_effect=lambda _fd: events.append("fsync"),
        ),
        patch.object(
            scheduler_state.os,
            "replace",
            side_effect=lambda source, destination: (
                events.append("replace"),
                real_replace(source, destination),
            )[-1],
        ),
    ):
        with DailyQuotaLedger(path, date(2026, 7, 26), 1):
            pass

    assert events == ["fsync", "replace", "fsync"]


def test_first_quota_directory_chain_is_persisted_in_each_parent(
    tmp_path: Path,
) -> None:
    directories: list[Path] = []
    path = tmp_path / "state" / "schedules" / "daily" / "quota.json"

    from groundskeeper.adapters import scheduler_state

    with patch.object(
        scheduler_state,
        "_fsync_directory",
        side_effect=lambda directory: directories.append(directory),
    ):
        with DailyQuotaLedger(path, date(2026, 7, 26), 1):
            pass

    assert directories == [
        tmp_path,
        tmp_path / "state",
        tmp_path / "state" / "schedules",
        tmp_path / "state" / "schedules" / "daily",
    ]
