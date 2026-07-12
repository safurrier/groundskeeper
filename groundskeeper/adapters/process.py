"""The single operating-system process execution boundary."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


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
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError:
            return CommandResult(argv, cwd, 127, "", f"command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            detail = f" after {timeout} seconds" if timeout is not None else ""
            return CommandResult(
                argv, cwd, 124, "", f"command timed out{detail}: {argv[0]}"
            )
        except OSError as error:
            return CommandResult(
                argv, cwd, 126, "", f"could not run {argv[0]}: {error}"
            )
        return CommandResult(
            argv=argv,
            cwd=cwd,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
