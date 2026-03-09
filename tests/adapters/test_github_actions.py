"""Tests for the GitHub Actions CI provider."""

from __future__ import annotations

import yaml

from groundskeeper.adapters.github_actions import GitHubActionsProvider
from groundskeeper.domain.triggers import (
    EventTrigger,
    GitHubEvent,
    ManualTrigger,
    ScheduleTrigger,
)


def _pr_triggers(*types: str) -> tuple[EventTrigger, ...]:
    """Helper to create PR event triggers."""
    return (EventTrigger(event=GitHubEvent.PULL_REQUEST, types=tuple(types)),)


def _push_triggers(*types: str) -> tuple[EventTrigger, ...]:
    """Helper to create push event triggers."""
    return (EventTrigger(event=GitHubEvent.PUSH, types=tuple(types)),)


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
        triggers=_pr_triggers("ready_for_review", "synchronize"),
    )
    parsed = yaml.safe_load(yaml_str)
    assert parsed["on"]["pull_request"]["types"] == [
        "ready_for_review",
        "synchronize",
    ]
    assert "gk_agent.yml" in yaml_str
    # PR triggers should have draft check
    assert "draft" in yaml_str


def test_generate_caller_with_depends_on() -> None:
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_caller(
        skill_name="context-files",
        triggers=_pr_triggers("ready_for_review"),
        depends_on=["codex-code-review"],
    )
    parsed = yaml.safe_load(yaml_str)
    job = next(iter(parsed["jobs"].values()))
    assert "codex-code-review" in job.get("needs", [])


def test_generate_caller_without_depends_on() -> None:
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_caller(
        skill_name="codex-code-review",
        triggers=_pr_triggers("opened"),
    )
    parsed = yaml.safe_load(yaml_str)
    job = next(iter(parsed["jobs"].values()))
    assert "needs" not in job


def test_workflow_directory() -> None:
    provider = GitHubActionsProvider()
    assert provider.workflow_directory == ".github/workflows"


def test_generate_chain_workflow_two_skills_sequential() -> None:
    """Sequential chain with 2 skills produces correct needs."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="review-chain",
        triggers=_pr_triggers("ready_for_review"),
        stages=[["code-review"], ["context-files"]],
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


def test_generate_chain_workflow_three_skills_sequential() -> None:
    """Sequential chain with 3 skills produces a linear needs chain."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="full-review",
        triggers=_pr_triggers("opened", "synchronize"),
        stages=[["lint"], ["code-review"], ["context-files"]],
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
        triggers=_pr_triggers("opened"),
        stages=[["code-review"]],
    )
    parsed = yaml.safe_load(yaml_str)

    assert "code-review" in parsed["jobs"]
    assert "needs" not in parsed["jobs"]["code-review"]
    assert parsed["jobs"]["code-review"]["uses"] == "./.github/workflows/gk_agent.yml"


def test_generate_chain_workflow_parallel_stage() -> None:
    """Skills in a parallel stage share the same needs."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="parallel-check",
        triggers=_pr_triggers("opened"),
        stages=[["lint", "type-check"], ["test"]],
    )
    parsed = yaml.safe_load(yaml_str)

    # Parallel stage: lint and type-check have no needs
    assert "needs" not in parsed["jobs"]["lint"]
    assert "needs" not in parsed["jobs"]["type-check"]

    # test depends on both lint and type-check
    assert set(parsed["jobs"]["test"]["needs"]) == {"lint", "type-check"}


def test_generate_chain_workflow_multi_parallel_stages() -> None:
    """Multiple parallel stages chain correctly."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="full",
        triggers=_pr_triggers("opened"),
        stages=[["lint", "types"], ["unit-test", "integration-test"], ["deploy"]],
    )
    parsed = yaml.safe_load(yaml_str)

    # Stage 1: no needs
    assert "needs" not in parsed["jobs"]["lint"]
    assert "needs" not in parsed["jobs"]["types"]

    # Stage 2: depends on stage 1
    assert set(parsed["jobs"]["unit-test"]["needs"]) == {"lint", "types"}
    assert set(parsed["jobs"]["integration-test"]["needs"]) == {"lint", "types"}

    # Stage 3: depends on stage 2
    assert set(parsed["jobs"]["deploy"]["needs"]) == {
        "unit-test",
        "integration-test",
    }


def test_generate_chain_workflow_single_parallel_stage() -> None:
    """A single parallel stage means all skills have no needs."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_chain_workflow(
        workflow_name="all-parallel",
        triggers=_pr_triggers("opened"),
        stages=[["a", "b", "c"]],
    )
    parsed = yaml.safe_load(yaml_str)
    for job_name in ["a", "b", "c"]:
        assert "needs" not in parsed["jobs"][job_name]


def test_generated_yaml_is_valid() -> None:
    """All generated YAML should be parseable."""
    provider = GitHubActionsProvider()
    yaml.safe_load(provider.generate_reusable_workflow())
    yaml.safe_load(provider.generate_caller("test", _push_triggers("main")))
    yaml.safe_load(
        provider.generate_chain_workflow(
            "chain", _push_triggers("main"), [["a"], ["b"]]
        )
    )


# --- Schedule trigger tests ---


def test_generate_caller_with_schedule_trigger() -> None:
    """Schedule trigger generates cron syntax + workflow_dispatch, no draft check."""
    provider = GitHubActionsProvider()
    triggers = (ScheduleTrigger(cron="0 15 * * 1"), ManualTrigger())
    yaml_str = provider.generate_caller(
        skill_name="vault-health-check",
        triggers=triggers,
    )
    parsed = yaml.safe_load(yaml_str)

    # Schedule trigger present with cron
    assert parsed["on"]["schedule"] == [{"cron": "0 15 * * 1"}]
    # workflow_dispatch present
    assert "workflow_dispatch" in parsed["on"]
    # No draft check (not a PR trigger)
    assert "draft" not in yaml_str
    # No PR-specific concurrency group
    assert "pull_request.number" not in yaml_str


def test_generate_chain_with_schedule_trigger() -> None:
    """Schedule trigger works for chain workflows too."""
    provider = GitHubActionsProvider()
    triggers = (ScheduleTrigger(cron="0 8 * * *"),)
    yaml_str = provider.generate_chain_workflow(
        workflow_name="daily-check",
        triggers=triggers,
        stages=[["lint"], ["test"]],
    )
    parsed = yaml.safe_load(yaml_str)

    assert parsed["on"]["schedule"] == [{"cron": "0 8 * * *"}]
    assert "draft" not in yaml_str


def test_generate_caller_with_mixed_triggers() -> None:
    """Schedule + PR triggers together."""
    provider = GitHubActionsProvider()
    triggers = (
        EventTrigger(event=GitHubEvent.PULL_REQUEST, types=("synchronize",)),
        ScheduleTrigger(cron="0 8 * * 1"),
        ManualTrigger(),
    )
    yaml_str = provider.generate_caller(
        skill_name="mixed-skill",
        triggers=triggers,
    )
    parsed = yaml.safe_load(yaml_str)

    assert "pull_request" in parsed["on"]
    assert "schedule" in parsed["on"]
    assert "workflow_dispatch" in parsed["on"]
    # PR trigger present = draft check enabled
    assert "draft" in yaml_str


def test_generate_caller_non_pr_event_no_draft_check() -> None:
    """Push triggers should not have draft check."""
    provider = GitHubActionsProvider()
    yaml_str = provider.generate_caller(
        skill_name="deploy",
        triggers=_push_triggers("main"),
    )
    assert "draft" not in yaml_str
    assert "pull_request.number" not in yaml_str
