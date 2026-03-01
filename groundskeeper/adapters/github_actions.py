"""GitHub Actions CI provider for Groundskeeper."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader


class GitHubActionsProvider:
    """Generates GitHub Actions workflow files from Jinja2 templates."""

    def __init__(self) -> None:
        self._template_dir = (
            Path(__file__).parent.parent / "builtins" / "templates" / "github_actions"
        )
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            keep_trailing_newline=True,
        )

    def generate_reusable_workflow(self) -> str:
        """Generate the reusable gk_agent.yml workflow."""
        return (self._template_dir / "reusable.yml").read_text()

    def generate_caller(
        self,
        skill_name: str,
        triggers: dict[str, list[str]],
        depends_on: list[str] | None = None,
    ) -> str:
        """Generate a caller workflow for a specific skill."""
        triggers_dict = {k: {"types": v} for k, v in triggers.items()}
        triggers_yaml = yaml.dump(triggers_dict, default_flow_style=False).rstrip()
        template = self._env.get_template("caller.yml.j2")
        return template.render(
            name=f"GK {skill_name}",
            skill_name=skill_name,
            triggers_yaml=triggers_yaml,
            depends_on=json.dumps(depends_on) if depends_on else None,
        )

    @property
    def workflow_directory(self) -> str:
        return ".github/workflows"
