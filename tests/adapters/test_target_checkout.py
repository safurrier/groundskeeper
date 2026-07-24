from pathlib import Path

import pytest

from groundskeeper.adapters.process import CommandResult
from groundskeeper.adapters.target_checkout import (
    TargetCheckoutError,
    validate_target_checkout,
)


class FakeProcess:
    def __init__(self, results: list[tuple[int, str, str]]) -> None:
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    def run(
        self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
    ) -> CommandResult:
        self.calls.append(argv)
        exit_code, stdout, stderr = self.results[len(self.calls) - 1]
        return CommandResult(argv, cwd, exit_code, stdout, stderr)


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/Example/Widgets.git\n",
        "git@github.com:Example/Widgets.git\n",
        "org-1965106@github.com:Example/Widgets.git\n",
        "ssh://git@github.com/Example/Widgets.git\n",
        "ssh://org-1965106@github.com/Example/Widgets.git\n",
    ],
)
def test_matching_github_https_and_ssh_origins_are_accepted(origin: str) -> None:
    process = FakeProcess([(0, "true\n", ""), (0, origin, "")])

    validate_target_checkout(process, Path("/repo"), "example/widgets")

    assert process.calls == [
        ("git", "rev-parse", "--is-inside-work-tree"),
        ("git", "remote", "get-url", "origin"),
    ]


def test_non_git_target_checkout_is_rejected() -> None:
    process = FakeProcess([(128, "", "not a git repository")])

    with pytest.raises(TargetCheckoutError, match="not a Git worktree"):
        validate_target_checkout(process, Path("/repo"), "example/widgets")


def test_mismatched_target_checkout_is_rejected() -> None:
    process = FakeProcess(
        [(0, "true\n", ""), (0, "https://github.com/other/repo.git\n", "")]
    )

    with pytest.raises(TargetCheckoutError, match="does not match"):
        validate_target_checkout(process, Path("/repo"), "example/widgets")


@pytest.mark.parametrize(
    "origin_result",
    [
        (2, "", "No such remote 'origin'"),
        (0, "https://gitlab.com/example/widgets.git\n", ""),
        (0, "git@github.com:example/too/many.git\n", ""),
    ],
)
def test_missing_or_malformed_origin_is_rejected(
    origin_result: tuple[int, str, str],
) -> None:
    process = FakeProcess([(0, "true\n", ""), origin_result])

    with pytest.raises(TargetCheckoutError):
        validate_target_checkout(process, Path("/repo"), "example/widgets")
