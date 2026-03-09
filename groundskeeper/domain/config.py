"""Config loader for .groundskeeper/config.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from groundskeeper.domain.errors import ConfigError
from groundskeeper.domain.triggers import (
    EventTrigger,
    GitHubEvent,
    ManualTrigger,
    ScheduleTrigger,
    TriggerSpec,
)

# Tools that can modify the working directory.
WRITE_TOOLS = frozenset({"Write", "Edit", "Bash", "NotebookEdit"})


@dataclass(frozen=True)
class SkillRef:
    """A reference to a skill within a workflow, with optional tool overrides."""

    name: str
    allowed_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParallelGroup:
    """A group of skills declared to run concurrently."""

    skills: list[SkillRef]


# A workflow step is either a single skill or a parallel group.
Step = SkillRef | ParallelGroup


@dataclass(frozen=True)
class Workflow:
    """A named workflow definition from config.

    Steps execute sequentially. A ParallelGroup step contains skills
    that may run concurrently (in CI always, locally only when safe).
    """

    name: str
    triggers: tuple[TriggerSpec, ...]
    steps: list[Step]
    allowed_tools: list[str] = field(default_factory=list)
    report_mode: str = "pr"

    @property
    def has_pr_trigger(self) -> bool:
        """Check if any trigger is a pull_request event."""
        return any(
            isinstance(t, EventTrigger) and t.event == GitHubEvent.PULL_REQUEST
            for t in self.triggers
        )

    @property
    def has_schedule(self) -> bool:
        """Check if any trigger is a cron schedule."""
        return any(isinstance(t, ScheduleTrigger) for t in self.triggers)

    @property
    def all_skill_names(self) -> list[str]:
        """All skill names flattened in step order."""
        names: list[str] = []
        for step in self.steps:
            if isinstance(step, SkillRef):
                names.append(step.name)
            else:
                names.extend(s.name for s in step.skills)
        return names

    @property
    def all_skill_refs(self) -> list[SkillRef]:
        """All SkillRefs flattened in step order."""
        refs: list[SkillRef] = []
        for step in self.steps:
            if isinstance(step, SkillRef):
                refs.append(step)
            else:
                refs.extend(step.skills)
        return refs

    def effective_tools(self, ref: SkillRef) -> list[str] | None:
        """Resolve allowed tools for a skill ref using precedence cascade.

        Returns the effective tool list, or None if nothing is configured
        (meaning the skill's own frontmatter should be used as-is).

        Precedence (highest wins):
            1. Per-step config (ref.allowed_tools)
            2. Workflow-level config (self.allowed_tools)
            3. None — fall through to skill frontmatter
        """
        if ref.allowed_tools:
            return ref.allowed_tools
        if self.allowed_tools:
            return self.allowed_tools
        return None

    def is_group_read_only(self, group: ParallelGroup) -> bool:
        """Check if all skills in a parallel group are read-only.

        A skill is read-only if its effective tools contain none of the
        write-capable tools (Write, Edit, Bash, NotebookEdit).
        Returns False if any skill has no tools configured (unknown).
        """
        for ref in group.skills:
            tools = self.effective_tools(ref)
            if tools is None:
                return False  # unknown tools = assume writes
            if set(tools) & WRITE_TOOLS:
                return False
        return True


def _parse_triggers(raw: dict[str, Any]) -> tuple[TriggerSpec, ...]:
    """Parse raw trigger config into typed trigger specs."""
    specs: list[TriggerSpec] = []
    for key, value in raw.items():
        if key == "schedule":
            specs.append(ScheduleTrigger(cron=str(value)))
        elif key == "workflow_dispatch":
            specs.append(ManualTrigger())
        else:
            event = GitHubEvent(key)
            types = tuple(str(t) for t in value) if isinstance(value, list) else ()
            specs.append(EventTrigger(event=event, types=types))
    # Auto-add ManualTrigger for scheduled workflows
    if any(isinstance(s, ScheduleTrigger) for s in specs):
        if not any(isinstance(s, ManualTrigger) for s in specs):
            specs.append(ManualTrigger())
    return tuple(specs)


def _parse_skill_ref(entry: Any) -> SkillRef | None:
    """Parse a single skill entry (string or dict) into a SkillRef."""
    if isinstance(entry, str):
        return SkillRef(name=entry)
    if isinstance(entry, dict) and "name" in entry:
        tools = entry.get("allowed-tools", [])
        if not isinstance(tools, list):
            tools = []
        return SkillRef(
            name=str(entry["name"]),
            allowed_tools=[str(t) for t in tools],
        )
    return None


def _parse_steps(raw_skills: list[Any]) -> list[Step]:
    """Parse the skills list into steps, supporting parallel groups.

    Accepted formats within the list:
        - "skill-name"                          -> SkillRef
        - {"name": "skill-name", ...}           -> SkillRef
        - ["skill-a", "skill-b"]                -> ParallelGroup
        - ["skill-a", {"name": "skill-b", ...}] -> ParallelGroup with overrides
    """
    steps: list[Step] = []
    for entry in raw_skills:
        if isinstance(entry, list):
            refs: list[SkillRef] = []
            for sub_entry in entry:
                ref = _parse_skill_ref(sub_entry)
                if ref is not None:
                    refs.append(ref)
            if refs:
                steps.append(ParallelGroup(skills=refs))
        else:
            ref = _parse_skill_ref(entry)
            if ref is not None:
                steps.append(ref)
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
    """Extract all workflow definitions from config."""
    raw = config.get("workflows", {})
    if not isinstance(raw, dict):
        return []

    workflows: list[Workflow] = []
    for name, wf_config in raw.items():
        if not isinstance(wf_config, dict):
            continue
        raw_triggers = wf_config.get("triggers", {})
        if not isinstance(raw_triggers, dict):
            raw_triggers = {}
        triggers = _parse_triggers(raw_triggers)

        raw_skills = wf_config.get("skills", [])
        if not isinstance(raw_skills, list) or not raw_skills:
            continue

        wf_tools = wf_config.get("allowed-tools", [])
        if not isinstance(wf_tools, list):
            wf_tools = []

        report_mode = wf_config.get("report-mode", "pr")
        if report_mode not in ("pr", "issue"):
            report_mode = "pr"

        steps = _parse_steps(raw_skills)
        if not steps:
            continue

        workflows.append(
            Workflow(
                name=str(name),
                triggers=triggers,
                steps=steps,
                allowed_tools=[str(t) for t in wf_tools],
                report_mode=str(report_mode),
            )
        )
    return workflows


def get_workflow(config: dict[str, Any], name: str) -> Workflow | None:
    """Look up a single workflow by name."""
    for wf in get_workflows(config):
        if wf.name == name:
            return wf
    return None
