"""Shared test fixtures for Groundskeeper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """A .groundskeeper/skills/ directory in a temp location."""
    d = tmp_path / ".groundskeeper" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def make_skill(skill_dir: Path):
    """Factory fixture: create a skill directory with SKILL.md + optional resources."""

    def _make(
        name: str,
        frontmatter: dict[str, Any],
        body: str,
        scripts: dict[str, str] | None = None,
        references: dict[str, str] | None = None,
    ) -> Path:
        p = skill_dir / name
        p.mkdir(exist_ok=True)
        fm = yaml.dump(frontmatter, default_flow_style=False)
        (p / "SKILL.md").write_text(f"---\n{fm}---\n\n{body}")
        if scripts:
            (p / "scripts").mkdir(exist_ok=True)
            for fname, content in scripts.items():
                (p / "scripts" / fname).write_text(content)
        if references:
            (p / "references").mkdir(exist_ok=True)
            for fname, content in references.items():
                (p / "references" / fname).write_text(content)
        return p

    return _make


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake git repo with .groundskeeper/ ready, chdir'd into it."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".groundskeeper" / "skills").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/ directory."""
    return Path(__file__).parent / "fixtures"
