"""Builtin skill store — reads skills bundled with the groundskeeper package."""

from __future__ import annotations

import sys
from pathlib import Path

from groundskeeper.domain.errors import SkillValidationError
from groundskeeper.domain.models import Skill
from groundskeeper.domain.parser import parse_skill_file

_BUILTINS_DIR = Path(__file__).parent.parent / "builtins" / "skills"


class BuiltinSkillStore:
    """Reads skills bundled with the groundskeeper package."""

    def __init__(self) -> None:
        self._skills_dir = _BUILTINS_DIR

    def list_skills(self) -> list[Skill]:
        """List all builtin skills.

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
                skills.append(parse_skill_file(skill_file, source_kind="builtin"))
            except (SkillValidationError, Exception) as exc:
                print(
                    f"Warning: skipping invalid builtin skill at {skill_file}: {exc}",
                    file=sys.stderr,
                )
        return skills

    def get_skill(self, name: str) -> Skill | None:
        """Look up a builtin skill by name.

        Args:
            name: The skill name (directory name).

        Returns:
            The parsed Skill, or None if not found.
        """
        skill_file = self._skills_dir / name / "SKILL.md"
        if not skill_file.is_file():
            return None
        try:
            return parse_skill_file(skill_file, source_kind="builtin")
        except SkillValidationError:
            return None
