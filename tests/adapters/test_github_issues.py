from dataclasses import replace

import pytest

from groundskeeper.adapters.gh import GhIssue
from groundskeeper.adapters.github_issues import GitHubIssuesTracker
from groundskeeper.domain.automation import (
    AdmissionState,
    PullRequestReconciliation,
    TaskState,
    automation_task_branch,
)
from groundskeeper.domain.config import (
    AutomationCheckout,
    AutomationPolicy,
    GitHubIssuesSource,
)
from groundskeeper.domain.task_contract import DependencyState, GitHubDependency

VALID_BODY = "## Factory Task\n\nSchema: 1\nKind: runnable\nMode: full\n\n## Dependencies\n\nNone\n"


class FakeGhClient:
    def __init__(self) -> None:
        self.issues = [
            GhIssue(
                1,
                "Trusted",
                VALID_BODY,
                "https://issue/1",
                "alex",
                ("factory:ready",),
                "open",
            ),
            GhIssue(
                2,
                "Untrusted",
                VALID_BODY,
                "https://issue/2",
                "mallory",
                ("factory:ready",),
                "open",
            ),
        ]
        self.labels: list[tuple[int, str, str]] = []
        self.comments: list[tuple[int, str]] = []
        self.issue_repositories: list[str] = []
        self.pull_request_repositories: list[str] = []
        self.pull_request_branches: list[tuple[str, str]] = []

    def list_issues(self, repository: str, labels: tuple[str, ...]) -> list[GhIssue]:
        self.issue_repositories.append(repository)
        return [
            item
            for item in self.issues
            if all(label in item.labels for label in labels)
        ]

    def get_issue(self, repository: str, issue: int) -> GhIssue:
        self.issue_repositories.append(repository)
        return next(item for item in self.issues if item.number == issue)

    def replace_label(self, repository: str, issue: int, old: str, new: str) -> None:
        self.issue_repositories.append(repository)
        self.labels.append((issue, old, new))
        self.issues = [
            replace(
                item,
                labels=tuple(new if label == old else label for label in item.labels),
            )
            if item.number == issue
            else item
            for item in self.issues
        ]

    def comment(self, repository: str, issue: int, body: str) -> None:
        self.issue_repositories.append(repository)
        self.comments.append((issue, body))

    def dependency_state(
        self, dependency: GitHubDependency
    ) -> tuple[DependencyState, str]:
        return DependencyState.COMPLETE, ""

    def reconcile_pull_requests(
        self, repository: str, source_issue: object
    ) -> PullRequestReconciliation:
        self.pull_request_repositories.append(repository)
        return PullRequestReconciliation()

    def reconcile_branch_pull_requests(
        self, repository: str, branch: str
    ) -> PullRequestReconciliation:
        self.pull_request_branches.append((repository, branch))
        return PullRequestReconciliation()


def test_filters_untrusted_authors_and_claim_is_idempotent() -> None:
    client = FakeGhClient()
    source = GitHubIssuesSource("source/queue", ("alex",))
    tracker = GitHubIssuesTracker(
        client,
        source,
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )
    tasks = tracker.list_ready()
    assert [task.source_issue.number for task in tasks] == [1]
    assert tasks[0].source_issue.repository == "source/queue"
    assert tasks[0].target_repository == "target/repo"
    first = tracker.claim(tasks[0])
    second = tracker.claim(tasks[0])
    assert first.claimed and first.task.state == TaskState.RUNNING
    assert not second.claimed
    assert client.labels == [(1, "factory:ready", "factory:running")]
    assert set(client.issue_repositories) == {"source/queue"}

    tracker.reconcile_pull_requests(first.task)
    assert client.pull_request_repositories == ["target/repo"]


def test_reconciliation_uses_task_branch_when_source_link_is_disabled() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(link_source_issue=False),
        AutomationCheckout(
            "isolated-worktree",
            "origin/main",
            "none",
            "changes/task",
        ),
    )
    task = tracker.list_ready()[0]

    tracker.reconcile_pull_requests(task)

    assert client.pull_request_repositories == []
    assert client.pull_request_branches == [
        ("target/repo", automation_task_branch(task, "changes/task"))
    ]


def test_claim_returns_freshly_relisted_issue_body() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )
    selected = tracker.list_ready()[0]
    changed_body = VALID_BODY.replace("Mode: full", "Mode: focused")
    client.issues = [replace(client.issues[0], body=changed_body)]

    claim = tracker.claim(selected)

    assert claim.claimed
    assert claim.task.body == changed_body
    assert claim.task.state is TaskState.RUNNING


def test_claim_fails_if_post_mutation_snapshot_is_no_longer_running() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )
    selected = tracker.list_ready()[0]
    client.get_issue = lambda repository, issue: replace(  # type: ignore[method-assign]
        client.issues[0], labels=("factory:blocked",)
    )

    claim = tracker.claim(selected)

    assert not claim.claimed
    assert "factory:running" in claim.reason


