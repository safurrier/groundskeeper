"""Groundskeeper CLI — gk command."""

from __future__ import annotations

import shutil
from pathlib import Path

import click
import yaml

from groundskeeper.adapters import list_all_skills, resolve_skill
from groundskeeper.adapters.builtin_store import BuiltinSkillStore
from groundskeeper.adapters.claude_code import ClaudeCodeRunner
from groundskeeper.adapters.dry_run import DryRunRunner
from groundskeeper.adapters.github_actions import GitHubActionsProvider
from groundskeeper.adapters.local_store import LocalSkillStore
from groundskeeper.domain.errors import SkillNotFoundError, SkillValidationError
from groundskeeper.domain.models import RunContext
from groundskeeper.domain.parser import parse_skill_file


def _get_stores(
    working_dir: Path | None = None,
) -> list[LocalSkillStore | BuiltinSkillStore]:
    """Build the store list: local first, then builtin."""
    wd = working_dir or Path.cwd()
    local_dir = wd / ".groundskeeper" / "skills"
    stores: list[LocalSkillStore | BuiltinSkillStore] = []
    if local_dir.is_dir():
        stores.append(LocalSkillStore(local_dir))
    stores.append(BuiltinSkillStore())
    return stores


@click.group()
@click.version_option(package_name="groundskeeper")
def cli() -> None:
    """Groundskeeper — Script AI agents to run on your PRs."""


@cli.command()
@click.option("--non-interactive", is_flag=True, help="Skip interactive prompts.")
def init(non_interactive: bool) -> None:
    """Initialize Groundskeeper in the current project."""
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

    # Generate workflows
    _generate_workflows(cwd, config_path)

    click.echo("Groundskeeper initialized.")


@cli.command("list")
def list_cmd() -> None:
    """Show available skills with source provenance."""
    stores = _get_stores()
    skills = list_all_skills(stores)  # type: ignore[arg-type]

    if not skills:
        click.echo("No skills found.")
        return

    for skill in skills:
        source_label = skill.source.kind
        click.echo(f"  {skill.name:<30} [{source_label}]  {skill.description}")


@cli.command()
@click.argument("name")
def show(name: str) -> None:
    """Display a skill's frontmatter and body."""
    stores = _get_stores()
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


@cli.command()
@click.argument("name")
@click.option("--args", "arguments", default="", help="Arguments to pass to the skill.")
@click.option("--dry-run", is_flag=True, help="Show rendered prompt without executing.")
def run(name: str, arguments: str, dry_run: bool) -> None:
    """Execute a skill locally via the configured runner."""
    stores = _get_stores()
    skill = resolve_skill(name, stores)  # type: ignore[arg-type]

    if skill is None:
        raise click.ClickException(f"Skill not found: {name}")

    context = RunContext(skill=skill, arguments=arguments)

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


@cli.command()
@click.argument("name", required=False)
def check(name: str | None) -> None:
    """Validate skill frontmatter, schema, and template syntax."""
    stores = _get_stores()

    if name:
        # Check a specific skill
        skill = resolve_skill(name, stores)  # type: ignore[arg-type]
        if skill is None:
            raise click.ClickException(f"Skill not found: {name}")
        _check_skill_by_name(name)
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


def _check_skill_by_name(name: str) -> None:
    """Validate a skill by looking it up and re-parsing."""
    stores = _get_stores()
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


@cli.command()
def generate() -> None:
    """Regenerate CI workflow files from current config."""
    cwd = Path.cwd()
    config_path = cwd / ".groundskeeper" / "config.yml"

    if not config_path.exists():
        raise click.ClickException("No config found. Run 'gk init' first.")

    _generate_workflows(cwd, config_path)
    click.echo("Workflows generated.")


def _generate_workflows(cwd: Path, config_path: Path) -> None:
    """Generate GitHub Actions workflows from config."""
    config = yaml.safe_load(config_path.read_text())

    ci = config.get("ci", "github-actions")
    if ci != "github-actions":
        raise click.ClickException(f"Unsupported CI provider: {ci}")

    provider = GitHubActionsProvider()
    wf_dir = cwd / provider.workflow_directory
    wf_dir.mkdir(parents=True, exist_ok=True)

    # Write reusable workflow
    reusable_path = wf_dir / "gk_agent.yml"
    reusable_path.write_text(provider.generate_reusable_workflow())
    click.echo(f"  Generated {reusable_path.relative_to(cwd)}")

    # Generate caller workflows
    workflows = config.get("workflows", {})
    for wf_name, wf_config in workflows.items():
        triggers = wf_config.get("triggers", {})
        skills = wf_config.get("skills", [])

        if len(skills) == 1:
            # Single skill — simple caller
            caller_yaml = provider.generate_caller(
                skill_name=skills[0],
                triggers=triggers,
            )
            caller_path = wf_dir / f"gk_{wf_name}.yml"
            caller_path.write_text(caller_yaml)
            click.echo(f"  Generated {caller_path.relative_to(cwd)}")
        else:
            # Multi-skill chain — generate one workflow with depends_on
            caller_path = wf_dir / f"gk_{wf_name}.yml"
            # For chained skills, we generate separate jobs but in one file
            # For v0.1, generate separate caller files with needs:
            prev_skill: str | None = None
            for skill_name in skills:
                depends = [prev_skill] if prev_skill else None
                caller_yaml = provider.generate_caller(
                    skill_name=skill_name,
                    triggers=triggers,
                    depends_on=depends,
                )
                skill_path = wf_dir / f"gk_{wf_name}_{skill_name}.yml"
                skill_path.write_text(caller_yaml)
                click.echo(f"  Generated {skill_path.relative_to(cwd)}")
                prev_skill = skill_name


@cli.command()
@click.argument("name")
@click.option("--args", "arguments", default="", help="Arguments to substitute.")
def render(name: str, arguments: str) -> None:
    """Output rendered prompt to stdout (used by CI internally)."""
    stores = _get_stores()
    skill = resolve_skill(name, stores)  # type: ignore[arg-type]

    if skill is None:
        raise SkillNotFoundError(name)

    click.echo(skill.render(arguments))
