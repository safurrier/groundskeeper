from pathlib import Path

import pytest

from groundskeeper.domain.config import get_automations
from groundskeeper.domain.errors import ConfigError


def _valid_automation_config() -> dict[str, object]:
    return {
        "automations": {
            "daily": {
                "source": {
                    "type": "github-issues",
                    "repository": "me/queue",
                    "trusted-authors": ["me"],
                    "labels": {},
                },
                "target": {
                    "repository": "me/dots",
                    "repository-path": "/tmp/dots",
                },
                "runner": {
                    "type": "pi",
                    "skill": "issue-implementation",
                    "approval": "allow",
                    "session": "deterministic",
                },
                "policy": {"concurrency": 1, "output": "draft-pr", "merge": "never"},
            }
        }
    }


def test_parses_explicit_source_and_target_automation() -> None:
    item = get_automations(_valid_automation_config())[0]

    assert item.source.repository == "me/queue"
    assert item.source.trusted_authors == ("me",)
    assert item.source.deferred_label == "factory:deferred"
    assert item.target.repository == "me/dots"
    assert item.target.repository_path == Path("/tmp/dots").resolve()
    assert item.target.checkout.mode == "existing"
    assert item.target.checkout.base_ref is None
    assert item.target.checkout.refresh == "none"
    assert item.runner.skill == "issue-implementation"
    assert item.runner.approval == "allow"
    assert item.runner.session == "deterministic"
    assert item.runner.timeout_seconds == 7200


def test_parses_isolated_worktree_checkout_policy() -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["target"]["checkout"] = {  # type: ignore[index]
        "mode": "isolated-worktree",
        "base-ref": "origin/main",
        "refresh": "fetch",
    }

    checkout = get_automations(config)[0].target.checkout

    assert checkout.mode == "isolated-worktree"
    assert checkout.base_ref == "origin/main"
    assert checkout.refresh == "fetch"


@pytest.mark.parametrize(
    ("checkout", "message"),
    [
        ({}, "checkout.mode"),
        (None, "target.checkout must be a mapping"),
        ({"mode": "unknown"}, "checkout.mode"),
        ({"mode": "existing", "base-ref": "origin/main"}, "only valid"),
        ({"mode": "isolated-worktree"}, "checkout.base-ref"),
        (
            {"mode": "isolated-worktree", "base-ref": "main", "refresh": "fetch"},
            "origin/",
        ),
        (
            {
                "mode": "isolated-worktree",
                "base-ref": "origin/main",
                "refresh": "sometimes",
            },
            "checkout.refresh",
        ),
        (
            {
                "mode": "isolated-worktree",
                "base-ref": "origin/main",
                "unexpected": True,
            },
            "checkout.*unknown key",
        ),
    ],
)
def test_rejects_invalid_checkout_policy(
    checkout: dict[str, object] | None, message: str
) -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["target"]["checkout"] = checkout  # type: ignore[index]

    with pytest.raises(ConfigError, match=message):
        get_automations(config)


@pytest.mark.parametrize("missing", ["source", "target"])
def test_requires_explicit_source_and_target(missing: str) -> None:
    config = _valid_automation_config()
    del config["automations"]["daily"][missing]  # type: ignore[index]

    with pytest.raises(ConfigError, match=missing):
        get_automations(config)


def test_rejects_legacy_source_repository_path_as_unknown() -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["source"]["repository-path"] = "/tmp/queue"  # type: ignore[index]

    with pytest.raises(ConfigError, match=r"source.*unknown key 'repository-path'"):
        get_automations(config)


@pytest.mark.parametrize("key", ["repository", "repository-path"])
def test_requires_every_target_key(key: str) -> None:
    config = _valid_automation_config()
    del config["automations"]["daily"]["target"][key]  # type: ignore[index]

    with pytest.raises(ConfigError, match=rf"target\.{key}"):
        get_automations(config)


def test_rejects_relative_target_repository_path() -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["target"]["repository-path"] = "../dots"  # type: ignore[index]

    with pytest.raises(ConfigError, match=r"target\.repository-path must be absolute"):
        get_automations(config)


