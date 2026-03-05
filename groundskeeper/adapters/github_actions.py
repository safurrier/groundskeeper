"""GitHub Actions CI provider for Groundskeeper."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from groundskeeper.domain.triggers import (
    EventTrigger,
    ManualTrigger,
    ScheduleTrigger,
    TriggerSpec,
)


def _triggers_to_actions_yaml(triggers: tuple[TriggerSpec, ...]) -> str:
    """Convert typed trigger specs to GitHub Actions 'on:' YAML."""
    result: dict = {}
    for trigger in triggers:
        match trigger:
            case EventTrigger(event=event, types=types):
                result[event.value] = {"types": list(types)}
            case ScheduleTrigger(cron=cron):
                result["schedule"] = [{"cron": cron}]
            case ManualTrigger():
                result["workflow_dispatch"] = {}
    return yaml.dump(result, default_flow_style=False).rstrip()


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
        triggers: tuple[TriggerSpec, ...],
        depends_on: list[str] | None = None,
    ) -> str:
        """Generate a caller workflow for a specific skill."""
        triggers_yaml = _triggers_to_actions_yaml(triggers)
        is_pr_trigger = any(
            isinstance(t, EventTrigger) and t.event.value == "pull_request"
            for t in triggers
        )
        template = self._env.get_template("caller.yml.j2")
        return template.render(
            name=f"GK {skill_name}",
            skill_name=skill_name,
            triggers_yaml=triggers_yaml,
            depends_on=json.dumps(depends_on) if depends_on else None,
            is_pr_trigger=is_pr_trigger,
        )

    def generate_chain_workflow(
        self,
        workflow_name: str,
        triggers: tuple[TriggerSpec, ...],
        stages: list[list[str]],
    ) -> str:
        """Generate a single workflow file with staged jobs.

        Skills within a stage run in parallel (no inter-dependencies).
        Each stage waits for all skills in the previous stage to complete.
        """
        triggers_yaml = _triggers_to_actions_yaml(triggers)
        is_pr_trigger = any(
            isinstance(t, EventTrigger) and t.event.value == "pull_request"
            for t in triggers
        )

        skills = []
        prev_stage_names: list[str] | None = None
        for stage in stages:
            for name in stage:
                skills.append(
                    {
                        "name": name,
                        "needs": json.dumps(prev_stage_names)
                        if prev_stage_names
                        else None,
                    }
                )
            prev_stage_names = list(stage)

        template = self._env.get_template("chain.yml.j2")
        return template.render(
            name=f"GK {workflow_name}",
            workflow_name=workflow_name,
            triggers_yaml=triggers_yaml,
            skills=skills,
            is_pr_trigger=is_pr_trigger,
        )

    @property
    def workflow_directory(self) -> str:
        return ".github/workflows"
