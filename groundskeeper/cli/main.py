"""Groundskeeper CLI — gk command."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from groundskeeper.adapters import list_all_skills, resolve_skill
from groundskeeper.adapters.builtin_store import BuiltinSkillStore
from groundskeeper.adapters.claude_code import ClaudeCodeRunner
from groundskeeper.adapters.dry_run import DryRunRunner
from groundskeeper.adapters.github_actions import GitHubActionsProvider
from groundskeeper.adapters.local_store import LocalSkillStore
from groundskeeper.domain.config import (
    ParallelGroup,
    SkillRef,
    get_workflow,
    get_workflows,
    load_config,
)
from groundskeeper.domain.errors import (
    ConfigError,
    SkillNotFoundError,
    SkillValidationError,
)
from groundskeeper.domain.models import RunContext, RunResult
from groundskeeper.domain.parser import parse_skill_file


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
