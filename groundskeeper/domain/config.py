"""Config loader for .groundskeeper/config.yml."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from groundskeeper.domain.automation import canonical_github_repository
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
_AUTOMATION_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
DEFAULT_PI_TIMEOUT_SECONDS = 7200


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


@dataclass(frozen=True)
class GitHubIssuesSource:
    """GitHub-specific queue configuration confined to its adapter."""

    repository: str
    trusted_authors: tuple[str, ...]
    ready_label: str = "factory:ready"
    running_label: str = "factory:running"
    review_label: str = "factory:review"
    blocked_label: str = "factory:blocked"
    deferred_label: str = "factory:deferred"


@dataclass(frozen=True)
class AutomationCheckout:
    """Typed checkout preparation contract for an automation target."""

    mode: Literal["existing", "isolated-worktree", "managed-worktree"] = "existing"
    base_ref: str | None = None
    refresh: Literal["none", "fetch"] = "none"
    branch_prefix: str = "groundskeeper/task"


@dataclass(frozen=True)
class AutomationTarget:
    """Repository and donor checkout where the implementation worker starts."""

    repository: str
    repository_path: Path
    checkout: AutomationCheckout = field(default_factory=AutomationCheckout)


@dataclass(frozen=True)
class AutomationPolicy:
    """Provider-neutral factory safety policy."""

    concurrency: int = 1
    output: str = "draft-pr"
    merge: str = "never"
    link_source_issue: bool = True
    include_factory_session: bool = True
    include_pi_resume: bool = True


@dataclass(frozen=True)
class PiRunnerConfig:
    """Typed, deterministic configuration for the Pi execution adapter."""

    skill: str
    type: Literal["pi"] = "pi"
    approval: Literal["allow"] = "allow"
    session: Literal["deterministic"] = "deterministic"
    timeout_seconds: int = DEFAULT_PI_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Automation:
    """A declarative automation composed from typed provider configuration."""

    name: str
    source: GitHubIssuesSource
    target: AutomationTarget
    runner: PiRunnerConfig
    policy: AutomationPolicy = field(default_factory=AutomationPolicy)


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


def _parse_automation_checkout(raw: object, entry_path: str) -> AutomationCheckout:
    """Parse the optional strict target checkout policy."""
    checkout = _automation_mapping(raw, f"{entry_path}.target.checkout")
    _reject_unknown_automation_keys(
        checkout,
        {"mode", "base-ref", "refresh", "branch-prefix"},
        f"{entry_path}.target.checkout",
    )
    mode = checkout.get("mode")
    if mode not in {"existing", "isolated-worktree", "managed-worktree"}:
        raise ConfigError(
            f"Automation '{entry_path.removeprefix('automations.')}' "
            "target.checkout.mode must be existing, isolated-worktree, or managed-worktree"
        )
    if mode == "existing":
        if set(checkout) != {"mode"}:
            raise ConfigError(
                "target.checkout base-ref, refresh, and branch-prefix are only valid for "
                "mode: isolated-worktree or managed-worktree"
            )
        return AutomationCheckout()

    base_ref = checkout.get("base-ref")
    if (
        not isinstance(base_ref, str)
        or not base_ref
        or base_ref.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in base_ref)
    ):
        raise ConfigError(
            f"Automation '{entry_path.removeprefix('automations.')}' "
            "requires non-empty target.checkout.base-ref"
        )
    branch_prefix = checkout.get("branch-prefix", "groundskeeper/task")
    if not isinstance(branch_prefix, str) or not _is_valid_branch_prefix(branch_prefix):
        raise ConfigError(
            f"Automation '{entry_path.removeprefix('automations.')}' "
            "target.checkout.branch-prefix must be a valid Git branch prefix"
        )
    if mode == "managed-worktree" and not _is_stable_managed_base_ref(
        base_ref, branch_prefix
    ):
        raise ConfigError(
            "target.checkout.mode: managed-worktree requires a stable named "
            "base-ref or full commit SHA"
        )
    refresh = checkout.get("refresh", "none")
    if refresh not in {"none", "fetch"}:
        raise ConfigError(
            f"Automation '{entry_path.removeprefix('automations.')}' "
            "target.checkout.refresh must be none or fetch"
        )
    if refresh == "fetch":
        branch = base_ref.removeprefix("origin/")
        if (
            branch == base_ref
            or not branch
            or branch.startswith("-")
            or branch.endswith("/")
            or ".." in branch
            or "@{" in branch
            or any(char in "~^:?*[\\" for char in branch)
        ):
            raise ConfigError(
                "target.checkout.refresh: fetch requires base-ref under origin/"
            )
    return AutomationCheckout(
        mode=cast(Literal["isolated-worktree", "managed-worktree"], mode),
        base_ref=base_ref,
        refresh=cast(Literal["none", "fetch"], refresh),
        branch_prefix=branch_prefix,
    )


def _is_valid_branch_prefix(value: str) -> bool:
    """Accept a strict Git branch prefix that remains valid after a task suffix."""
    if (
        not value
        or value.startswith(("-", "/", "."))
        or value.endswith(("/", "."))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(
            char in " ~^:?*[\\" or ord(char) < 32 or ord(char) == 127 for char in value
        )
    ):
        return False
    return all(
        part and not part.startswith(".") and not part.endswith(".lock")
        for part in value.split("/")
    )


def _is_stable_managed_base_ref(
    value: str, branch_prefix: str = "groundskeeper/task"
) -> bool:
    """Reject checkout-relative revision expressions and task-owned refs."""
    if _COMMIT_SHA_RE.fullmatch(value):
        return True
    task_prefix = f"{branch_prefix}-"
    if value in {"HEAD", "@"} or value.startswith(task_prefix):
        return False
    if value.startswith(f"refs/heads/{task_prefix}"):
        return False
    if (
        value.startswith("/")
        or value.endswith(("/", "."))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(char in " ~^:?*[\\" for char in value)
    ):
        return False
    parts = value.split("/")
    return all(
        part and not part.startswith(".") and not part.endswith(".lock")
        for part in parts
    )


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


def _reject_unknown_automation_keys(
    values: Mapping[str, object],
    allowed: set[str],
    field_path: str,
    corrections: dict[str, str] | None = None,
) -> None:
    """Reject typos at the strict automation ingress boundary."""
    unknown = sorted(set(values) - allowed)
    if not unknown:
        return
    key = unknown[0]
    correction = (corrections or {}).get(key)
    suggestion = f" Use '{correction}'." if correction else ""
    raise ConfigError(f"{field_path} has unknown key '{key}'.{suggestion}")


def _automation_mapping(value: object, field_path: str) -> dict[str, object]:
    """Narrow one raw automation mapping without leaking an untyped config blob."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{field_path} must be a mapping with string keys")
    return cast(dict[str, object], value)


