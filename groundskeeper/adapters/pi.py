"""Pi process adapter for executing an already-rendered prompt."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from groundskeeper.adapters.process import PROCESS_ERROR_TAIL_CHARS, ProcessClient
from groundskeeper.domain.automation import FailureDisposition, WorkResult
from groundskeeper.domain.config import DEFAULT_PI_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PiExecutionSettings:
    """Typed execution identity and approval policy for one Pi invocation."""

    session_id: str
    name: str
    approval: Literal["allow"] = "allow"
    timeout_seconds: int = DEFAULT_PI_TIMEOUT_SECONDS


_BLOCKING_PROVIDER_PATTERNS = (
    re.compile(
        r"\bno api key found\b|\b(?:invalid|missing) (?:api key|credentials?)\b"
        r"|\b(?:authentication failed|authentication required|unauthorized|forbidden)\b"
        r"|\b(?:http(?: status)?|status(?: code)?|api error)?\s*[:=]?\s*(?:401|403)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:unknown|invalid|unavailable|missing)\s+model\b"
        r"|\bmodel\b.{0,80}\b(?:not found|unavailable|not available|invalid|unknown)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:request|access)\b.{0,80}\b(?:rejected|denied|blocked)\b.{0,80}\bpolicy\b"
        r"|\bpolicy\b.{0,80}\b(?:blocked|violation|rejected|denied)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)

_TRANSIENT_PROVIDER_PATTERNS = (
    re.compile(
        r"\b(?:codex\s+)?usage limit (?:has been )?reached\b"
        r"|\b(?:you(?:'ve| have)\s+)?hit (?:your\s+)?usage limit\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\brate[- ]?limit (?:has been )?(?:exceeded|reached)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:http(?: status)?|status(?: code)?|api error)\s*[:=]?\s*429\b"
        r"|\b429\s+too many requests\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:provider|service|server|api)\b.{0,80}"
        r"\b(?:temporarily (?:at )?capacity|at capacity|overloaded|"
        r"temporarily unavailable due to (?:high )?(?:demand|capacity))\b"
        r"|\btemporary provider capacity\b",
        re.IGNORECASE | re.DOTALL,
    ),
)


def _failure_disposition(error: str, exit_code: int) -> FailureDisposition:
    """Classify only explicit transient provider-exhaustion diagnostics."""
    if exit_code == 124:
        return FailureDisposition.BLOCKED
    if any(pattern.search(error) for pattern in _BLOCKING_PROVIDER_PATTERNS):
        return FailureDisposition.BLOCKED
    if any(pattern.search(error) for pattern in _TRANSIENT_PROVIDER_PATTERNS):
        return FailureDisposition.DEFERRED
    return FailureDisposition.BLOCKED


class PiClient:
    """Typed facade over non-interactive Pi execution."""

    def __init__(self, process: ProcessClient) -> None:
        self._process = process

    def is_available(self) -> bool:
        """Return whether the Pi executable is discoverable without invoking it."""
        return shutil.which("pi") is not None

    def supports_automation(self) -> bool:
        """Return whether the host can safely contain Pi's descendant processes."""
        return self._process.supports_streaming_process_groups()

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
        error_detail = (
            result.stderr[-PROCESS_ERROR_TAIL_CHARS:]
            if result.stderr
            else result.stdout[-PROCESS_ERROR_TAIL_CHARS:]
            if not result.success
            else ""
        )
        return WorkResult(
            success=result.success,
            output=result.stdout,
            error=error_detail,
            exit_code=result.exit_code,
            pull_request_url=match.group(0) if match else None,
            session_id=settings.session_id,
            session_name=settings.name,
            resume_command=f"pi --session {settings.session_id}",
            failure_disposition=(
                FailureDisposition.BLOCKED
                if result.success
                else _failure_disposition(result.stderr, result.exit_code)
            ),
        )
