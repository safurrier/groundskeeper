"""Config loader for .groundskeeper/config.yml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from groundskeeper.domain.errors import ConfigError


@dataclass(frozen=True)
class Workflow:
    """A named workflow definition from config."""

    name: str
    triggers: dict[str, list[str]]
    skills: list[str]


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
        skills = wf_config.get("skills", [])
        if not isinstance(skills, list) or not skills:
            continue
        workflows.append(
            Workflow(
                name=str(name),
                triggers=triggers,
                skills=[str(s) for s in skills],
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
