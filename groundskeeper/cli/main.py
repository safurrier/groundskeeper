"""Groundskeeper CLI — gk command."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import click

from groundskeeper.adapters import list_all_skills, resolve_skill
from groundskeeper.adapters.automation_runner import (
    AutomationSkillRenderer,
    PiAutomationRunner,
)
from groundskeeper.adapters.builtin_store import BuiltinSkillStore
from groundskeeper.adapters.claude_code import ClaudeCodeRunner
from groundskeeper.adapters.dry_run import DryRunRunner
from groundskeeper.adapters.gh import GhClient
from groundskeeper.adapters.github_actions import GitHubActionsProvider
from groundskeeper.adapters.github_issues import GitHubIssuesTracker
from groundskeeper.adapters.local_store import LocalSkillStore
from groundskeeper.adapters.pi import PiClient
from groundskeeper.adapters.process import ProcessClient
from groundskeeper.adapters.target_checkout import (
    TargetCheckoutManager,
    prepare_target_checkout,
    target_checkout_common_dir,
    validate_target_checkout,
)
from groundskeeper.adapters.tick_lock import TickLock
from groundskeeper.automation import AutomationService, ReviewReconciliationService
from groundskeeper.domain.automation import TickResult
from groundskeeper.domain.config import (
    Automation,
    ParallelGroup,
    SkillRef,
    get_automation,
    get_automations,
    get_workflow,
    get_workflows,
    load_config,
)
from groundskeeper.domain.errors import (
    ConfigError,
    SkillNotFoundError,
    SkillValidationError,
)
from groundskeeper.domain.models import RunContext, RunResult, Skill
from groundskeeper.domain.parser import parse_skill_file
from groundskeeper.protocols import SkillStore
from groundskeeper.scheduler import (
    ScheduledAutomationService,
    ScheduledRunRequest,
    ScheduledTick,
)


def _get_stores(
    working_dir: Path | None = None,
    extra_skill_paths: tuple[str, ...] = (),
) -> list[LocalSkillStore | BuiltinSkillStore]:
    """Build the store list: local first, then external, then builtin."""
    wd = working_dir or Path.cwd()
    local_dir = wd / ".groundskeeper" / "skills"
    stores: list[LocalSkillStore | BuiltinSkillStore] = []
    if local_dir.is_dir():
        stores.append(LocalSkillStore(local_dir))
    for p in extra_skill_paths:
        stores.append(LocalSkillStore(Path(p), source_kind="external"))
    stores.append(BuiltinSkillStore())
    return stores


@click.group(
    epilog="""
\b
Examples:
  gk init                          Set up Groundskeeper in your repo
  gk list                          See available skills
  gk run code-review               Run a skill with Claude Code
  gk run code-review --dry-run     Preview the prompt without executing
  gk check                         Validate all skill definitions
""",
)
@click.version_option(package_name="groundskeeper")
@click.option(
    "--skill-path",
    multiple=True,
    type=click.Path(exists=True, file_okay=False),
    help="Additional skill directories to search.",
)
@click.pass_context
def cli(ctx: click.Context, skill_path: tuple[str, ...]) -> None:
    """gk - Script AI agents to run on your PRs.

    Groundskeeper manages reusable AI skills that run during CI or locally.
    Skills are prompt templates stored as SKILL.md files. Define them in
    .groundskeeper/skills/, then trigger them on pull requests via GitHub
    Actions or run them locally with Claude Code.
    """
    ctx.ensure_object(dict)
    ctx.obj["extra_skill_paths"] = skill_path


JSON_VERSION = 2
_SCHEDULE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


class AutomationCommandError(click.ClickException):
    """An automation preflight error with a stable scheduler-facing exit code."""

    exit_code = 2


@cli.group(
    epilog="""
\b
Examples:
  gk automation list
  gk automation show daily-dev
  gk automation validate daily-dev --json
  gk automation inspect daily-dev --json
  gk automation tick daily-dev --dry-run --json
