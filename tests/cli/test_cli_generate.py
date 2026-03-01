"""Tests for gk generate command."""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from groundskeeper.cli.main import cli


def test_gk_generate_produces_valid_yaml(fake_repo: Path):
    config = {
        "version": 1,
        "runner": "claude-code",
        "ci": "github-actions",
        "workflows": {
            "review": {
                "triggers": {"pull_request": ["ready_for_review"]},
                "skills": ["codex-code-review"],
            }
        },
    }
    (fake_repo / ".groundskeeper" / "config.yml").write_text(yaml.dump(config))

    runner = CliRunner()
    result = runner.invoke(cli, ["generate"])
    assert result.exit_code == 0

    wf_dir = fake_repo / ".github" / "workflows"
    assert (wf_dir / "gk_agent.yml").exists()
    # At least one caller workflow
    yml_files = list(wf_dir.glob("gk_review*.yml"))
    assert len(yml_files) >= 1

    # All generated files should be valid YAML
    for f in wf_dir.glob("*.yml"):
        yaml.safe_load(f.read_text())


def test_gk_generate_no_config(fake_repo: Path):
    # Remove any config
    config_path = fake_repo / ".groundskeeper" / "config.yml"
    if config_path.exists():
        config_path.unlink()

    runner = CliRunner()
    result = runner.invoke(cli, ["generate"])
    assert result.exit_code != 0
    assert "no config" in result.output.lower() or "init" in result.output.lower()


def test_gk_generate_chained_skills(fake_repo: Path):
    config = {
        "version": 1,
        "runner": "claude-code",
        "ci": "github-actions",
        "workflows": {
            "review-chain": {
                "triggers": {"pull_request": ["ready_for_review"]},
                "skills": ["codex-code-review", "context-files"],
            }
        },
    }
    (fake_repo / ".groundskeeper" / "config.yml").write_text(yaml.dump(config))

    runner = CliRunner()
    result = runner.invoke(cli, ["generate"])
    assert result.exit_code == 0

    wf_dir = fake_repo / ".github" / "workflows"
    # Should generate a single chain workflow file (not separate files)
    chain_path = wf_dir / "gk_review-chain.yml"
    assert chain_path.exists()

    parsed = yaml.safe_load(chain_path.read_text())
    # Both skills should be jobs in the same workflow
    assert "codex-code-review" in parsed["jobs"]
    assert "context-files" in parsed["jobs"]
    # Second skill depends on the first
    assert parsed["jobs"]["context-files"]["needs"] == ["codex-code-review"]


def test_gk_generate_parallel_stage(fake_repo: Path):
    config = {
        "version": 1,
        "runner": "claude-code",
        "ci": "github-actions",
        "workflows": {
            "full-check": {
                "triggers": {"pull_request": ["ready_for_review"]},
                "skills": [["lint", "type-check"], "test"],
            }
        },
    }
    (fake_repo / ".groundskeeper" / "config.yml").write_text(yaml.dump(config))

    runner = CliRunner()
    result = runner.invoke(cli, ["generate"])
    assert result.exit_code == 0

    chain_path = fake_repo / ".github" / "workflows" / "gk_full-check.yml"
    assert chain_path.exists()

    parsed = yaml.safe_load(chain_path.read_text())
    # lint and type-check run in parallel (no needs)
    assert "needs" not in parsed["jobs"]["lint"]
    assert "needs" not in parsed["jobs"]["type-check"]
    # test depends on both
    assert set(parsed["jobs"]["test"]["needs"]) == {"lint", "type-check"}
