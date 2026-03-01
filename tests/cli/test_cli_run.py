"""Tests for gk run and gk render commands."""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from groundskeeper.cli.main import cli


def test_gk_run_dry_run(fake_repo: Path, make_skill):
    make_skill(
        "my-skill",
        {"name": "my-skill", "description": "Test"},
        "Do the thing with $ARGUMENTS",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "my-skill", "--args", "hello", "--dry-run"])
    assert result.exit_code == 0
    assert "Do the thing with hello" in result.output


def test_gk_run_dry_run_builtin(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "codex-code-review", "--dry-run"])
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_gk_run_not_found(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "nonexistent", "--dry-run"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_gk_render_outputs_clean_prompt(fake_repo: Path, make_skill):
    make_skill(
        "test-skill",
        {"name": "test-skill", "description": "T"},
        "Prompt: $ARGUMENTS",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "test-skill", "--args", "foo"])
    assert result.exit_code == 0
    assert "Prompt: foo" in result.output
    # No frontmatter in output
    assert "---" not in result.output


def test_gk_render_not_found(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["render", "nonexistent"])
    assert result.exit_code != 0


def test_gk_run_external_skill_dry_run(fake_repo: Path, tmp_path: Path):
    """--skill-path allows running an external skill with --dry-run."""
    ext_dir = tmp_path / "ext-skills"
    skill_subdir = ext_dir / "my-ext"
    skill_subdir.mkdir(parents=True)
    fm = yaml.dump(
        {"name": "my-ext", "description": "External test"},
        default_flow_style=False,
    )
    (skill_subdir / "SKILL.md").write_text(f"---\n{fm}---\n\nExternal body here.")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--skill-path", str(ext_dir), "run", "my-ext", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "External body here." in result.output