""",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".groundskeeper/config.yml"),
    show_default=True,
    help="Automation configuration file.",
)
@click.pass_context
def automation(ctx: click.Context, config_path: Path) -> None:
    """Inspect, validate, and run declarative local automations."""
    ctx.ensure_object(dict)
    ctx.obj["automation_config"] = config_path


def _automation_config_path(ctx: click.Context) -> Path:
    """Read the automation config selected at the command group."""
    return ctx.obj["automation_config"]


def _automation_stores(
    config_path: Path, extra_skill_paths: tuple[str, ...]
) -> list[SkillStore]:
    """Resolve automation skills relative to their config file."""
    stores: list[SkillStore] = []
    local_dir = config_path.parent / "skills"
    if local_dir.is_dir():
        stores.append(LocalSkillStore(local_dir))
    for skill_path in extra_skill_paths:
        stores.append(LocalSkillStore(Path(skill_path), source_kind="external"))
    stores.append(BuiltinSkillStore())
    return stores


def _automation_summary(
    item: Automation, skill: Skill | None = None
) -> dict[str, object]:
    """Return a stable, compact automation description."""
    summary: dict[str, object] = {
        "name": item.name,
        "source": {
            "type": "github-issues",
            "repository": item.source.repository,
            "trusted_authors": list(item.source.trusted_authors),
            "automation_authors": list(item.source.automation_authors),
            "labels": {
                "ready": item.source.ready_label,
                "running": item.source.running_label,
                "deferred": item.source.deferred_label,
                "review": item.source.review_label,
                "blocked": item.source.blocked_label,
                "closed": item.source.closed_label,
            },
        },
        "target": _automation_target_summary(item),
        "runner": {
            "type": item.runner.type,
            "skill": item.runner.skill,
            "approval": item.runner.approval,
            "session": item.runner.session,
            "timeout_seconds": item.runner.timeout_seconds,
        },
        "policy": {
            "concurrency": item.policy.concurrency,
            "output": item.policy.output,
            "merge": item.policy.merge,
            "link_source_issue": item.policy.link_source_issue,
            "include_factory_session": item.policy.include_factory_session,
            "include_pi_resume": item.policy.include_pi_resume,
        },
    }
    if skill is not None:
        summary["skill"] = {
            "name": skill.name,
            "source_kind": skill.source.kind,
            "path": str(skill.source.path),
        }
    return summary


def _automation_target_summary(item: Automation) -> dict[str, object]:
    """Return the public typed target and checkout contract."""
    return {
        "repository": item.target.repository,
        "repository_path": str(item.target.repository_path),
        "checkout": {
            "mode": item.target.checkout.mode,
            "base_ref": item.target.checkout.base_ref,
            "refresh": item.target.checkout.refresh,
            "branch_prefix": item.target.checkout.branch_prefix,
        },
    }


def _automation_envelope(
    status: str, data: dict[str, object], exit_code: int = 0
) -> str:
    """Serialize the versioned JSON contract shared by automation commands."""
    return json.dumps(
        {
            "version": JSON_VERSION,
            "status": status,
            "data": data,
            "exit_code": exit_code,
        }
    )


def _automation_error(message: str, json_output: bool) -> None:
    """Print an actionable automation error and exit consistently."""
    if json_output:
        click.echo(_automation_envelope("error", {"error": message}, exit_code=2))
        raise SystemExit(2)
    raise AutomationCommandError(message)


def _load_automation(config_path: Path, name: str) -> Automation:
    """Load one strict automation definition or raise an actionable config error."""
    definition = get_automation(load_config(config_path), name)
    if definition is None:
        raise ConfigError(f"Automation not found: {name}. Run 'gk automation list'.")
    return definition


def _automation_state_root() -> Path:
    """Return the host-local state root, with a narrow test/host override."""
    configured_root = os.environ.get("GROUNDSKEEPER_STATE_HOME")
    if configured_root:
        return Path(configured_root).expanduser() / "groundskeeper"
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "groundskeeper"
    return Path.home() / ".local" / "state" / "groundskeeper"


def _automation_lock_path(definition: Automation) -> Path:
    """Return the source-queue lock, preserving the pre-split lock identity."""
    repository_identity = definition.source.repository.casefold()
    repository_key = hashlib.sha256(repository_identity.encode("utf-8")).hexdigest()[
        :16
    ]
    return _automation_state_root() / "locks" / f"repository-{repository_key}.lock"


def _automation_target_lock_path(definition: Automation, git_common_dir: Path) -> Path:
    """Return the lock protecting one target donor and its task worktrees."""
    target_identity = (
        f"{definition.target.repository.casefold()}\0{git_common_dir.resolve()}"
    )
    target_key = hashlib.sha256(target_identity.encode("utf-8")).hexdigest()[:16]
    return _automation_state_root() / "locks" / f"target-{target_key}.lock"


def _resolve_automation_skill(
    definition: Automation, config_path: Path, extra_skill_paths: tuple[str, ...]
) -> Skill:
    """Resolve the configured skill before a tick can claim external work."""
    skill = resolve_skill(
        definition.runner.skill, _automation_stores(config_path, extra_skill_paths)
    )
    if skill is None:
        raise ConfigError(
            f"Automation '{definition.name}' cannot resolve runner.skill "
            f"'{definition.runner.skill}'. Add it under {config_path.parent / 'skills'} "
            "or pass --skill-path."
        )
    return skill


def _build_automation_runner(
    definition: Automation,
    config_path: Path,
    extra_skill_paths: tuple[str, ...],
    process: ProcessClient,
    require_available: bool = True,
    refresh_target: bool = False,
    skill: Skill | None = None,
) -> PiAutomationRunner:
    """Construct the one supported typed automation runner after preflight checks."""
    repository_path = definition.target.repository_path
    if not repository_path.is_dir():
        raise ConfigError(
            f"Automation '{definition.name}' repository path does not exist: "
            f"{repository_path}"
        )
    resolved_skill = skill or _resolve_automation_skill(
        definition, config_path, extra_skill_paths
    )
    client = PiClient(process)
    if require_available and not client.is_available():
        raise ConfigError(
            "Pi CLI not found. Install Pi and retry 'gk automation validate'."
        )
    if require_available and not client.supports_automation():
        raise ConfigError(
            "Pi automation requires POSIX process-group isolation; "
            "run this automation on macOS or Linux."
        )
    if refresh_target:
        base_sha = prepare_target_checkout(
            process,
            repository_path,
            definition.target.repository,
            definition.target.checkout,
        )
    else:
        base_sha = validate_target_checkout(
            process,
            repository_path,
            definition.target.repository,
            definition.target.checkout,
        )
    checkout_manager = TargetCheckoutManager(
        process,
        repository_path,
        definition.target.repository,
        definition.target.checkout,
        base_sha,
        _automation_state_root(),
    )
    return PiAutomationRunner(
        client,
        checkout_manager,
        AutomationSkillRenderer(
            resolved_skill,
            definition.policy,
            definition.target.checkout,
        ),
        definition.runner,
    )


@automation.command(
    "list",
    epilog="""
