"""Claude Code runner — shells out to the claude CLI."""

from __future__ import annotations

import shutil
import subprocess

from groundskeeper.domain.models import RunContext, RunResult


class ClaudeCodeRunner:
    """Shells out to the ``claude`` CLI to execute a skill."""

    def run(self, context: RunContext) -> RunResult:
        """Execute the skill via claude CLI."""
        rendered = context.skill.render(context.arguments)
        try:
            result = subprocess.run(
                ["claude", "--print", rendered],
                capture_output=True,
                text=True,
                cwd=str(context.working_directory),
                timeout=600,
            )
            return RunResult(
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr,
                exit_code=result.returncode,
            )
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

    def is_available(self) -> bool:
        """Check if claude CLI is on PATH."""
        return shutil.which("claude") is not None
