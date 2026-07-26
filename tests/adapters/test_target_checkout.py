import subprocess
from pathlib import Path

import pytest

from groundskeeper.adapters.process import CommandResult, ProcessClient
from groundskeeper.adapters.target_checkout import (
    TargetCheckoutError,
    TargetCheckoutManager,
    prepare_target_checkout,
    target_checkout_common_dir,
    validate_target_checkout,
)
from groundskeeper.domain.automation import AutomationTask, GitHubIssueIdentity
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
            "+refs/heads/main:refs/remotes/origin/main",
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


def test_managed_checkout_requires_clean_linked_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:example/widgets.git"],
        cwd=repository,
        check=True,
    )
    (repository / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
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
        cwd=repository,
        check=True,
    )

    with pytest.raises(TargetCheckoutError, match="linked disposable worktree"):
        validate_target_checkout(
            ProcessClient(),
            repository,
            "example/widgets",
            AutomationCheckout("managed-worktree", "HEAD", "none"),
        )


def test_managed_checkout_reuses_disposable_worktree_without_second_checkout(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    canonical = tmp_path / "canonical"
    managed = tmp_path / "managed"
    subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
    subprocess.run(["git", "clone", "-q", remote, canonical], check=True)
    subprocess.run(["git", "switch", "-q", "-c", "main"], cwd=canonical, check=True)
    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:example/widgets.git"],
        cwd=canonical,
        check=True,
    )
    (canonical / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=canonical, check=True)
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
        cwd=canonical,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=canonical,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", managed, base],
        cwd=canonical,
        check=True,
    )

    checkout = AutomationCheckout("managed-worktree", "main", "none")
    base_sha = validate_target_checkout(
        ProcessClient(), managed, "example/widgets", checkout
    )
    task = AutomationTask(
        "github",
        "Do work",
        "Acceptance",
        "https://github.com/source/queue/issues/7",
        "alex",
        GitHubIssueIdentity("source/queue", 7),
        "example/widgets",
    )
    state_root = tmp_path / "state"
    workspace = TargetCheckoutManager(
        ProcessClient(),
        managed,
        "example/widgets",
        checkout,
        base_sha,
        state_root,
    ).workspace_for(task)

    assert workspace.path == managed
    assert workspace.base_sha == base
    assert not (state_root / "worktrees").exists()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch.startswith("groundskeeper/task-")

    wip = managed / "wip.txt"
    wip.write_text("resume me\n")
    resumed_base = validate_target_checkout(
        ProcessClient(), managed, "example/widgets", checkout
    )
    resumed = TargetCheckoutManager(
        ProcessClient(),
        managed,
        "example/widgets",
        checkout,
        resumed_base,
        state_root,
    ).workspace_for(task)
    assert resumed.path == managed
    assert wip.read_text() == "resume me\n"
    wip.unlink()

    (managed / "tracked.txt").write_text("task one\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=managed, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Groundskeeper Test",
            "-c",
            "user.email=groundskeeper@example.com",
            "commit",
            "-qm",
            "task one",
        ],
        cwd=managed,
        check=True,
    )
    second_task = AutomationTask(
        "github",
        "Do other work",
        "Acceptance",
        "https://github.com/source/queue/issues/8",
        "alex",
        GitHubIssueIdentity("source/queue", 8),
        "example/widgets",
    )
    second_base = validate_target_checkout(
        ProcessClient(), managed, "example/widgets", checkout
    )
    second = TargetCheckoutManager(
        ProcessClient(),
        managed,
        "example/widgets",
        checkout,
        second_base,
        state_root,
    ).workspace_for(second_task)

    assert second.base_sha == second_base == base
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=managed,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == base
    )