\b
Examples:
  gk automation list --json
  gk automation --config /srv/widgets/.groundskeeper/config.yml list --json

Exit codes: 0 listed successfully, 2 invalid configuration.
""",
)
@click.option("--json", "json_output", is_flag=True, help="Emit versioned JSON output.")
@click.pass_context
def automation_list(ctx: click.Context, json_output: bool) -> None:
    """List configured automations without contacting external services.

    Use when choosing an automation name. Don't use for one automation's
    resolved settings; use `gk automation show NAME` instead.
    """
    try:
        items = get_automations(load_config(_automation_config_path(ctx)))
    except ConfigError as error:
        _automation_error(str(error), json_output)
        return
    summaries = [_automation_summary(item) for item in items]
    if json_output:
        click.echo(_automation_envelope("ok", {"automations": summaries}))
        return
    if not summaries:
        click.echo("No automations configured.")
        return
    for item in items:
        click.echo(f"  {item.name:<24} {item.source.repository} [{item.runner.skill}]")


@automation.command(
    "show",
    epilog="""
\b
Examples:
  gk automation show daily-dev --json
  gk automation --config /srv/widgets/.groundskeeper/config.yml show daily-dev

Exit codes: 0 found, 2 invalid configuration or unknown automation.
""",
)
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Emit versioned JSON output.")
@click.pass_context
def automation_show(ctx: click.Context, name: str, json_output: bool) -> None:
    """Show one automation's resolved source, skill, runner, and safety policy.

    Use when inspecting one automation. Don't use to check executable and path
    readiness; use `gk automation validate NAME` instead.
    """
    config_path = _automation_config_path(ctx)
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    try:
        definition = _load_automation(config_path, name)
        skill = _resolve_automation_skill(definition, config_path, extra)
    except ConfigError as error:
        _automation_error(str(error), json_output)
        return
    summary = _automation_summary(definition, skill)
    if json_output:
        click.echo(_automation_envelope("ok", {"automation": summary}))
        return
    click.echo(json.dumps(summary, indent=2))


@automation.command(
    "validate",
    epilog="""
\b
Examples:
  gk automation validate daily-dev --json
  gk automation --config /srv/widgets/.groundskeeper/config.yml validate

Exit codes: 0 valid, 2 invalid configuration, unresolved skill, missing Pi, or missing repository.
""",
)
@click.argument("name", required=False)
@click.option("--json", "json_output", is_flag=True, help="Emit versioned JSON output.")
@click.pass_context
def automation_validate(
    ctx: click.Context, name: str | None, json_output: bool
) -> None:
    """Validate config, skill resolution, Pi availability, and local repository paths.

    Use when changing configuration or before scheduling a tick. Don't use to
    select work; use `gk automation tick NAME --dry-run` instead. This command
    never claims issues, changes labels, or starts a worker.
    """
    config_path = _automation_config_path(ctx)
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    try:
        items = get_automations(load_config(config_path))
        if name:
            items = [item for item in items if item.name == name]
            if not items:
                raise ConfigError(
                    f"Automation not found: {name}. Run 'gk automation list'."
                )
        summaries: list[dict[str, object]] = []
        for item in items:
            skill = _resolve_automation_skill(item, config_path, extra)
            _build_automation_runner(
                item, config_path, extra, ProcessClient(), skill=skill
            )
            summaries.append(_automation_summary(item, skill))
    except (ConfigError, RuntimeError) as error:
        _automation_error(str(error), json_output)
        return
    data = {"automations": summaries}
    if json_output:
        click.echo(_automation_envelope("ok", data))
        return
    click.echo(f"Validated {len(items)} automation(s) without GitHub mutation.")


@automation.command(
    "inspect",
    epilog="""
\b
Examples:
  gk automation inspect daily-dev --json

