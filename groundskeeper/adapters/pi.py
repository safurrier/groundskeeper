"""Pi process adapter for executing an already-rendered prompt."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import WorkResult
from groundskeeper.domain.config import DEFAULT_PI_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PiExecutionSettings:
    """Typed execution identity and approval policy for one Pi invocation."""

    session_id: str
    name: str
    approval: Literal["allow"] = "allow"
    timeout_seconds: int = DEFAULT_PI_TIMEOUT_SECONDS


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
        print(
            f"[groundskeeper] pi start session={settings.session_id} name={settings.name}",
            file=sys.stderr,
            flush=True,
        )
        result = self._process.run_streaming(
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
            timeout=settings.timeout_seconds,
        )
        print(
            f"[groundskeeper] pi finish session={settings.session_id} exit={result.exit_code}",
            file=sys.stderr,
            flush=True,
        )
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", result.stdout)
        return WorkResult(
            success=result.success,
            output=result.stdout,
            error=result.stderr,
            exit_code=result.exit_code,
            pull_request_url=match.group(0) if match else None,
            session_id=settings.session_id,
            session_name=settings.name,
            resume_command=f"pi --session {settings.session_id}",
        )
