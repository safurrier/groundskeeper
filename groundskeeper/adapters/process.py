"""The single operating-system process execution boundary."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROCESS_TERMINATION_GRACE_SECONDS = 5


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

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
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
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
        finally:
            process.communicate()