Exit codes: 0 inspected, 2 invalid configuration or tracker failure.
""",
)
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Emit versioned JSON output.")
@click.pass_context
def automation_inspect(ctx: click.Context, name: str, json_output: bool) -> None:
    """Inspect task admission without labels, comments, or worker mutation."""
    config_path = _automation_config_path(ctx)
    try:
        definition = _load_automation(config_path, name)
        process = ProcessClient()
        tracker = GitHubIssuesTracker(
            GhClient(process, definition.target.repository_path),
            definition.source,
            definition.target.repository,
            definition.policy,
            definition.target.checkout,
        )
        tasks = [
            *tracker.list_running(),
            *tracker.list_deferred(),
            *tracker.list_ready(),
        ]
        records: list[dict[str, object]] = []
        for task in tasks:
            admission = tracker.admit(task)
            contract = admission.task.contract
            records.append(
                {
                    "id": str(task.source_issue.number),
                    "source": {
                        "repository": task.source_issue.repository,
                        "issue": task.source_issue.number,
                    },
                    "target": {"repository": task.target_repository},
                    "title": task.title,
                    "url": task.url,
                    "state": task.state.value,
                    "admitted": admission.eligible,
                    "admission_status": admission.state.value,
                    "reason": admission.reason,
                    "kind": contract.kind.value if contract else None,
                    "mode": contract.mode.value if contract and contract.mode else None,
                    "dependency_status": (
                        "resolved"
                        if admission.state.value == "admitted"
                        else (
                            admission.state.value.removeprefix("dependency-")
                            if admission.state.value.startswith("dependency-")
                            else None
                        )
                    ),
                }
            )
    except (ConfigError, RuntimeError) as error:
        _automation_error(str(error), json_output)
        return
    data = {
        "automation": name,
        "source": {"repository": definition.source.repository},
        "target": _automation_target_summary(definition),
        "tasks": records,
    }
    if json_output:
        click.echo(_automation_envelope("ok", data))
        return
    click.echo(json.dumps(data, indent=2))


def _automation_task_data(result: TickResult) -> dict[str, object] | None:
    """Return the public task identity for one tick result."""
    if result.task is None:
        return None
    return {
        "id": str(result.task.source_issue.number),
        "title": result.task.title,
        "url": result.task.url,
        "source": {
            "repository": result.task.source_issue.repository,
            "issue": result.task.source_issue.number,
        },
        "target": {"repository": result.task.target_repository},
        "state": result.task.state.value,
    }


def _automation_tick_exit_code(status: str) -> int:
    """Map the stable tick status contract to its process exit code."""
    return {
        "no-work": 0,
        "review": 0,
        "closed": 0,
        "deferred": 0,
        "would-dispatch": 0,
        "would-resume": 0,
        "would-block": 0,
        "would-close": 0,
        "would-wait": 0,
        "not-claimed": 4,
        "blocked": 5,
    }.get(status, 2)


def _execute_automation_tick(
    config_path: Path,
    name: str,
    extra_skill_paths: tuple[str, ...],
    *,
    dry_run: bool = False,
) -> tuple[Automation, TickResult]:
    """Execute one tick through the same lifecycle used by the CLI command."""
    definition = _load_automation(config_path, name)
    process = ProcessClient()
    if dry_run:
        runner = _build_automation_runner(
            definition,
            config_path,
            extra_skill_paths,
            process,
            require_available=False,
        )
        tracker = GitHubIssuesTracker(
            GhClient(process, definition.target.repository_path),
            definition.source,
            definition.target.repository,
            definition.policy,
            definition.target.checkout,
        )
        service = AutomationService(tracker, runner)
        return definition, service.tick(definition, dry_run=True)

    target_common_dir = target_checkout_common_dir(
        process, definition.target.repository_path
    )
    with ExitStack() as locks:
        locks.enter_context(TickLock(_automation_lock_path(definition)))
        locks.enter_context(
            TickLock(_automation_target_lock_path(definition, target_common_dir))
        )
        runner = _build_automation_runner(
            definition,
            config_path,
            extra_skill_paths,
            process,
            refresh_target=True,
        )
        tracker = GitHubIssuesTracker(
            GhClient(process, definition.target.repository_path),
            definition.source,
            definition.target.repository,
            definition.policy,
            definition.target.checkout,
        )
        service = AutomationService(tracker, runner)
        return definition, service.tick(definition, dry_run=False)


def _execute_automation_reconciliation(
    config_path: Path,
    name: str,
) -> tuple[Automation, TickResult | None]:
    """Reconcile terminal review state without preparing a worker workspace."""
    definition = _load_automation(config_path, name)
    process = ProcessClient()
    with TickLock(_automation_lock_path(definition)):
        tracker = GitHubIssuesTracker(
            GhClient(process, definition.target.repository_path),
            definition.source,
            definition.target.repository,
            definition.policy,
            definition.target.checkout,
        )
        result = ReviewReconciliationService(tracker).reconcile(definition.name)
    return definition, result


def _preflight_scheduled_automations(
    config_path: Path,
    requested_names: tuple[str, ...],
    extra_skill_paths: tuple[str, ...],
) -> list[str]:
    """Validate every scheduled automation before opening daily quota state."""
    definitions = get_automations(load_config(config_path))
    available = {definition.name: definition for definition in definitions}
    names = list(requested_names) if requested_names else list(available)
    if not names:
        raise ConfigError("No automations configured for the scheduled run.")
    if len(set(names)) != len(names):
        raise ConfigError("Scheduled automation names must be unique.")
    process = ProcessClient()
    for name in names:
        definition = available.get(name)
        if definition is None:
            raise ConfigError(
                f"Automation not found: {name}. Run 'gk automation list'."
            )
        skill = _resolve_automation_skill(definition, config_path, extra_skill_paths)
        _build_automation_runner(
            definition,
            config_path,
            extra_skill_paths,
            process,
            skill=skill,
        )
    return names


@automation.command(
    "tick",
    epilog="""
\b
Examples:
  gk automation tick daily-dev --dry-run --json
  gk automation tick daily-dev --json

Exit codes: 0 complete/no work/deferred, 2 configuration or runner error,
4 claim contention, 5 blocked worker.
""",
)
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Preview selection without mutation.")
@click.option("--json", "json_output", is_flag=True, help="Emit versioned JSON output.")
@click.pass_context
def automation_tick(
    ctx: click.Context, name: str, dry_run: bool, json_output: bool
) -> None:
    """Run one bounded reconciliation pass for NAME.

    Use when dispatching or reconciling work. Don't use to inspect configuration;
    use `gk automation show` or `validate` first. Dry-run omits issue bodies
    from output and never changes GitHub state or starts Pi.
    """
    config_path = _automation_config_path(ctx)
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    try:
        definition, result = _execute_automation_tick(
            config_path, name, extra, dry_run=dry_run
        )
    except (ConfigError, RuntimeError) as error:
        _automation_error(str(error), json_output)
        return

    data: dict[str, object] = {
        "automation": result.automation,
        "source": {"repository": definition.source.repository},
        "target": _automation_target_summary(definition),
        "task": _automation_task_data(result),
        "pull_request_url": result.pull_request_url,
        "detail": result.detail,
        "session_id": result.session_id,
        "session_name": result.session_name,
        "resume_command": result.resume_command,
        "operator_detail": result.operator_detail,
    }
    exit_code = _automation_tick_exit_code(result.status)
    if json_output:
        click.echo(_automation_envelope(result.status, data, exit_code))
    else:
        click.echo(
            f"{result.status}: {result.detail or result.pull_request_url or ''}".rstrip()
        )
    if exit_code:
        raise SystemExit(exit_code)


@automation.command(
    "run-scheduled",
    epilog="""