def test_workspace_rejects_task_for_different_target() -> None:
    manager = TargetCheckoutManager(
        ProcessClient(),
        Path("/repo"),
        "example/widgets",
        AutomationCheckout(),
        None,
        Path("/state"),
    )
    task = AutomationTask(
        "github",
        "Do work",
        "Acceptance",
        "https://github.com/source/queue/issues/7",
        "alex",
        GitHubIssueIdentity("source/queue", 7),
        "other/repository",
    )

    with pytest.raises(TargetCheckoutError, match="does not match"):
        manager.workspace_for(task)


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

    process = LocalRemoteProcess()
    checkout = AutomationCheckout("isolated-worktree", "origin/main", "fetch")
    base_sha = prepare_target_checkout(
        process,
        donor,
        "example/widgets",
        checkout,
    )
    task = AutomationTask(
        "github",
        "Do work",
        "Acceptance",
        "https://github.com/source/queue/issues/7",
        "alex",
        GitHubIssueIdentity("source/queue", 7),
        "example/widgets",
    )
    manager = TargetCheckoutManager(
        process,
        donor,
        "example/widgets",
        checkout,
        base_sha,
        tmp_path / "state",
    )
    workspace = manager.workspace_for(task)

    assert base_sha == remote_head
    assert workspace.base_sha == remote_head
    assert workspace.path != donor
    assert target_checkout_common_dir(process, donor) == target_checkout_common_dir(
        process, workspace.path
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace.path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == remote_head
    )
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

    (seed / "tracked.txt").write_text("three\n")
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
            "three",
        ],
        cwd=seed,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)
    newer_base = prepare_target_checkout(
        process,
        donor,
        "example/widgets",
        checkout,
    )
    recovered = TargetCheckoutManager(
        process,
        donor,
        "example/widgets",
        checkout,
        newer_base,
        tmp_path / "state",
    ).workspace_for(task)

    assert newer_base != remote_head
    assert recovered.path == workspace.path
    assert recovered.base_sha == remote_head

    pinned_ref = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--format=%(refname)",
            "refs/groundskeeper/bases",
        ],
        cwd=donor,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", "-d", pinned_ref], cwd=donor, check=True)

    with pytest.raises(TargetCheckoutError, match="without its immutable base pin"):
        TargetCheckoutManager(
            process,
            donor,
            "example/widgets",
            checkout,
            newer_base,
            tmp_path / "state",
        ).workspace_for(task)


def test_isolated_checkout_recovers_only_untouched_interrupted_worktree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", "-b", "main", repository], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:example/widgets.git"],
        cwd=repository,
        check=True,
    )
    (repository / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
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
        cwd=repository,
        check=True,
    )
    process = ProcessClient()
    checkout = AutomationCheckout("isolated-worktree", "HEAD", "none")
    base_sha = validate_target_checkout(
        process, repository, "example/widgets", checkout
    )
    task = AutomationTask(
        "github",
        "Do work",
        "Acceptance",
        "https://github.com/source/queue/issues/7",
        "alex",
        GitHubIssueIdentity("source/queue", 7),
        "example/widgets",
    )
    manager = TargetCheckoutManager(
        process,
        repository,
        "example/widgets",
        checkout,
        base_sha,
        tmp_path / "state",
    )
    workspace = manager.workspace_for(task)
    branch_ref = subprocess.run(
        ["git", "symbolic-ref", "HEAD"],
        cwd=workspace.path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "worktree", "remove", str(workspace.path)],
        cwd=repository,
        check=True,
    )
    workspace.path.mkdir(parents=True)
    common_dir = target_checkout_common_dir(process, repository)
    (workspace.path / ".git").write_text(
        f"gitdir: {common_dir / 'worktrees' / workspace.path.name}\n"
    )
    (workspace.path / "partial.txt").write_text("interrupted\n")

    recovered = manager.workspace_for(task)

    assert recovered.path == workspace.path
    assert not (workspace.path / "partial.txt").exists()
    assert (
        workspace.path.with_name(f"{workspace.path.name}.interrupted") / "partial.txt"
    ).read_text() == "interrupted\n"
    assert (
        subprocess.run(
            ["git", "symbolic-ref", "HEAD"],
            cwd=workspace.path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == branch_ref
    )

    subprocess.run(
        ["git", "worktree", "remove", str(workspace.path)],
        cwd=repository,
        check=True,
    )
    advanced_sha = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Groundskeeper Test",
            "-c",
            "user.email=groundskeeper@example.com",
            "commit-tree",
            f"{base_sha}^{{tree}}",
            "-p",
            base_sha,
            "-m",
            "advanced",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", branch_ref, advanced_sha, base_sha],
        cwd=repository,
        check=True,
    )
    workspace.path.mkdir(parents=True)
    (workspace.path / ".git").write_text(
        f"gitdir: {common_dir / 'worktrees' / workspace.path.name}\n"
    )
    marker = workspace.path / "preserve.txt"
    marker.write_text("do not delete\n")

    with pytest.raises(TargetCheckoutError):
        manager.workspace_for(task)

    assert marker.read_text() == "do not delete\n"
