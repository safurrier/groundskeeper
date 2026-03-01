"""Local skill store — scans .groundskeeper/skills/ for skill directories."""

from __future__ import annotations

import sys
from pathlib import Path

from groundskeeper.domain.errors import SkillValidationError
from groundskeeper.domain.models import Skill
from groundskeeper.domain.parser import parse_skill_file


class LocalSkillStore:
    """Scans .groundskeeper/skills/ for skill directories."""

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir

    def list_skills(self) -> list[Skill]:
        """Scan all subdirectories for SKILL.md files.

        Skips invalid skills with a warning to stderr.
        """
        skills: list[Skill] = []
        if not self._skills_dir.is_dir():
            return skills

        for child in sorted(self._skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                skills.append(parse_skill_file(skill_file, source_kind="local"))
            except (SkillValidationError, Exception) as exc:
                print(
                    f"Warning: skipping invalid skill at {skill_file}: {exc}",
                    file=sys.stderr,
                )
        return skills

    def get_skill(self, name: str) -> Skill | None:
        """Look up a skill by name.

        Args:
            name: The skill name (directory name).

        Returns:
            The parsed Skill, or None if not found.
        """
        skill_file = self._skills_dir / name / "SKILL.md"
        if not skill_file.is_file():
            return None
        try:
            return parse_skill_file(skill_file, source_kind="local")
        except SkillValidationError:
            return None
