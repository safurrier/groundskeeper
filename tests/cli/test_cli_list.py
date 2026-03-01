"""Tests for gk list command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from groundskeeper.cli.main import cli


def test_gk_list_shows_builtins(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "codex-code-review" in result.output
    assert "context-files" in result.output
    assert "builtin" in result.output


def test_gk_list_shows_local_skills(fake_repo: Path, make_skill):
    make_skill(
        "my-skill",
        {"name": "my-skill", "description": "A local skill"},
        "Do something",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "my-skill" in result.output
    assert "local" in result.output


def test_gk_list_local_shadows_builtin(fake_repo: Path, make_skill):
    make_skill(
        "codex-code-review",
        {"name": "codex-code-review", "description": "Local override"},
        "Custom review",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    # Should show local, not builtin
    output_lines = result.output.strip().split("\n")
    review_lines = [line for line in output_lines if "codex-code-review" in line]
    assert len(review_lines) == 1
    assert "local" in review_lines[0]