\b
Examples:
  gk automation --config .groundskeeper/config.yml run-scheduled \
    --schedule-id personal --source-repository-path ~/dots --daily-attempt-limit 5
  gk automation --config .groundskeeper/config.work.yml run-scheduled discord-dev \
    --schedule-id work --source-repository-path ~/dots --daily-attempt-limit 1

Exit codes: 0 completed/no work, 2 preflight, state, or automation failure.
""",
)
@click.argument("names", nargs=-1)
@click.option(
    "--schedule-id",
    required=True,
    help="Stable kebab-case identity for locks and daily quota state.",
)
@click.option(
    "--source-repository-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Git donor used only to resolve and materialize the execution snapshot.",
)
@click.option(
    "--source-ref",
    default="origin/main",
    show_default=True,
    help="Commit-ish containing the scheduled config and local skills.",
)
@click.option(
    "--source-refresh",
    type=click.Choice(["none", "fetch"]),
    default="fetch",
    show_default=True,
    help="Refresh origin/* before pinning the execution snapshot.",
)
@click.option(
    "--daily-attempt-limit",
    type=click.IntRange(min=1),
    required=True,
    help="Maximum worker attempts shared by this schedule each local day.",
)
@click.option(
    "--rotation",
    type=click.Choice(["daily", "fixed"]),
    default="daily",
    show_default=True,
    help="Rotate the first automation daily or preserve declaration order.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit versioned JSON output.")
@click.pass_context
def automation_run_scheduled(
    ctx: click.Context,
    names: tuple[str, ...],
    schedule_id: str,
    source_repository_path: Path,
    source_ref: str,
    source_refresh: str,
    daily_attempt_limit: int,
    rotation: str,
    json_output: bool,
) -> None:
    """Run automations from an immutable managed configuration snapshot.

    Use this as the stable command behind launchd, systemd, cron, or another
    host scheduler. The donor checkout may be dirty: Groundskeeper fetches and
    pins SOURCE_REF, executes config and local skills from a detached managed
    worktree, and never checks out, resets, stashes, or cleans the donor.
    """
    if not _SCHEDULE_ID_RE.fullmatch(schedule_id):
        _automation_error("Schedule id must be kebab-case.", json_output)
        return
    configured_path = _automation_config_path(ctx)
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    if extra:
        _automation_error(
            "run-scheduled does not accept --skill-path; scheduled skills must "
            "come from the pinned execution snapshot or builtins.",
            json_output,
        )
        return
    today = date.today()
    request = ScheduledRunRequest(
        schedule_id=schedule_id,
        source_repository_path=source_repository_path,
        source_ref=source_ref,
        source_refresh=source_refresh,
        config_path=configured_path,
        names=names,
        daily_attempt_limit=daily_attempt_limit,
        rotation=today.toordinal() if rotation == "daily" else 0,
        day=today,
    )
    service = ScheduledAutomationService(_automation_state_root(), ProcessClient())

    def preflight(config_path: Path, selected: tuple[str, ...]) -> list[str]:
        return _preflight_scheduled_automations(config_path, selected, ())

    def tick(config_path: Path, name: str) -> ScheduledTick:
        try:
            _definition, result = _execute_automation_tick(config_path, name, ())
        except (ConfigError, RuntimeError) as error:
            return ScheduledTick(name, "error", 2, None, str(error))
        return ScheduledTick(
            name,
            result.status,
            _automation_tick_exit_code(result.status),
            _automation_task_data(result),
            result.detail or None,
            result.pull_request_url,
            operator_detail=result.operator_detail,
        )

    def reconcile(config_path: Path, name: str) -> ScheduledTick | None:
        try:
            _definition, result = _execute_automation_reconciliation(config_path, name)
        except (ConfigError, RuntimeError) as error:
            return ScheduledTick(name, "error", 2, None, str(error))
        if result is None:
            return None
        return ScheduledTick(
            name,
            result.status,
            _automation_tick_exit_code(result.status),
            _automation_task_data(result),
            result.detail or None,
            result.pull_request_url,
            operator_detail=result.operator_detail,
        )

    try:
        scheduled = service.run(
            request, preflight=preflight, tick=tick, reconcile=reconcile
        )
    except (ConfigError, RuntimeError, ValueError) as error:
        _automation_error(str(error), json_output)
        return

    runs = scheduled.runs
    consumed = scheduled.consumed_today
    source = scheduled.execution_source
    run_data = [
        {
            "automation": run.automation,
            "status": run.status,
            "exit_code": run.exit_code,
            "consumed_attempt": run.consumed_attempt,
            "task": run.task,
            "pull_request_url": run.pull_request_url,
            "detail": run.detail,
            "operator_detail": run.operator_detail,
        }
        for run in runs
    ]
    failed = any(run.exit_code != 0 for run in runs)
    data: dict[str, object] = {
        "schedule": schedule_id,
        "execution_source": {
            "repository_path": str(source_repository_path.resolve()),
            "ref": source_ref,
            "commit": source.commit,
            "workspace": str(source.path),
        },
        "date": today.isoformat(),
        "limit": daily_attempt_limit,
        "consumed_today": consumed,
        "remaining_today": max(0, daily_attempt_limit - consumed),
        "runs": run_data,
    }
    if json_output:
        click.echo(
            _automation_envelope(
                "error" if failed else "ok",
                data,
                exit_code=2 if failed else 0,
            )
        )
    else:
        click.echo(
            f"{'error' if failed else 'ok'}: {len(runs)} tick(s), "
            f"{consumed}/{daily_attempt_limit} attempts consumed"
        )
    if failed:
        raise SystemExit(2)


@cli.command(
    epilog="""
