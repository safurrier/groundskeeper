"""Tests for BuiltinSkillStore."""

from __future__ import annotations

from groundskeeper.adapters.builtin_store import BuiltinSkillStore


def test_builtin_store_lists_skills():
    """list_skills() finds the bundled builtin skills."""
    store = BuiltinSkillStore()
    skills = store.list_skills()
    names = {s.name for s in skills}

    assert "codex-code-review" in names
    assert "context-files" in names


def test_builtin_store_get_skill():
    """get_skill() returns the correct builtin skill."""
    store = BuiltinSkillStore()
    skill = store.get_skill("codex-code-review")

    assert skill is not None
    assert skill.name == "codex-code-review"
    assert "review" in skill.description.lower()
    assert skill.source.kind == "builtin"


def test_builtin_store_get_skill_not_found():
    """get_skill() returns None for a missing skill."""
    store = BuiltinSkillStore()
    assert store.get_skill("nonexistent-skill") is None


def test_builtin_skills_are_valid():
    """Each builtin skill has name, description, and non-empty body."""
    store = BuiltinSkillStore()
    skills = store.list_skills()

    assert len(skills) >= 2

    for skill in skills:
        assert skill.name, "name must not be empty"
        assert skill.description, "description must not be empty"
        assert skill.body, "body must not be empty"
