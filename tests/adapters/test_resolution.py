"""Tests for skill resolution functions."""

from __future__ import annotations

from pathlib import Path

from groundskeeper.adapters import list_all_skills, resolve_skill
from groundskeeper.adapters.builtin_store import BuiltinSkillStore
from groundskeeper.adapters.local_store import LocalSkillStore


def test_resolve_skill_first_store_wins(skill_dir: Path, make_skill):
    """Local store shadows a builtin skill with the same name."""
    make_skill(
        "codex-code-review",
        {"name": "codex-code-review", "description": "Local override"},
        "Local body.",
    )

    local = LocalSkillStore(skill_dir)
    builtin = BuiltinSkillStore()

    skill = resolve_skill("codex-code-review", [local, builtin])

    assert skill is not None
    assert skill.source.kind == "local"
    assert skill.description == "Local override"


def test_resolve_skill_falls_through(skill_dir: Path):
    """If not in the first store, found in the second."""
    local = LocalSkillStore(skill_dir)
    builtin = BuiltinSkillStore()

    skill = resolve_skill("codex-code-review", [local, builtin])

    assert skill is not None
    assert skill.source.kind == "builtin"


def test_resolve_skill_not_found(skill_dir: Path):
    """Returns None if no store has the skill."""
    local = LocalSkillStore(skill_dir)
    builtin = BuiltinSkillStore()

    assert resolve_skill("totally-missing", [local, builtin]) is None


def test_list_all_skills_deduplicates(skill_dir: Path, make_skill):
    """Local skill with the same name as builtin appears only once."""
    make_skill(
        "codex-code-review",
        {"name": "codex-code-review", "description": "Local override"},
        "Local body.",
    )

    local = LocalSkillStore(skill_dir)
    builtin = BuiltinSkillStore()

    skills = list_all_skills([local, builtin])
    matching = [s for s in skills if s.name == "codex-code-review"]

    assert len(matching) == 1
    assert matching[0].source.kind == "local"


def test_list_all_skills_combines_stores(skill_dir: Path, make_skill):
    """Skills from both stores appear in the result."""
    make_skill(
        "custom-skill",
        {"name": "custom-skill", "description": "Custom"},
        "Custom body.",
    )

    local = LocalSkillStore(skill_dir)
    builtin = BuiltinSkillStore()

    skills = list_all_skills([local, builtin])
    names = {s.name for s in skills}

    assert "custom-skill" in names
    assert "codex-code-review" in names
    assert "context-files" in names