def test_refresh_rejects_closed_issue() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )
    running = replace(tracker.list_ready()[0], state=TaskState.RUNNING)
    client.get_issue = lambda repository, issue: replace(  # type: ignore[method-assign]
        client.issues[0], state="closed", labels=("factory:running",)
    )

    with pytest.raises(RuntimeError, match="no longer open"):
        tracker.refresh(running)


def test_lists_and_atomically_claims_deferred_work() -> None:
    client = FakeGhClient()
    client.issues = [
        GhIssue(
            7,
            "Retry",
            VALID_BODY,
            "https://issue/7",
            "alex",
            ("factory:deferred",),
            "open",
        )
    ]
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )

    task = tracker.list_deferred()[0]
    claim = tracker.claim(task)

    assert task.state is TaskState.DEFERRED
    assert claim.claimed
    assert claim.task.state is TaskState.RUNNING
    assert client.labels == [(7, "factory:deferred", "factory:running")]
    assert tracker.list_deferred() == []


def test_admission_blocks_tracking_and_unresolved_dependencies() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )
    tracking = replace(
        tracker.list_ready()[0],
        body="## Factory Task\n\nSchema: 1\nKind: tracking\n\n## Dependencies\n\nNone\n",
    )
    tracking_admission = tracker.admit(tracking)
    assert not tracking_admission.eligible
    assert tracking_admission.state is AdmissionState.TRACKING
    assert tracking_admission.task.contract is not None
    assert tracking_admission.task.contract.kind.value == "tracking"
    assert "Tracking" in tracking_admission.reason

    client.dependency_state = lambda dependency: (  # type: ignore[method-assign]
        DependencyState.UNRESOLVED,
        "Dependency issue is open: https://github.com/other/repo/issues/9",
    )
    blocked = replace(
        tracker.list_ready()[0],
        body=(
            "## Factory Task\n\nSchema: 1\nKind: runnable\nMode: full\n\n"
            "## Dependencies\n\n- https://github.com/other/repo/issues/9\n"
        ),
    )
    admission = tracker.admit(blocked)
    assert not admission.eligible
    assert admission.state is AdmissionState.DEPENDENCY_UNRESOLVED
    assert admission.task.contract is not None
    assert admission.task.contract.mode is not None
    assert admission.task.contract.mode.value == "full"
    assert "open" in admission.reason


def test_transition_to_deferred_uses_configured_label_and_ai_authored_comment() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource(
            "source/queue",
            ("alex",),
            deferred_label="queue:later",
        ),
        "target/repo",
        AutomationPolicy(),
        AutomationCheckout(),
    )
    running = tracker.claim(tracker.list_ready()[0]).task

    tracker.transition(running, TaskState.DEFERRED, "retry next tick")

    assert client.labels[-1] == (1, "factory:running", "queue:later")
    assert client.comments == [(1, "AI-authored factory update: retry next tick")]


def test_private_review_transition_uses_clickable_no_backlink_result_url() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(link_source_issue=False),
        AutomationCheckout("isolated-worktree", "origin/main"),
    )
    running = tracker.claim(tracker.list_ready()[0]).task

    tracker.transition(
        running,
        TaskState.REVIEW,
        (
            "https://github.com/TARGET/repo/pull/42\n\n"
            "Factory session: `private-session`"
        ),
        pull_request_url="https://github.com/target/REPO/pull/42",
    )

    assert client.comments == [
        (
            1,
            (
                "AI-authored factory update: "
                "https://redirect.github.com/target/repo/pull/42\n\n"
                "Factory session: `private-session`"
            ),
        )
    ]


def test_private_policy_rewrites_target_pr_urls_in_blocked_comments() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(link_source_issue=False),
        AutomationCheckout("isolated-worktree", "origin/main"),
    )
    running = tracker.claim(tracker.list_ready()[0]).task

    tracker.transition(
        running,
        TaskState.BLOCKED,
        "Closed pull request: https://github.com/TARGET/REPO/pull/42",
    )

    assert client.comments == [
        (
            1,
            (
                "AI-authored factory update: Closed pull request: "
                "https://redirect.github.com/target/repo/pull/42"
            ),
        )
    ]


def test_private_review_requires_typed_target_pr_before_mutation() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource("source/queue", ("alex",)),
        "target/repo",
        AutomationPolicy(link_source_issue=False),
        AutomationCheckout("isolated-worktree", "origin/main"),
    )
    running = tracker.claim(tracker.list_ready()[0]).task
    labels_before = list(client.labels)

    with pytest.raises(RuntimeError, match="exact target pull request URL"):
        tracker.transition(
            running,
            TaskState.REVIEW,
            "https://github.com/target/repo/pull/43",
            pull_request_url="https://github.com/target/repo/pull/42",
        )

    assert client.labels == labels_before
    assert client.comments == []
