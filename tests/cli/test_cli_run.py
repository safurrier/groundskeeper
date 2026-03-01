"""Tests for gk run and gk render commands."""

from __future__ import annotations

from pathlib import Path

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
