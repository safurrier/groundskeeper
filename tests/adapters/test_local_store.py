"""Tests for LocalSkillStore."""

from __future__ import annotations

from pathlib import Path

from groundskeeper.adapters.local_store import LocalSkillStore


def test_local_store_discovers_skills(skill_dir: Path, make_skill):
    """list_skills() finds all valid skill directories."""
    make_skill(
        "alpha",
        {"name": "alpha", "description": "First skill"},
        "Do alpha things.",
    )
    make_skill(
        "beta",
        {"name": "beta", "description": "Second skill"},
        "Do beta things.",
    )

    store = LocalSkillStore(skill_dir)
    skills = store.list_skills()

    names = [s.name for s in skills]
    assert "alpha" in names
    assert "beta" in names
    assert len(skills) == 2


def test_local_store_get_skill(skill_dir: Path, make_skill):
    """get_skill() returns a parsed Skill when it exists."""
    make_skill(
        "my-skill",
        {"name": "my-skill", "description": "A test skill"},
        "Skill body content.",
    )

    store = LocalSkillStore(skill_dir)
    skill = store.get_skill("my-skill")

    assert skill is not None
    assert skill.name == "my-skill"
    assert skill.description == "A test skill"
    assert skill.body == "Skill body content."
    assert skill.source.kind == "local"


def test_local_store_get_skill_not_found(skill_dir: Path):
    """get_skill() returns None for a missing skill."""
    store = LocalSkillStore(skill_dir)
    assert store.get_skill("nonexistent") is None


def test_local_store_ignores_invalid_skills(skill_dir: Path, make_skill, capsys):
    """list_skills() skips skills with bad frontmatter."""
    # Create a valid skill
    make_skill(
        "good-skill",
        {"name": "good-skill", "description": "Valid"},
        "Valid body.",
    )

    # Create an invalid skill (missing required fields)
    bad_dir = skill_dir / "bad-skill"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text("not valid frontmatter at all")

    store = LocalSkillStore(skill_dir)
    skills = store.list_skills()

    assert len(skills) == 1
    assert skills[0].name == "good-skill"

    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_local_store_empty_dir(skill_dir: Path):
    """list_skills() returns [] for an empty directory."""
    store = LocalSkillStore(skill_dir)
    assert store.list_skills() == []
