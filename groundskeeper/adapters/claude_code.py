"""Claude Code runner — shells out to the claude CLI."""

from __future__ import annotations

import json
import shutil
import subprocess

from groundskeeper.domain.models import RunContext, RunResult


class ClaudeCodeRunner:
    """Shells out to the ``claude`` CLI to execute a skill."""

    def run(self, context: RunContext) -> RunResult:
        """Execute the skill via claude CLI."""
        rendered = context.skill.render(context.arguments)
        cmd = ["claude", "-p", rendered, "--output-format", "json"]

        if context.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        else:
            tools = context.allowed_tools_override or context.skill.allowed_tools
            for tool in tools:
                cmd.extend(["--allowedTools", tool])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(context.working_directory),
                timeout=600,
            )
            return self._parse_result(result)
        except FileNotFoundError:
            return RunResult(
                success=False,
                error="claude CLI not found. Install it from https://claude.ai/code",
                exit_code=1,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False,
                error="claude CLI timed out after 600 seconds",
                exit_code=1,
            )

    def _parse_result(self, result: subprocess.CompletedProcess[str]) -> RunResult:
        """Parse claude CLI JSON output into RunResult."""
        try:
            data = json.loads(result.stdout)
            return RunResult(
                success=result.returncode == 0 and not data.get("is_error", False),
                output=data.get("result", ""),
                error=result.stderr,
                exit_code=result.returncode,
                metadata={
                    k: v for k, v in data.items() if k not in {"result", "is_error"}
                },
            )
        except (json.JSONDecodeError, KeyError):
            return RunResult(
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr,
                exit_code=result.returncode,
            )

    def is_available(self) -> bool:
        """Check if claude CLI is on PATH."""
        return shutil.which("claude") is not None
