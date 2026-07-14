"""The single operating-system process execution boundary."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

PROCESS_TERMINATION_GRACE_SECONDS = 5
PROCESS_ERROR_TAIL_CHARS = 8_000
STREAMING_PROCESS_GROUPS_SUPPORTED = os.name == "posix"


@dataclass(frozen=True)
class CommandResult:
    """Typed result of a non-interactive command invocation."""

    argv: tuple[str, ...]
    cwd: Path
    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def _drain_process_output(
    source: TextIO | None,
    destination: TextIO,
    chunks: list[str],
    stream_lock: threading.Lock,
) -> None:
    """Copy one child stream to an in-memory buffer and the shared live stream."""
    if source is None:
        return
    for chunk in source:
        chunks.append(chunk)
        with stream_lock:
            destination.write(chunk)
            destination.flush()


class ProcessClient:
    """Executes commands without shell interpolation."""

    def supports_streaming_process_groups(self) -> bool:
        """Return whether streaming workers can be terminated as one process group."""
        return STREAMING_PROCESS_GROUPS_SUPPORTED

    def run(
        self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
    ) -> CommandResult:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError:
            return CommandResult(argv, cwd, 127, "", f"command not found: {argv[0]}")
        except OSError as error:
            return CommandResult(
                argv, cwd, 126, "", f"could not run {argv[0]}: {error}"
            )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            detail = f" after {timeout} seconds" if timeout is not None else ""
            return CommandResult(
                argv, cwd, 124, "", f"command timed out{detail}: {argv[0]}"
            )

        return CommandResult(
            argv=argv,
            cwd=cwd,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def run_streaming(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        timeout: int | None = None,
        stream: TextIO | None = None,
    ) -> CommandResult:
        """Tee both child streams live while preserving stdout and stderr separately."""
        if not STREAMING_PROCESS_GROUPS_SUPPORTED:
            return CommandResult(
                argv,
                cwd,
                126,
                "",
                "streaming automation requires POSIX process-group isolation",
            )
        destination = stream or sys.stderr
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError:
            return CommandResult(argv, cwd, 127, "", f"command not found: {argv[0]}")
        except OSError as error:
            return CommandResult(
                argv, cwd, 126, "", f"could not run {argv[0]}: {error}"
            )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stream_lock = threading.Lock()
        readers = [
            threading.Thread(
                target=_drain_process_output,
                args=(process.stdout, destination, stdout_chunks, stream_lock),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_process_output,
                args=(process.stderr, destination, stderr_chunks, stream_lock),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout if timeout is not None else None
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree(process, drain_pipes=False)

        if not timed_out:
            for reader in readers:
                if deadline is None:
                    reader.join()
                else:
                    reader.join(timeout=max(0.0, deadline - time.monotonic()))
                    if reader.is_alive():
                        timed_out = True
                        self._terminate_process_tree(process, drain_pipes=False)
                        break
        if timed_out:
            for reader in readers:
                reader.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if timed_out:
            detail = f" after {timeout} seconds" if timeout is not None else ""
            error = f"command timed out{detail}: {argv[0]}"
            return CommandResult(argv, cwd, 124, stdout, error)
        return CommandResult(
            argv=argv,
            cwd=cwd,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _terminate_process_tree(
        process: subprocess.Popen[str], drain_pipes: bool = True
    ) -> None:
        """Terminate a timed-out command group, escalate if needed, then reap it."""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()

        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

        # The direct process may exit on SIGTERM while a descendant in the same
        # group ignores it. Always kill any remaining group before releasing the
        # repository lock or waiting on inherited stdout/stderr pipes.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()

        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
        finally:
            if drain_pipes:
                process.communicate()
