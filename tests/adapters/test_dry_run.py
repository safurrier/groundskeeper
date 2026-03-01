"""Tests for the DryRunRunner adapter."""

from __future__ import annotations

from pathlib import Path

from groundskeeper.adapters.dry_run import DryRunRunner
from groundskeeper.domain.models import RunContext, Skill, SkillSource


def _make_skill(body: str) -> Skill:
    return Skill(
        name="test-skill",
        description="A test skill",
        body=body,
        source=SkillSource(kind="local", path=Path("/fake")),
    )


class TestDryRunRunner:
    def test_dry_run_renders_prompt(self) -> None:
        skill = _make_skill("Hello world")
        context = RunContext(skill=skill)
        result = DryRunRunner().run(context)
        assert result.output == "Hello world"

    def test_dry_run_substitutes_arguments(self) -> None:
        skill = _make_skill("Fix this: $ARGUMENTS")
        context = RunContext(skill=skill, arguments="the bug")
        result = DryRunRunner().run(context)
        assert result.output == "Fix this: the bug"

    def test_dry_run_always_succeeds(self) -> None:
        skill = _make_skill("anything")
        context = RunContext(skill=skill)
        result = DryRunRunner().run(context)
        assert result.success is True
        assert result.exit_code == 0

    def test_dry_run_is_always_available(self) -> None:
        assert DryRunRunner().is_available() is True
