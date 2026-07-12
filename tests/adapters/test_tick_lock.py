from pathlib import Path

import pytest

from groundskeeper.adapters.tick_lock import TickAlreadyRunningError, TickLock


def test_tick_lock_rejects_concurrent_owner_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "daily.lock"
    with TickLock(path):
        with pytest.raises(TickAlreadyRunningError, match="another tick"):
            with TickLock(path):
                pass
    with TickLock(path):
        pass
