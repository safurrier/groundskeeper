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
    process: subprocess.Popen[str], destination: TextIO, chunks: list[str]
) -> None:
    """Copy merged process output to both an in-memory buffer and a live stream."""
    if process.stdout is None:
        return
    for chunk in process.stdout:
        chunks.append(chunk)
        destination.write(chunk)
        destination.flush()


class ProcessClient:
    """Executes commands without shell interpolation."""

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
        """Tee combined child output to a stream while preserving it for parsing."""
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
                stderr=subprocess.STDOUT,
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

        chunks: list[str] = []
        reader = threading.Thread(
            target=_drain_process_output,
            args=(process, destination, chunks),
            daemon=True,
        )
        reader.start()
        deadline = time.monotonic() + timeout if timeout is not None else None
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree(process, drain_pipes=False)

        if not timed_out:
            if deadline is None:
                reader.join()
            else:
                reader.join(timeout=max(0.0, deadline - time.monotonic()))
                if reader.is_alive():
                    timed_out = True
                    self._terminate_process_tree(process, drain_pipes=False)
        if timed_out:
            reader.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        combined = "".join(chunks)
        if timed_out:
            detail = f" after {timeout} seconds" if timeout is not None else ""
            error = f"command timed out{detail}: {argv[0]}"
            return CommandResult(argv, cwd, 124, combined, error)
        return CommandResult(
            argv=argv,
            cwd=cwd,
            exit_code=process.returncode,
            stdout=combined,
            stderr=combined[-PROCESS_ERROR_TAIL_CHARS:] if process.returncode else "",
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
