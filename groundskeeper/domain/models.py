"""Core domain models for Groundskeeper."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class SkillSource:
    """Where a skill was loaded from."""

    kind: Literal["local", "builtin", "external"]
    path: Path


@dataclass(frozen=True)
class Skill:
    """A parsed skill with metadata and body."""

    name: str
    description: str
    body: str
    source: SkillSource
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self, arguments: str = "") -> str:
        """Render the skill body, substituting $ARGUMENTS."""
        return self.body.replace("$ARGUMENTS", arguments)


@dataclass(frozen=True)
class RunContext:
    """Context for running a skill."""

    skill: Skill
    arguments: str = ""
    working_directory: Path = field(default_factory=Path.cwd)
    skip_permissions: bool = False
    allowed_tools_override: list[str] | None = None


@dataclass
class RunResult:
    """Result of running a skill."""

    success: bool
    output: str = ""
    error: str = ""
    exit_code: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