@pytest.mark.parametrize("section", ["source", "target"])
@pytest.mark.parametrize(
    "repository",
    [" owner/repo", "owner/repo ", "owner /repo", "owner/", "/repo", "a/b/c"],
)
def test_rejects_noncanonical_repository_identity(
    section: str, repository: str
) -> None:
    config = _valid_automation_config()
    config["automations"]["daily"][section]["repository"] = repository  # type: ignore[index]

    with pytest.raises(ConfigError, match=rf"{section}\.repository.*owner/repo"):
        get_automations(config)


def test_rejects_non_positive_pi_timeout() -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["runner"]["timeout-seconds"] = 0  # type: ignore[index]
    with pytest.raises(ConfigError, match=r"runner\.timeout-seconds"):
        get_automations(config)


@pytest.mark.parametrize(
    ("section", "key", "expected"),
    [
        ("entry", "unexpected", "automations.daily"),
        ("source", "repo", "automations.daily.source"),
        ("target", "checkout-policy", "automations.daily.target"),
        ("runner", "timeout_seconds", "automations.daily.runner"),
        ("policy", "concurency", "automations.daily.policy"),
        ("labels", "done", "automations.daily.source.labels"),
    ],
)
def test_rejects_unknown_automation_keys(section: str, key: str, expected: str) -> None:
    config = _valid_automation_config()
    automation = config["automations"]["daily"]  # type: ignore[index]
    if section == "labels":
        target = automation["source"]["labels"]  # type: ignore[index]
    else:
        target = automation if section == "entry" else automation[section]  # type: ignore[index]
    target[key] = "invalid"  # type: ignore[index]
    with pytest.raises(ConfigError, match=expected):
        get_automations(config)


def test_rejects_misspelled_top_level_automation() -> None:
    with pytest.raises(ConfigError, match="Use 'automations'"):
        get_automations({"automation": {}})


@pytest.mark.parametrize("key", ["automtion", "automationz", "foo"])
def test_rejects_every_unknown_top_level_key(key: str) -> None:
    with pytest.raises(ConfigError, match=rf"config has unknown key '{key}'"):
        get_automations({key: {}})


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        ({"type": "pi"}, "runner.skill"),
        (
            {
                "type": "pi",
                "skill": "issue-implementation",
                "approval": "ask",
                "session": "deterministic",
            },
            "runner.approval",
        ),
        (
            {
                "type": "pi",
                "skill": "issue-implementation",
                "approval": "allow",
                "session": "random",
            },
            "runner.session",
        ),
    ],
)
def test_rejects_incomplete_or_unsafe_pi_runner(
    runner: dict[str, str], message: str
) -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["runner"] = runner  # type: ignore[index]
    with pytest.raises(ConfigError, match=message):
        get_automations(config)


@pytest.mark.parametrize("name", ["/tmp/lock", "../escape", "queue/a", "UPPER"])
def test_rejects_unsafe_automation_names(name: str) -> None:
    config = _valid_automation_config()
    value = config["automations"].pop("daily")  # type: ignore[union-attr]
    config["automations"][name] = value  # type: ignore[index]
    with pytest.raises(ConfigError, match="kebab-case"):
        get_automations(config)


@pytest.mark.parametrize(
    "labels",
    [
        {"ready": ""},
        {"ready": None},
        {"ready": "factory:shared", "running": "factory:shared"},
        {"running": "factory:shared", "deferred": "factory:shared"},
        {"unexpected": "factory:other"},
    ],
)
def test_rejects_invalid_lifecycle_labels(labels: dict[str, object]) -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["source"]["labels"] = labels  # type: ignore[index]
    with pytest.raises(ConfigError, match=r"source\.labels"):
        get_automations(config)


def test_parses_custom_deferred_label() -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["source"]["labels"] = {  # type: ignore[index]
        "deferred": "queue:retry-later"
    }

    automation = get_automations(config)[0]

    assert automation.source.deferred_label == "queue:retry-later"


@pytest.mark.parametrize(
    "policy",
    [
        {"concurrency": 2},
        {"merge": "auto"},
        {"output": "merged-pr"},
    ],
)
def test_rejects_unsafe_policy(policy: dict[str, object]) -> None:
    config = _valid_automation_config()
    config["automations"]["daily"]["policy"] = policy  # type: ignore[index]
    with pytest.raises(ConfigError):
        get_automations(config)
