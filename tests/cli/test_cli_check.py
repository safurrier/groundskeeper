"""Tests for gk check command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from groundskeeper.cli.main import cli


def test_gk_check_valid_skill(fake_repo: Path, make_skill):
    make_skill(
        "good",
        {"name": "good", "description": "Valid"},
        "Some body",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "good"])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_gk_check_invalid_skill(fake_repo: Path):
    p = fake_repo / ".groundskeeper" / "skills" / "bad"
    p.mkdir(parents=True, exist_ok=True)
    (p / "SKILL.md").write_text("---\ndescription: no name\n---\n\nbody")
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "bad"])
    assert result.exit_code != 0


def test_gk_check_all_builtins(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["check"])
    assert result.exit_code == 0
    assert "codex-code-review" in result.output
    assert "context-files" in result.output


def test_gk_check_not_found(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "nonexistent"])
    assert result.exit_code != 0