def get_automations(config: Mapping[str, object]) -> list[Automation]:
    """Parse strict automation definitions separately from legacy workflows."""
    _reject_unknown_automation_keys(
        config,
        {"version", "runner", "ci", "workflows", "automations"},
        "config",
        {
            "automation": "automations",
            "automtion": "automations",
            "automationz": "automations",
        },
    )
    raw_value = config.get("automations", {})
    if raw_value is None:
        return []
    raw = _automation_mapping(raw_value, "automations")
    automations: list[Automation] = []
    for name, value in raw.items():
        if not isinstance(name, str) or not _AUTOMATION_NAME_RE.match(name):
            raise ConfigError("Automation names must be kebab-case")
        entry_path = f"automations.{name}"
        value = _automation_mapping(value, entry_path)
        _reject_unknown_automation_keys(
            value, {"source", "target", "runner", "policy"}, entry_path
        )
        source = value.get("source")
        target = value.get("target")
        runner = value.get("runner")
        policy = value.get("policy", {})
        if not isinstance(source, dict):
            raise ConfigError(
                f"Automation '{name}' requires source.type: github-issues"
            )
        source = _automation_mapping(source, f"{entry_path}.source")
        if source.get("type") != "github-issues":
            raise ConfigError(
                f"Automation '{name}' requires source.type: github-issues"
            )
        _reject_unknown_automation_keys(
            source,
            {"type", "repository", "trusted-authors", "labels"},
            f"{entry_path}.source",
        )
        if not isinstance(target, dict):
            raise ConfigError(f"Automation '{name}' requires target")
        target = _automation_mapping(target, f"{entry_path}.target")
        _reject_unknown_automation_keys(
            target,
            {"repository", "repository-path", "checkout"},
            f"{entry_path}.target",
        )
        checkout = (
            _parse_automation_checkout(target["checkout"], entry_path)
            if "checkout" in target
            else AutomationCheckout()
        )
        if not isinstance(runner, dict):
            raise ConfigError(f"Automation '{name}' requires runner.type: pi")
        runner = _automation_mapping(runner, f"{entry_path}.runner")
        if runner.get("type") != "pi":
            raise ConfigError(f"Automation '{name}' requires runner.type: pi")
        _reject_unknown_automation_keys(
            runner,
            {"type", "skill", "approval", "session", "timeout-seconds"},
            f"{entry_path}.runner",
            {"timeout_seconds": "timeout-seconds"},
        )
        skill = runner.get("skill")
        if not isinstance(skill, str) or not skill:
            raise ConfigError(f"Automation '{name}' requires runner.skill")
        if runner.get("approval") != "allow":
            raise ConfigError(f"Automation '{name}' requires runner.approval: allow")
        if runner.get("session") != "deterministic":
            raise ConfigError(
                f"Automation '{name}' requires runner.session: deterministic"
            )
        timeout_seconds = runner.get("timeout-seconds", DEFAULT_PI_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ConfigError(
                f"Automation '{name}' requires positive runner.timeout-seconds"
            )
        policy = _automation_mapping(policy, f"{entry_path}.policy")
        _reject_unknown_automation_keys(
            policy,
            {
                "concurrency",
                "output",
                "merge",
                "link-source-issue",
                "include-factory-session",
                "include-pi-resume",
            },
            f"{entry_path}.policy",
            {"concurency": "concurrency"},
        )
        source_repository = source.get("repository")
        target_repository = target.get("repository")
        repository_path = target.get("repository-path")
        authors = source.get("trusted-authors")
        if not isinstance(source_repository, str) or not source_repository:
            raise ConfigError(f"Automation '{name}' requires source.repository")
        if not isinstance(target_repository, str) or not target_repository:
            raise ConfigError(f"Automation '{name}' requires target.repository")
        try:
            source_repository = canonical_github_repository(source_repository)
        except ValueError as error:
            raise ConfigError(
                f"Automation '{name}' source.repository must be canonical owner/repo"
            ) from error
        try:
            target_repository = canonical_github_repository(target_repository)
        except ValueError as error:
            raise ConfigError(
                f"Automation '{name}' target.repository must be canonical owner/repo"
            ) from error
        if not isinstance(repository_path, str) or not repository_path:
            raise ConfigError(f"Automation '{name}' requires target.repository-path")
        if (
            not isinstance(authors, list)
            or not authors
            or not all(isinstance(author, str) and author for author in authors)
        ):
            raise ConfigError(
                f"Automation '{name}' requires non-empty source.trusted-authors"
            )
        concurrency = policy.get("concurrency", 1)
        if concurrency != 1:
            raise ConfigError(
                f"Automation '{name}' currently requires policy.concurrency: 1"
            )
        if policy.get("merge", "never") != "never":
            raise ConfigError(f"Automation '{name}' requires policy.merge: never")
        if policy.get("output", "draft-pr") != "draft-pr":
            raise ConfigError(f"Automation '{name}' requires policy.output: draft-pr")
        public_policy: dict[str, bool] = {}
        for key in (
            "link-source-issue",
            "include-factory-session",
            "include-pi-resume",
        ):
            value = policy.get(key, True)
            if not isinstance(value, bool):
                raise ConfigError(f"Automation '{name}' requires boolean policy.{key}")
            public_policy[key] = value
        if not public_policy["link-source-issue"] and checkout.mode == "existing":
            raise ConfigError(
                f"Automation '{name}' requires target.checkout.mode "
                "isolated-worktree or managed-worktree when "
                "policy.link-source-issue is false"
            )
        labels = source.get("labels", {})
        labels = _automation_mapping(labels, f"{entry_path}.source.labels")
        label_defaults = {
            "ready": "factory:ready",
            "running": "factory:running",
            "deferred": "factory:deferred",
            "review": "factory:review",
            "blocked": "factory:blocked",
        }
        _reject_unknown_automation_keys(
            labels, set(label_defaults), f"{entry_path}.source.labels"
        )
        resolved_labels: dict[str, str] = {}
        for label_name, default in label_defaults.items():
            label = labels.get(label_name, default)
            if not isinstance(label, str) or not label.strip():
                raise ConfigError(
                    f"Automation '{name}' source.labels.{label_name} must be a non-empty string"
                )
            resolved_labels[label_name] = label
        if len(set(resolved_labels.values())) != len(resolved_labels):
            raise ConfigError(f"Automation '{name}' source.labels must be distinct")
        path = Path(repository_path).expanduser()
        if not path.is_absolute():
            raise ConfigError(
                f"Automation '{name}' target.repository-path must be absolute"
            )
        automations.append(
            Automation(
                name=str(name),
                source=GitHubIssuesSource(
                    repository=source_repository,
                    trusted_authors=tuple(cast(list[str], authors)),
                    ready_label=resolved_labels["ready"],
                    running_label=resolved_labels["running"],
                    review_label=resolved_labels["review"],
                    blocked_label=resolved_labels["blocked"],
                    deferred_label=resolved_labels["deferred"],
                ),
                target=AutomationTarget(
                    repository=target_repository,
                    repository_path=path.resolve(),
                    checkout=checkout,
                ),
                runner=PiRunnerConfig(skill=skill, timeout_seconds=timeout_seconds),
                policy=AutomationPolicy(
                    link_source_issue=public_policy["link-source-issue"],
                    include_factory_session=public_policy["include-factory-session"],
                    include_pi_resume=public_policy["include-pi-resume"],
                ),
            )
        )
    return automations


def get_automation(config: Mapping[str, object], name: str) -> Automation | None:
    """Look up one automation by name."""
    return next((item for item in get_automations(config) if item.name == name), None)
