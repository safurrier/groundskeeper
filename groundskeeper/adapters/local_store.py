"""Local skill store — scans .groundskeeper/skills/ for skill directories."""

from __future__ import annotations

import sys
from pathlib import Path

from groundskeeper.domain.errors import SkillValidationError
from groundskeeper.domain.models import Skill
from groundskeeper.domain.parser import parse_skill_file


class LocalSkillStore:
    """Scans .groundskeeper/skills/ for skill directories."""

    def __init__(self, skills_dir: Path, source_kind: str = "local") -> None:
        self._skills_dir = skills_dir
        self._source_kind = source_kind

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
                skills.append(
                    parse_skill_file(skill_file, source_kind=self._source_kind)
                )
            except (SkillValidationError, Exception) as exc:
                print(
                    f"Warning: skipping invalid skill at {skill_file}: {exc}",
                    file=sys.stderr,
                )
        return skills

    def get_skill(self, name: str) -> Skill | None:
        """Look up a skill by name.

        First tries a direct path lookup (directory name == skill name).
        Falls back to scanning all subdirectories and matching by the
        ``name`` field in SKILL.md frontmatter.

        Args:
            name: The skill name (from frontmatter, not necessarily the dir name).

        Returns:
            The parsed Skill, or None if not found.
        """
        # Fast path: directory name matches skill name
        skill_file = self._skills_dir / name / "SKILL.md"
        if skill_file.is_file():
            try:
                return parse_skill_file(skill_file, source_kind=self._source_kind)
            except SkillValidationError:
                pass

        # Slow path: scan all dirs and match by frontmatter name
        for skill in self.list_skills():
            if skill.name == name:
                return skill
        return None