\b
Creates:
  .groundskeeper/config.yml      Workflow configuration
  .groundskeeper/skills/         Directory for local skill definitions
\b
Examples:
  gk init                        Interactive setup
  gk init --non-interactive      Accept defaults without prompts
""",
)
@click.option(
    "--non-interactive", is_flag=True, help="Accept all defaults without prompting."
)
def init(non_interactive: bool) -> None:
    """Set up Groundskeeper in the current project.

    Creates the .groundskeeper/ directory with a default config and
    skills directory. Safe to re-run — existing config is preserved.
    Run 'gk generate' afterwards to create CI workflow files.
    """
    cwd = Path.cwd()
    gk_dir = cwd / ".groundskeeper"
    config_path = gk_dir / "config.yml"
    skills_dir = gk_dir / "skills"

    # Create .groundskeeper/ structure
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Copy default config
    if not config_path.exists():
        template_path = (
            Path(__file__).parent.parent / "builtins" / "templates" / "config.yml"
        )
        shutil.copy2(template_path, config_path)
        click.echo(f"Created {config_path.relative_to(cwd)}")
    else:
        click.echo(f"Config already exists: {config_path.relative_to(cwd)}")

    click.echo("Groundskeeper initialized.")
    click.echo("Run 'gk generate' to create CI workflow files.")


@cli.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """List available skills with their source and description.

    Shows skills from both local (.groundskeeper/skills/) and builtin
    sources. Local skills appear first and override builtins with the
    same name.
    """
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    stores = _get_stores(extra_skill_paths=extra)
    skills = list_all_skills(stores)  # type: ignore[arg-type]

    if not skills:
        click.echo("No skills found.")
        return

    for skill in skills:
        source_label = skill.source.kind
        click.echo(f"  {skill.name:<30} [{source_label}]  {skill.description}")


@cli.command()
@click.argument("name")
@click.pass_context
def show(ctx: click.Context, name: str) -> None:
    """Display a skill's metadata and full prompt body.

    Prints the skill's name, description, source, allowed tools, tags,
    argument hints, triggers, and the complete prompt template.
    """
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    stores = _get_stores(extra_skill_paths=extra)
    skill = resolve_skill(name, stores)  # type: ignore[arg-type]

    if skill is None:
        raise click.ClickException(f"Skill not found: {name}")

    click.echo(f"Name:        {skill.name}")
    click.echo(f"Description: {skill.description}")
    click.echo(f"Source:      {skill.source.kind} ({skill.source.path})")
    if skill.allowed_tools:
        click.echo(f"Tools:       {', '.join(skill.allowed_tools)}")
    if skill.tags:
        click.echo(f"Tags:        {', '.join(skill.tags)}")
    if skill.argument_hint:
        click.echo(f"Arguments:   {skill.argument_hint}")
    if skill.triggers:
        click.echo(f"Triggers:    {skill.triggers}")
    click.echo()
    click.echo(skill.body)


@cli.command(
    epilog="""
