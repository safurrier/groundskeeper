"""Tests for gk init command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from groundskeeper.cli.main import cli


def test_gk_init_creates_config(fake_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert (fake_repo / ".groundskeeper" / "config.yml").exists()
    # init should NOT generate CI workflow files
    assert not (fake_repo / ".github" / "workflows").exists()


def test_gk_init_creates_skills_dir(fake_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert (fake_repo / ".groundskeeper" / "skills").is_dir()


def test_gk_init_does_not_overwrite_existing_config(fake_repo: Path) -> None:
    config_path = fake_repo / ".groundskeeper" / "config.yml"
    config_path.write_text("version: 99\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert "already exists" in result.output
    assert config_path.read_text() == "version: 99\n"


def test_gk_init_hints_about_generate(fake_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert "gk generate" in result.output
