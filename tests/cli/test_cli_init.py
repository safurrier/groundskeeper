"""Tests for gk init command."""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from groundskeeper.cli.main import cli


def test_gk_init_creates_config_and_workflows(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert (fake_repo / ".groundskeeper" / "config.yml").exists()
    assert (fake_repo / ".github" / "workflows" / "gk_agent.yml").exists()
    # Validate generated YAML is parseable
    yaml.safe_load((fake_repo / ".github" / "workflows" / "gk_agent.yml").read_text())


def test_gk_init_creates_skills_dir(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert (fake_repo / ".groundskeeper" / "skills").is_dir()


def test_gk_init_does_not_overwrite_existing_config(fake_repo: Path):
    config_path = fake_repo / ".groundskeeper" / "config.yml"
    config_path.write_text("version: 99\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    assert "already exists" in result.output
    # Config should not be overwritten
    assert config_path.read_text() == "version: 99\n"


def test_gk_init_generates_caller_workflows(fake_repo: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--non-interactive"])
    assert result.exit_code == 0
    wf_dir = fake_repo / ".github" / "workflows"
    # Should have at least the reusable + one caller
    yml_files = list(wf_dir.glob("*.yml"))
    assert len(yml_files) >= 2
