"""Parser for SKILL.md files — extracts YAML frontmatter and markdown body."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from groundskeeper.domain.errors import SkillValidationError
from groundskeeper.domain.models import Skill, SkillSource

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into frontmatter dict and body string.

    Args:
        content: Raw file content.

    Returns:
        Tuple of (frontmatter_dict, body_string).

    Raises:
        SkillValidationError: If frontmatter cannot be parsed.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise SkillValidationError("<unknown>", "missing or invalid YAML frontmatter")

    raw_yaml, body = match.group(1), match.group(2)

    try:
        frontmatter = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        raise SkillValidationError("<unknown>", f"invalid YAML: {e}") from e

    if not isinstance(frontmatter, dict):
        raise SkillValidationError("<unknown>", "frontmatter must be a YAML mapping")

    return frontmatter, body


def _validate_frontmatter(frontmatter: dict[str, Any], path: str) -> list[str]:
    """Validate frontmatter fields. Returns list of warnings."""
    warnings: list[str] = []

    if "name" not in frontmatter:
        raise SkillValidationError(path, "name is required")

    if "description" not in frontmatter:
        raise SkillValidationError(path, "description is required")

    name = frontmatter["name"]
    if not isinstance(name, str) or not _KEBAB_CASE_RE.match(name):
        raise SkillValidationError(path, "name must be kebab-case")

    return warnings


def parse_skill_file(
    path: Path,
    source_kind: str = "local",
) -> Skill:
    """Parse a SKILL.md file into a Skill domain object.

    Args:
        path: Path to the SKILL.md file.
        source_kind: Either "local" or "builtin".

    Returns:
        Parsed Skill object.

    Raises:
        SkillValidationError: If the file is invalid.
    """
    content = path.read_text(encoding="utf-8")
    frontmatter, body = _extract_frontmatter(content)

    # Update error paths now that we know the file
    str_path = str(path)
    _validate_frontmatter(frontmatter, str_path)

    body = body.strip()
    if not body:
        raise SkillValidationError(str_path, "body is empty")

    # Warn if $ARGUMENTS is used but no argument-hint is set
    if "$ARGUMENTS" in body and "argument-hint" not in frontmatter:
        pass  # Future: collect warnings

    source = SkillSource(
        kind=source_kind,  # type: ignore[arg-type]
        path=path.parent,
    )

    return Skill(
        name=frontmatter["name"],
        description=frontmatter["description"],
        body=body,
        source=source,
        allowed_tools=frontmatter.get("allowed-tools", []),
        argument_hint=frontmatter.get("argument-hint", ""),
        tags=frontmatter.get("tags", []),
        triggers=frontmatter.get("triggers", {}),
        metadata={
            k: v
            for k, v in frontmatter.items()
            if k
            not in {
                "name",
                "description",
                "allowed-tools",
                "argument-hint",
                "tags",
                "triggers",
            }
        },
    )
