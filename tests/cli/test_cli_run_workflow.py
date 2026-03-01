"""Tests for gk run-workflow command."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from click.testing import CliRunner

from groundskeeper.cli.main import cli


def _write_config(repo: Path, workflows: dict[str, Any]) -> None:
    """Write a config.yml with the given workflows section."""
    config = {
        "version": 1,
        "runner": "claude-code",
        "ci": "github-actions",
        "workflows": workflows,
    }
    config_path = repo / ".groundskeeper" / "config.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config))


def test_run_workflow_dry_run_single_skill(
    fake_repo: Path,
    make_skill: Callable[..., Path],
) -> None:
    make_skill(
        "skill-a",
        {"name": "skill-a", "description": "First skill"},
        "Do the first thing",
    )
    _write_config(
        fake_repo,
        {
            "my-chain": {
                "triggers": {"pull_request": ["synchronize"]},
                "skills": ["skill-a"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "my-chain", "--dry-run"])
    assert result.exit_code == 0
    assert "skill-a" in result.output
    assert "Do the first thing" in result.output


def test_run_workflow_dry_run_chain(
    fake_repo: Path,
    make_skill: Callable[..., Path],
) -> None:
    make_skill(
        "skill-a",
        {"name": "skill-a", "description": "First"},
        "First prompt body",
    )
    make_skill(
        "skill-b",
        {"name": "skill-b", "description": "Second"},
        "Second prompt body",
    )
    _write_config(
        fake_repo,
        {
            "my-chain": {
                "triggers": {"pull_request": ["synchronize"]},
                "skills": ["skill-a", "skill-b"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "my-chain", "--dry-run"])
    assert result.exit_code == 0
    assert "skill-a" in result.output
    assert "First prompt body" in result.output
    assert "skill-b" in result.output
    assert "Second prompt body" in result.output


def test_run_workflow_dry_run_with_args(
    fake_repo: Path,
    make_skill: Callable[..., Path],
) -> None:
    make_skill(
        "greet",
        {"name": "greet", "description": "Greeter"},
        "Hello $ARGUMENTS",
    )
    _write_config(
        fake_repo,
        {
            "greeting": {
                "triggers": {},
                "skills": ["greet"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(
        cli, ["run-workflow", "greeting", "--dry-run", "--args", "world"]
    )
    assert result.exit_code == 0
    assert "Hello world" in result.output


def test_run_workflow_missing_workflow(fake_repo: Path) -> None:
    _write_config(
        fake_repo,
        {
            "other": {
                "triggers": {},
                "skills": ["something"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "nonexistent", "--dry-run"])
    assert result.exit_code != 0
    assert (
        "not found" in result.output.lower()
        or "not found" in (result.output + str(result.exception)).lower()
    )


def test_run_workflow_missing_config(fake_repo: Path) -> None:
    # Remove the config if it exists
    config_path = fake_repo / ".groundskeeper" / "config.yml"
    if config_path.exists():
        config_path.unlink()
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "anything", "--dry-run"])
    assert result.exit_code != 0


def test_run_workflow_skill_not_found(fake_repo: Path) -> None:
    _write_config(
        fake_repo,
        {
            "broken": {
                "triggers": {},
                "skills": ["nonexistent-skill"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "broken", "--dry-run"])
    assert result.exit_code != 0
    assert (
        "not found" in result.output.lower()
        or "not found" in (result.output + str(result.exception)).lower()
    )


def test_run_workflow_chain_stops_on_second_skill_not_found(
    fake_repo: Path,
    make_skill: Callable[..., Path],
) -> None:
    make_skill(
        "skill-a",
        {"name": "skill-a", "description": "First"},
        "First body",
    )
    _write_config(
        fake_repo,
        {
            "partial": {
                "triggers": {},
                "skills": ["skill-a", "missing-skill"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "partial", "--dry-run"])
    assert result.exit_code != 0


def test_run_workflow_dry_run_parallel_stage(
    fake_repo: Path,
    make_skill: Callable[..., Path],
) -> None:
    """Parallel stage in dry-run shows grouping label and all outputs."""
    make_skill("lint", {"name": "lint", "description": "Lint"}, "Lint body")
    make_skill("types", {"name": "types", "description": "Types"}, "Types body")
    make_skill("test", {"name": "test", "description": "Test"}, "Test body")
    _write_config(
        fake_repo,
        {
            "staged": {
                "triggers": {},
                "skills": [["lint", "types"], "test"],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "staged", "--dry-run"])
    assert result.exit_code == 0
    assert "Lint body" in result.output
    assert "Types body" in result.output
    assert "Test body" in result.output
    assert "sequential locally" in result.output.lower()
    assert "completed successfully" in result.output


def test_run_workflow_parallel_stage_missing_skill(
    fake_repo: Path,
    make_skill: Callable[..., Path],
) -> None:
    """Missing skill in parallel stage reports error."""
    make_skill("lint", {"name": "lint", "description": "Lint"}, "Lint body")
    _write_config(
        fake_repo,
        {
            "broken": {
                "triggers": {},
                "skills": [["lint", "missing-skill"]],
            }
        },
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["run-workflow", "broken", "--dry-run"])
    assert result.exit_code != 0
    assert (
        "not found" in result.output.lower()
        or "not found" in (result.output + str(result.exception)).lower()
    )
