"""Dry-run runner — renders the prompt without executing."""

from __future__ import annotations

from groundskeeper.domain.models import RunContext, RunResult


class DryRunRunner:
    """Prints the rendered prompt without executing. Always available."""

    def run(self, context: RunContext) -> RunResult:
        """Render the skill and return the output without execution."""
        rendered = context.skill.render(context.arguments)
        return RunResult(success=True, output=rendered, exit_code=0)

    def is_available(self) -> bool:
        return True
