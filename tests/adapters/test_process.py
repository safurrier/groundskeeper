import io
import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from groundskeeper.adapters.process import PROCESS_ERROR_TAIL_CHARS, ProcessClient


def test_streaming_process_fails_closed_without_process_groups(tmp_path: Path) -> None:
    with patch(
        "groundskeeper.adapters.process.STREAMING_PROCESS_GROUPS_SUPPORTED",
        False,
    ):
        result = ProcessClient().run_streaming(
            (sys.executable, "-c", "print('must not run')"),
            tmp_path,
            stream=io.StringIO(),
        )

    assert result.exit_code == 126
    assert "requires POSIX" in result.stderr
    assert result.stdout == ""


def test_streaming_process_tees_separate_output_and_preserves_result(
    tmp_path: Path,
) -> None:
    destination = io.StringIO()
    script = (
        "import sys; "
        "print('stdout milestone', flush=True); "
        "print('stderr milestone', file=sys.stderr, flush=True)"
    )

    result = ProcessClient().run_streaming(
        (sys.executable, "-c", script), tmp_path, stream=destination
    )

    assert result.success
    assert "stdout milestone" in result.stdout
    assert "stderr milestone" not in result.stdout
    assert "stderr milestone" in result.stderr
    assert "stdout milestone" in destination.getvalue()
    assert "stderr milestone" in destination.getvalue()


def test_streaming_process_failure_keeps_output_as_error_detail(tmp_path: Path) -> None:
    destination = io.StringIO()

    result = ProcessClient().run_streaming(
        (sys.executable, "-c", "print('failed detail'); raise SystemExit(3)"),
        tmp_path,
        stream=destination,
    )

    assert result.exit_code == 3
    assert "failed detail" in result.stdout
    assert result.stderr == ""


def test_streaming_process_preserves_full_stderr_for_classification(
    tmp_path: Path,
) -> None:
    result = ProcessClient().run_streaming(
        (
            sys.executable,
            "-c",
            "import sys; "
            f"print('x' * {PROCESS_ERROR_TAIL_CHARS + 200}, file=sys.stderr); "
            "raise SystemExit(3)",
        ),
        tmp_path,
        stream=io.StringIO(),
    )

    assert result.stdout == ""
    assert len(result.stderr) > PROCESS_ERROR_TAIL_CHARS


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_streaming_timeout_kills_descendant_holding_output_pipe(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "stream-child.pid"
    child_script = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    parent_script = (
        "import pathlib, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))"
    )

    result = ProcessClient().run_streaming(
        (sys.executable, "-c", parent_script),
        tmp_path,
        timeout=1,
        stream=io.StringIO(),
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
        pytest.fail("streaming timeout left descendant alive after parent exit")


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    child_ready_path = tmp_path / "child.ready"
    child_script = (
        "import pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_ready_path)!r}).write_text('ready'); "
        "time.sleep(60)"
    )
    parent_script = (
        "import pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        f"ready = pathlib.Path({str(child_ready_path)!r}); "
        "deadline = time.monotonic() + 5; "
        "\nwhile not ready.exists() and time.monotonic() < deadline: time.sleep(0.01); "
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
