"""Tests for the GitHub Actions CI provider."""

from __future__ import annotations

import yaml

from groundskeeper.adapters.github_actions import GitHubActionsProvider


def test_generate_reusable_workflow() -> None:
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_reusable_workflow()
    parsed = yaml.safe_load(yaml_str)
    assert parsed["on"]["workflow_call"]["inputs"]["skill"]["required"] is True
    assert "astral-sh/setup-uv" in yaml_str
    assert "uv tool install" in yaml_str
    assert "gk render" in yaml_str


def test_generate_caller_with_triggers() -> None:
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_caller(
        skill_name="codex-code-review",
        triggers={"pull_request": ["ready_for_review", "synchronize"]},
    )
    parsed = yaml.safe_load(yaml_str)
    assert parsed["on"]["pull_request"]["types"] == [
        "ready_for_review",
        "synchronize",
    ]
    assert "gk_agent.yml" in yaml_str


def test_generate_caller_with_depends_on() -> None:
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_caller(
        skill_name="context-files",
        triggers={"pull_request": ["ready_for_review"]},
        depends_on=["codex-code-review"],
    )
    parsed = yaml.safe_load(yaml_str)
    job = next(iter(parsed["jobs"].values()))
    assert "codex-code-review" in job.get("needs", [])


def test_generate_caller_without_depends_on() -> None:
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_caller(
        skill_name="codex-code-review",
        triggers={"pull_request": ["opened"]},
    )
    parsed = yaml.safe_load(yaml_str)
    job = next(iter(parsed["jobs"].values()))
    assert "needs" not in job


def test_workflow_directory() -> None:
    provider = GitHubActionsProvider()
    assert provider.workflow_directory == ".github/workflows"


def test_generated_yaml_is_valid() -> None:
    """All generated YAML should be parseable."""
    provider = GitHubActionsProvider()
    yaml.safe_load(provider.generate_reusable_workflow())
    yaml.safe_load(provider.generate_caller("test", {"push": ["main"]}))
