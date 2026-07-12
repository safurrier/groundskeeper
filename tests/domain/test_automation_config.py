from pathlib import Path

import pytest

from groundskeeper.domain.config import get_automations
from groundskeeper.domain.errors import ConfigError


def test_parses_github_pi_automation() -> None:
    items = get_automations(
        {
            "automations": {
                "daily": {
                    "source": {
                        "type": "github-issues",
                        "repository": "me/dots",
                        "repository-path": "/tmp/dots",
                        "trusted-authors": ["me"],
                    },
                    "runner": {
                        "type": "pi",
                        "skill": "issue-implementation",
                        "approval": "allow",
                        "session": "deterministic",
                    },
                    "policy": {
                        "concurrency": 1,
                        "output": "draft-pr",
                        "merge": "never",
                    },
                }
            }
        }
    )
    assert items[0].source.repository_path == Path("/tmp/dots").resolve()
    assert items[0].source.trusted_authors == ("me",)
    assert items[0].runner.skill == "issue-implementation"
    assert items[0].runner.approval == "allow"
    assert items[0].runner.session == "deterministic"
    assert items[0].runner.timeout_seconds == 7200


def test_rejects_non_positive_pi_timeout() -> None:
    config = {
        "automations": {
            "daily": {
                "source": {
                    "type": "github-issues",
                    "repository": "me/dots",
                    "repository-path": "/tmp/dots",
                    "trusted-authors": ["me"],
                },
                "runner": {
                    "type": "pi",
                    "skill": "issue-implementation",
                    "approval": "allow",
                    "session": "deterministic",
                    "timeout-seconds": 0,
                },
            }
        }
    }
    with pytest.raises(ConfigError, match=r"runner\.timeout-seconds"):
        get_automations(config)


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
    with pytest.raises(ConfigError, match=message):
        get_automations(
            {
                "automations": {
                    "daily": {
                        "source": {
                            "type": "github-issues",
                            "repository": "me/dots",
                            "repository-path": "/tmp/dots",
                            "trusted-authors": ["me"],
                        },
                        "runner": runner,
                    }
                }
            }
        )


@pytest.mark.parametrize("name", ["/tmp/lock", "../escape", "queue/a", "UPPER"])
def test_rejects_unsafe_automation_names(name: str) -> None:
    with pytest.raises(ConfigError, match="kebab-case"):
        get_automations(
            {
                "automations": {
                    name: {
                        "source": {
                            "type": "github-issues",
                            "repository": "me/dots",
                            "repository-path": "/tmp/dots",
                            "trusted-authors": ["me"],
                        },
                        "runner": {
                            "type": "pi",
                            "skill": "issue-implementation",
                            "approval": "allow",
                            "session": "deterministic",
                        },
                    }
                }
            }
        )


def test_rejects_relative_repository_path() -> None:
    with pytest.raises(ConfigError, match="must be absolute"):
        get_automations(
            {
                "automations": {
                    "daily": {
                        "source": {
                            "type": "github-issues",
                            "repository": "me/dots",
                            "repository-path": "../dots",
                            "trusted-authors": ["me"],
                        },
                        "runner": {
                            "type": "pi",
                            "skill": "issue-implementation",
                            "approval": "allow",
                            "session": "deterministic",
                        },
                    }
                }
            }
        )


@pytest.mark.parametrize(
    "labels",
    [
        {"ready": ""},
        {"ready": None},
        {"ready": "factory:shared", "running": "factory:shared"},
        {"unexpected": "factory:other"},
    ],
)
def test_rejects_invalid_lifecycle_labels(labels: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match=r"source\.labels"):
        get_automations(
            {
                "automations": {
                    "daily": {
                        "source": {
                            "type": "github-issues",
                            "repository": "me/dots",
                            "repository-path": "/tmp/dots",
                            "trusted-authors": ["me"],
                            "labels": labels,
                        },
                        "runner": {
                            "type": "pi",
                            "skill": "issue-implementation",
                            "approval": "allow",
                            "session": "deterministic",
                        },
                    }
                }
            }
        )


@pytest.mark.parametrize(
    "policy",
    [
        {"concurrency": 2},
        {"merge": "auto"},
        {"output": "merged-pr"},
    ],
)
def test_rejects_unsafe_policy(policy: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        get_automations(
            {
                "automations": {
                    "daily": {
                        "source": {
                            "type": "github-issues",
                            "repository": "me/dots",
                            "repository-path": "/tmp/dots",
                            "trusted-authors": ["me"],
                        },
                        "runner": {
                            "type": "pi",
                            "skill": "issue-implementation",
                            "approval": "allow",
                            "session": "deterministic",
                        },
                        "policy": policy,
                    }
                }
            }
        )
