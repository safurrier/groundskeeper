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
                    "runner": {"type": "pi"},
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
                        "runner": {"type": "pi"},
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
                        "runner": {"type": "pi"},
                        "policy": policy,
                    }
                }
            }
        )
