"""Config loader for .groundskeeper/config.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from groundskeeper.domain.errors import ConfigError


@dataclass(frozen=True)
class WorkflowStep:
    """A single step in a workflow — a skill name + optional tool overrides."""

    name: str
    allowed_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Workflow:
    """A named workflow definition from config."""

    name: str
    triggers: dict[str, list[str]]
    steps: list[WorkflowStep]
    allowed_tools: list[str] = field(default_factory=list)

    @property
    def skills(self) -> list[str]:
        """Skill names in order (convenience for CI generation)."""
        return [s.name for s in self.steps]

    def effective_tools(self, step: WorkflowStep) -> list[str] | None:
        """Resolve allowed tools for a step using precedence cascade.

        Returns the effective tool list, or None if nothing is configured
        (meaning the skill's own frontmatter should be used as-is).

        Precedence (highest wins):
            1. Per-step config (step.allowed_tools)
            2. Workflow-level config (self.allowed_tools)
            3. None — fall through to skill frontmatter
        """
        if step.allowed_tools:
            return step.allowed_tools
        if self.allowed_tools:
            return self.allowed_tools
        return None


def _parse_steps(raw_skills: list[Any]) -> list[WorkflowStep]:
    """Parse the skills list, supporting both string and dict formats.

    Accepted formats:
        - "skill-name"
        - {"name": "skill-name", "allowed-tools": ["Read", "Write"]}
    """
    steps: list[WorkflowStep] = []
    for entry in raw_skills:
        if isinstance(entry, str):
            steps.append(WorkflowStep(name=entry))
        elif isinstance(entry, dict) and "name" in entry:
            tools = entry.get("allowed-tools", [])
            if not isinstance(tools, list):
                tools = []
            steps.append(
                WorkflowStep(
                    name=str(entry["name"]),
                    allowed_tools=[str(t) for t in tools],
                )
            )
    return steps


def load_config(path: Path) -> dict[str, Any]:
    """Load and validate config.yml.

    Args:
        path: Path to config.yml.

    Returns:
        Parsed config dict.

    Raises:
        ConfigError: If the file is missing or invalid.
    """
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(f"Config must be a YAML mapping: {path}")

    return data


def get_workflows(config: dict[str, Any]) -> list[Workflow]:
    """Extract all workflow definitions from config.

    Args:
        config: Parsed config dict.

    Returns:
        List of Workflow objects.
    """
    raw = config.get("workflows", {})
    if not isinstance(raw, dict):
        return []

    workflows: list[Workflow] = []
    for name, wf_config in raw.items():
        if not isinstance(wf_config, dict):
            continue
        triggers = wf_config.get("triggers", {})
        raw_skills = wf_config.get("skills", [])
        if not isinstance(raw_skills, list) or not raw_skills:
            continue

        wf_tools = wf_config.get("allowed-tools", [])
        if not isinstance(wf_tools, list):
            wf_tools = []

        steps = _parse_steps(raw_skills)
        if not steps:
            continue

        workflows.append(
            Workflow(
                name=str(name),
                triggers=triggers,
                steps=steps,
                allowed_tools=[str(t) for t in wf_tools],
            )
        )
    return workflows


def get_workflow(config: dict[str, Any], name: str) -> Workflow | None:
    """Look up a single workflow by name.

    Args:
        config: Parsed config dict.
        name: Workflow name to find.

    Returns:
        The Workflow, or None if not found.
    """
    for wf in get_workflows(config):
        if wf.name == name:
            return wf
    return None
