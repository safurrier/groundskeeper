import os
import signal
import sys
import time
from pathlib import Path

import pytest

from groundskeeper.adapters.process import ProcessClient


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    child_script = "import time; time.sleep(60)"
    parent_script = (
        "import pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )

    result = ProcessClient().run(
        (sys.executable, "-c", parent_script), tmp_path, timeout=1
    )

    assert result.exit_code == 124
    child_pid = int(child_pid_path.read_text())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child_pid, signal.SIGKILL)
        pytest.fail("timed-out descendant process survived process-group termination")
