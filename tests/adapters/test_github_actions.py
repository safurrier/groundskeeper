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


def test_generate_chain_workflow_two_skills() -> None:
    """Chain with 2 skills produces a single file with both jobs and correct needs."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="review-chain",
        triggers={"pull_request": ["ready_for_review"]},
        skill_names=["code-review", "context-files"],
    )
    parsed = yaml.safe_load(yaml_str)

    # Both jobs exist in the same file
    assert "code-review" in parsed["jobs"]
    assert "context-files" in parsed["jobs"]

    # First job has no needs
    assert "needs" not in parsed["jobs"]["code-review"]

    # Second job depends on the first
    assert parsed["jobs"]["context-files"]["needs"] == ["code-review"]

    # Both jobs use the reusable workflow
    assert parsed["jobs"]["code-review"]["uses"] == "./.github/workflows/gk_agent.yml"
    assert parsed["jobs"]["context-files"]["uses"] == "./.github/workflows/gk_agent.yml"


def test_generate_chain_workflow_three_skills() -> None:
    """Chain with 3 skills produces a linear needs chain."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="full-review",
        triggers={"pull_request": ["opened", "synchronize"]},
        skill_names=["lint", "code-review", "context-files"],
    )
    parsed = yaml.safe_load(yaml_str)

    assert "lint" in parsed["jobs"]
    assert "code-review" in parsed["jobs"]
    assert "context-files" in parsed["jobs"]

    # Linear chain: lint -> code-review -> context-files
    assert "needs" not in parsed["jobs"]["lint"]
    assert parsed["jobs"]["code-review"]["needs"] == ["lint"]
    assert parsed["jobs"]["context-files"]["needs"] == ["code-review"]


def test_generate_chain_workflow_single_skill() -> None:
    """Chain with 1 skill still works and has no needs."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="solo",
        triggers={"pull_request": ["opened"]},
        skill_names=["code-review"],
    )
    parsed = yaml.safe_load(yaml_str)

    assert "code-review" in parsed["jobs"]
    assert "needs" not in parsed["jobs"]["code-review"]
    assert parsed["jobs"]["code-review"]["uses"] == "./.github/workflows/gk_agent.yml"


def test_generated_yaml_is_valid() -> None:
    """All generated YAML should be parseable."""
    provider = GitHubActionsProvider()
    yaml.safe_load(provider.generate_reusable_workflow())
    yaml.safe_load(provider.generate_caller("test", {"push": ["main"]}))
    yaml.safe_load(
        provider.generate_chain_workflow("chain", {"push": ["main"]}, ["a", "b"])
    )
