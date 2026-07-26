import subprocess
from pathlib import Path

import pytest

from groundskeeper.adapters.process import CommandResult, ProcessClient
from groundskeeper.adapters.target_checkout import (
    TargetCheckoutError,
    prepare_target_checkout,
    validate_target_checkout,
)
from groundskeeper.domain.config import AutomationCheckout


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


def test_isolated_checkout_requires_resolved_base_without_touching_donor() -> None:
    process = FakeProcess(
        [
            (0, "true\n", ""),
            (0, "git@github.com:Example/Widgets.git\n", ""),
            (0, "abc123\n", ""),
        ]
    )

    base_sha = validate_target_checkout(
        process,
        Path("/repo"),
        "example/widgets",
        AutomationCheckout("isolated-worktree", "origin/main", "none"),
    )

    assert base_sha == "abc123"
    assert process.calls == [
        ("git", "rev-parse", "--is-inside-work-tree"),
        ("git", "remote", "get-url", "origin"),
        (
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            "origin/main^{commit}",
        ),
    ]
    assert all("status" not in call for call in process.calls)


def test_live_isolated_checkout_fetches_before_resolving_base() -> None:
    process = FakeProcess(
        [
            (0, "true\n", ""),
            (0, "git@github.com:Example/Widgets.git\n", ""),
            (0, "", ""),
            (0, "abc123\n", ""),
        ]
    )

    base_sha = prepare_target_checkout(
        process,
        Path("/repo"),
        "example/widgets",
        AutomationCheckout("isolated-worktree", "origin/main", "fetch"),
    )

    assert base_sha == "abc123"
    assert process.calls[-2:] == [
        (
            "git",
            "fetch",
            "--no-tags",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        ),
        (
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            "origin/main^{commit}",
        ),
    ]


def test_live_isolated_checkout_stops_when_fetch_fails() -> None:
    process = FakeProcess(
        [
            (0, "true\n", ""),
            (0, "git@github.com:Example/Widgets.git\n", ""),
            (1, "", "offline"),
        ]
    )

    with pytest.raises(TargetCheckoutError, match="refresh failed"):
        prepare_target_checkout(
            process,
            Path("/repo"),
            "example/widgets",
            AutomationCheckout("isolated-worktree", "origin/main", "fetch"),
        )

    assert len(process.calls) == 3


def test_live_refresh_preserves_dirty_donor_worktree(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    donor = tmp_path / "donor"
    subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True
    )
    (seed / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=seed, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Groundskeeper Test",
            "-c",
            "user.email=groundskeeper@example.com",
            "commit",
            "-qm",
            "one",
        ],
        cwd=seed,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=seed, check=True)
    subprocess.run(["git", "clone", "-q", "-b", "main", remote, donor], check=True)
    donor_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=donor,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (donor / "unrelated.txt").write_text("preserve me\n")
    (seed / "tracked.txt").write_text("two\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=seed, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Groundskeeper Test",
            "-c",
            "user.email=groundskeeper@example.com",
            "commit",
            "-qm",
            "two",
        ],
        cwd=seed,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)
    remote_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=seed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    class LocalRemoteProcess(ProcessClient):
        def run(
            self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
        ) -> CommandResult:
            if argv == ("git", "remote", "get-url", "origin"):
                return CommandResult(
                    argv, cwd, 0, "git@github.com:example/widgets.git\n", ""
                )
            return super().run(argv, cwd, timeout)

    base_sha = prepare_target_checkout(
        LocalRemoteProcess(),
        donor,
        "example/widgets",
        AutomationCheckout("isolated-worktree", "origin/main", "fetch"),
    )

    assert base_sha == remote_head
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=donor,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == donor_head
    )
    assert (donor / "unrelated.txt").read_text() == "preserve me\n"
