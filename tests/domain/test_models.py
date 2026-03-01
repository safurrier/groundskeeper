"""Tests for domain models."""

from __future__ import annotations

from pathlib import Path

import pytest

from groundskeeper.domain.models import RunContext, RunResult, Skill, SkillSource


class TestSkill:
    def test_render_substitutes_arguments(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="Do the thing with $ARGUMENTS",
            source=SkillSource(kind="local", path=Path(".")),
        )
        assert skill.render("hello world") == "Do the thing with hello world"

    def test_render_no_arguments_replaces_with_empty(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="Do the thing with $ARGUMENTS",
            source=SkillSource(kind="local", path=Path(".")),
        )
        assert skill.render() == "Do the thing with "

    def test_render_explicit_empty_string(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="Do the thing with $ARGUMENTS",
            source=SkillSource(kind="local", path=Path(".")),
        )
        assert skill.render("") == "Do the thing with "

    def test_render_multiple_occurrences(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="First: $ARGUMENTS, Second: $ARGUMENTS",
            source=SkillSource(kind="local", path=Path(".")),
        )
        assert skill.render("foo") == "First: foo, Second: foo"

    def test_render_no_placeholder(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="No placeholders here",
            source=SkillSource(kind="local", path=Path(".")),
        )
        assert skill.render("foo") == "No placeholders here"

    def test_skill_defaults(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="body",
            source=SkillSource(kind="local", path=Path(".")),
        )
        assert skill.allowed_tools == []
        assert skill.argument_hint == ""
        assert skill.tags == []
        assert skill.triggers == {}
        assert skill.metadata == {}

    def test_skill_is_frozen(self):
        skill = Skill(
            name="test",
            description="Test skill",
            body="body",
            source=SkillSource(kind="local", path=Path(".")),
        )
        with pytest.raises(AttributeError):
            skill.name = "changed"  # type: ignore[misc]


class TestSkillSource:
    def test_source_kinds(self):
        local = SkillSource(kind="local", path=Path("/a"))
        builtin = SkillSource(kind="builtin", path=Path("/b"))
        assert local.kind == "local"
        assert builtin.kind == "builtin"


class TestRunContext:
    def test_defaults(self):
        skill = Skill(
            name="test",
            description="Test",
            body="body",
            source=SkillSource(kind="local", path=Path(".")),
        )
        ctx = RunContext(skill=skill)
        assert ctx.arguments == ""
        assert ctx.working_directory == Path.cwd()


class TestRunResult:
    def test_success(self):
        result = RunResult(success=True, output="done", exit_code=0)
        assert result.success is True
        assert result.output == "done"

    def test_failure(self):
        result = RunResult(success=False, error="boom", exit_code=1)
        assert result.success is False
        assert result.error == "boom"
        assert result.exit_code == 1
