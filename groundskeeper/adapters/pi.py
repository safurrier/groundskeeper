"""Pi process adapter for executing an already-rendered prompt."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import WorkResult


@dataclass(frozen=True)
class PiExecutionSettings:
    """Typed execution identity and approval policy for one Pi invocation."""

    session_id: str
    name: str
    approval: Literal["allow"] = "allow"


class PiClient:
    """Typed facade over non-interactive Pi execution."""

    def __init__(self, process: ProcessClient) -> None:
        self._process = process

    def is_available(self) -> bool:
        """Return whether the Pi executable is discoverable without invoking it."""
        return shutil.which("pi") is not None

    def run_prompt(
        self, prompt: str, cwd: Path, settings: PiExecutionSettings
    ) -> WorkResult:
        """Run a rendered prompt with explicit, already-approved settings."""
        result = self._process.run(
            (
                "pi",
                "--session-id",
                settings.session_id,
                "--name",
                settings.name,
                "--approve",
                "-p",
                prompt,
            ),
            cwd,
        )
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", result.stdout)
        return WorkResult(
            success=result.success,
            output=result.stdout,
            error=result.stderr,
            exit_code=result.exit_code,
            pull_request_url=match.group(0) if match else None,
        )
