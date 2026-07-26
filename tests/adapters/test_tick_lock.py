from pathlib import Path

import pytest

from groundskeeper.adapters.tick_lock import (
    TickAlreadyRunningError,
    TickLock,
    TickLockError,
)


def test_tick_lock_rejects_concurrent_owner_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "daily.lock"
    with TickLock(path):
        with pytest.raises(TickAlreadyRunningError, match="another tick"):
            with TickLock(path):
                pass
    with TickLock(path):
        pass


def test_tick_lock_translates_state_path_failure(tmp_path: Path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied")

    with pytest.raises(TickLockError):
        with TickLock(parent_file / "daily.lock"):
            pass