\b
Requires the claude CLI (https://claude.ai/code) unless --dry-run
is used.
\b
Examples:
  gk run code-review                   Run a skill with Claude Code
  gk run code-review --dry-run         Preview prompt without executing
  gk run greet --args "Alice"          Pass arguments to a skill template
""",
)
@click.argument("name")
@click.option(
    "--args",
    "arguments",
    default="",
    help="Text to substitute for $ARGUMENTS in the skill template.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the rendered prompt without executing.",
)
@click.option(
    "--yolo",
    is_flag=True,
    help="Skip all permission checks (passes --dangerously-skip-permissions to claude).",
)
@click.pass_context
def run(
    ctx: click.Context, name: str, arguments: str, dry_run: bool, yolo: bool
) -> None:
    """Execute a skill locally via Claude Code."""
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    stores = _get_stores(extra_skill_paths=extra)
    skill = resolve_skill(name, stores)  # type: ignore[arg-type]

    if skill is None:
        raise click.ClickException(f"Skill not found: {name}")

    context = RunContext(skill=skill, arguments=arguments, skip_permissions=yolo)

    if dry_run:
        runner = DryRunRunner()
    else:
        runner = ClaudeCodeRunner()
        if not runner.is_available():
            raise click.ClickException(
                "claude CLI not found. Install it from https://claude.ai/code"
            )

    result = runner.run(context)

    if result.output:
        click.echo(result.output)
    if result.error:
        click.echo(result.error, err=True)

    if not result.success:
        raise SystemExit(result.exit_code)


@cli.command(
    epilog="""
\b
Examples:
  gk check                  Validate all skills
  gk check code-review      Validate a single skill
""",
)
@click.argument("name", required=False)
@click.pass_context
def check(ctx: click.Context, name: str | None) -> None:
    """Validate skill definitions by re-parsing their SKILL.md files.

    Checks frontmatter schema, required fields, and template syntax.
    When NAME is omitted, validates every skill from all sources.
    Exits with code 1 if any skill fails validation.
    """
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    stores = _get_stores(extra_skill_paths=extra)

    if name:
        # Check a specific skill
        skill = resolve_skill(name, stores)  # type: ignore[arg-type]
        if skill is None:
            raise click.ClickException(f"Skill not found: {name}")
        _check_skill_by_name(name, extra_skill_paths=extra)
    else:
        # Check all skills
        skills = list_all_skills(stores)  # type: ignore[arg-type]
        if not skills:
            click.echo("No skills to check.")
            return
        errors = 0
        for skill in skills:
            try:
                _check_skill_at_path(skill.source.path / "SKILL.md", skill.name)
                click.echo(f"  {skill.name}: OK")
            except SkillValidationError as e:
                click.echo(f"  {skill.name}: FAIL — {e.message}", err=True)
                errors += 1
        if errors:
            raise click.ClickException(f"{errors} skill(s) failed validation")


def _check_skill_by_name(name: str, extra_skill_paths: tuple[str, ...] = ()) -> None:
    """Validate a skill by looking it up and re-parsing."""
    stores = _get_stores(extra_skill_paths=extra_skill_paths)
    skill = resolve_skill(name, stores)  # type: ignore[arg-type]
    if skill is None:
        raise click.ClickException(f"Skill not found: {name}")
    try:
        _check_skill_at_path(skill.source.path / "SKILL.md", name)
        click.echo(f"{name}: OK")
    except SkillValidationError as e:
        click.echo(f"{name}: FAIL — {e.message}", err=True)
        raise SystemExit(1) from e


def _check_skill_at_path(path: Path, name: str) -> None:
    """Re-parse a skill file to validate it."""
    parse_skill_file(path)


@cli.command(
    epilog="""
\b
Reads .groundskeeper/config.yml and writes workflow files to
.github/workflows/. Run 'gk init' first if no config exists.
""",
)
def generate() -> None:
    """Regenerate GitHub Actions workflow files from config.

    Re-reads .groundskeeper/config.yml and overwrites the workflow
    files in .github/workflows/. Use after editing the config to
    pick up trigger or skill chain changes.
    """
    cwd = Path.cwd()
    config_path = cwd / ".groundskeeper" / "config.yml"

    if not config_path.exists():
        raise click.ClickException("No config found. Run 'gk init' first.")

    _generate_workflows(cwd, config_path)
    click.echo("Workflows generated.")


def _generate_workflows(cwd: Path, config_path: Path) -> None:
    """Generate GitHub Actions workflows from config."""
    config = load_config(config_path)

    ci = config.get("ci")
    if ci is None:
        raise click.ClickException(
            "No CI provider configured. Add 'ci: github-actions' to "
            ".groundskeeper/config.yml, then re-run."
        )
    if ci != "github-actions":
        raise click.ClickException(f"Unsupported CI provider: {ci}")

    provider = GitHubActionsProvider()
    wf_dir = cwd / provider.workflow_directory
    wf_dir.mkdir(parents=True, exist_ok=True)

    # Write reusable workflow
    reusable_path = wf_dir / "gk_agent.yml"
    reusable_path.write_text(provider.generate_reusable_workflow())
    click.echo(f"  Generated {reusable_path.relative_to(cwd)}")

    # Generate caller workflows using domain model
    for workflow in get_workflows(config):
        all_refs = workflow.all_skill_refs
        if len(all_refs) == 1:
            caller_yaml = provider.generate_caller(
                skill_name=all_refs[0].name,
                triggers=workflow.triggers,
            )
            caller_path = wf_dir / f"gk_{workflow.name}.yml"
            caller_path.write_text(caller_yaml)
            click.echo(f"  Generated {caller_path.relative_to(cwd)}")
        else:
            # Build stage groups for CI (parallel within stage, sequential across)
            stages: list[list[str]] = []
            for step in workflow.steps:
                if isinstance(step, SkillRef):
                    stages.append([step.name])
                else:
                    stages.append([s.name for s in step.skills])
            chain_yaml = provider.generate_chain_workflow(
                workflow_name=workflow.name,
                triggers=workflow.triggers,
                stages=stages,
            )
            chain_path = wf_dir / f"gk_{workflow.name}.yml"
            chain_path.write_text(chain_yaml)
            click.echo(f"  Generated {chain_path.relative_to(cwd)}")


@cli.command("run-workflow")
@click.argument("name")
@click.option(
    "--args",
    "arguments",
    default="",
    help="Text to substitute for $ARGUMENTS in each skill template.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print each rendered prompt without executing.",
)
@click.option(
    "--yolo",
    is_flag=True,
    help="Skip all permission checks (passes --dangerously-skip-permissions to claude).",
)
@click.option(
    "--parallel",
    is_flag=True,
    help="Run parallel groups concurrently (even if they have write-capable tools).",
)
@click.pass_context
def run_workflow(
    ctx: click.Context,
    name: str,
    arguments: str,
    dry_run: bool,
    yolo: bool,
    parallel: bool,
) -> None:
    """Execute a workflow locally.

    Reads .groundskeeper/config.yml, looks up the named workflow, and
    runs steps sequentially. Parallel groups run concurrently when all
    skills are read-only, or when --parallel is passed explicitly.
    """
    cwd = Path.cwd()
    config_path = cwd / ".groundskeeper" / "config.yml"

    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e

    workflow = get_workflow(config, name)
    if workflow is None:
        raise click.ClickException(
            f"Workflow not found: {name}. "
            f"Check .groundskeeper/config.yml for available workflows."
        )

    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    stores = _get_stores(extra_skill_paths=extra)

    if dry_run:
        runner: DryRunRunner | ClaudeCodeRunner = DryRunRunner()
    else:
        runner = ClaudeCodeRunner()
        if not runner.is_available():
            raise click.ClickException(
                "claude CLI not found. Install it from https://claude.ai/code"
            )

    total = len(workflow.all_skill_refs)
    click.echo(f"Running workflow: {name} ({total} skills)")

    step_num = 0
    for step in workflow.steps:
        if isinstance(step, SkillRef):
            step_num += 1
            _run_single(
                step, step_num, total, name, workflow, stores, runner, arguments, yolo
            )
        else:
            # ParallelGroup — decide whether to actually parallelize
            should_parallel = not dry_run and (
                parallel or workflow.is_group_read_only(step)
            )
            names = ", ".join(s.name for s in step.skills)

            if should_parallel:
                click.echo(f"\n=== Parallel: [{names}] ===")
                step_num = _run_group_parallel(
                    step,
                    step_num,
                    total,
                    name,
                    workflow,
                    stores,
                    runner,
                    arguments,
                    yolo,
                )
            else:
                reason = "dry-run" if dry_run else "has write-capable tools"
                click.echo(f"\n=== [{names}] (sequential locally: {reason}) ===")
                for ref in step.skills:
                    step_num += 1
                    _run_single(
                        ref,
                        step_num,
                        total,
                        name,
                        workflow,
                        stores,
                        runner,
                        arguments,
                        yolo,
                    )

    click.echo(f"\nWorkflow '{name}' completed successfully.")


def _run_single(
    ref: SkillRef,
    step_num: int,
    total: int,
    workflow_name: str,
    workflow: object,
    stores: list[LocalSkillStore | BuiltinSkillStore],
    runner: DryRunRunner | ClaudeCodeRunner,
    arguments: str,
    yolo: bool,
) -> None:
    """Run a single skill ref and handle output/failure."""
    from groundskeeper.domain.config import Workflow

    skill = resolve_skill(ref.name, stores)  # type: ignore[arg-type]
    if skill is None:
        raise click.ClickException(
            f"Skill not found: {ref.name} "
            f"(step {step_num}/{total} in workflow '{workflow_name}')"
        )

    click.echo(f"\n--- [{step_num}/{total}] {ref.name} ---")
    assert isinstance(workflow, Workflow)
    context = RunContext(
        skill=skill,
        arguments=arguments,
        skip_permissions=yolo,
        allowed_tools_override=workflow.effective_tools(ref),
    )
    result = runner.run(context)

    if result.output:
        click.echo(result.output)
    if result.error:
        click.echo(result.error, err=True)

    if not result.success:
        click.echo(f"\nWorkflow failed at step {step_num}: {ref.name}")
        raise SystemExit(result.exit_code or 1)


def _run_group_parallel(
    group: ParallelGroup,
    step_num_start: int,
    total: int,
    workflow_name: str,
    workflow: object,
    stores: list[LocalSkillStore | BuiltinSkillStore],
    runner: DryRunRunner | ClaudeCodeRunner,
    arguments: str,
    yolo: bool,
) -> int:
    """Run all skills in a parallel group concurrently. Returns updated step_num."""
    from concurrent.futures import Future, ThreadPoolExecutor, as_completed

    from groundskeeper.domain.config import Workflow

    assert isinstance(workflow, Workflow)

    # Resolve all skills upfront
    contexts: list[tuple[SkillRef, RunContext]] = []
    for i, ref in enumerate(group.skills):
        skill = resolve_skill(ref.name, stores)  # type: ignore[arg-type]
        if skill is None:
            raise click.ClickException(
                f"Skill not found: {ref.name} "
                f"(step {step_num_start + i + 1}/{total} in workflow '{workflow_name}')"
            )
        context = RunContext(
            skill=skill,
            arguments=arguments,
            skip_permissions=yolo,
            allowed_tools_override=workflow.effective_tools(ref),
        )
        contexts.append((ref, context))

    # Run concurrently
    failures: list[tuple[str, RunResult]] = []
    step_num = step_num_start
    with ThreadPoolExecutor(max_workers=len(contexts)) as executor:
        future_to_ref: dict[Future[RunResult], SkillRef] = {}
        for ref, context in contexts:
            future_to_ref[executor.submit(runner.run, context)] = ref

        for future in as_completed(future_to_ref):
            ref = future_to_ref[future]
            result = future.result()
            step_num += 1

            click.echo(f"\n--- [{step_num}/{total}] {ref.name} ---")
            if result.output:
                click.echo(result.output)
            if result.error:
                click.echo(result.error, err=True)

            if not result.success:
                failures.append((ref.name, result))

    if failures:
        failed = ", ".join(n for n, _ in failures)
        click.echo(f"\nWorkflow failed at parallel group: {failed}")
        raise SystemExit(failures[0][1].exit_code or 1)

    return step_num


@cli.command()
@click.argument("name")
@click.option(
    "--args",
    "arguments",
    default="",
    help="Text to substitute for $ARGUMENTS in the skill template.",
)
@click.pass_context
def render(ctx: click.Context, name: str, arguments: str) -> None:
    """Render a skill's prompt template to stdout.

    Replaces $ARGUMENTS in the skill body with the provided --args
    value and prints the result. Used internally by CI workflows.
    """
    extra = ctx.obj.get("extra_skill_paths", ()) if ctx.obj else ()
    stores = _get_stores(extra_skill_paths=extra)
    skill = resolve_skill(name, stores)  # type: ignore[arg-type]

    if skill is None:
        raise SkillNotFoundError(name)

    click.echo(skill.render(arguments))
