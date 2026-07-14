from dataclasses import replace
from pathlib import Path

from groundskeeper.adapters.gh import GhIssue
from groundskeeper.adapters.github_issues import GitHubIssuesTracker
from groundskeeper.domain.automation import TaskState
from groundskeeper.domain.config import GitHubIssuesSource


class FakeGhClient:
    def __init__(self) -> None:
        self.issues = [
            GhIssue(
                1, "Trusted", "body", "https://issue/1", "alex", ("factory:ready",)
            ),
            GhIssue(
                2, "Untrusted", "body", "https://issue/2", "mallory", ("factory:ready",)
            ),
        ]
        self.labels: list[tuple[int, str, str]] = []
        self.comments: list[tuple[int, str]] = []

    def list_issues(self, repository: str, labels: tuple[str, ...]) -> list[GhIssue]:
        return [
            item
            for item in self.issues
            if all(label in item.labels for label in labels)
        ]

    def replace_label(self, repository: str, issue: int, old: str, new: str) -> None:
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
        self.comments.append((issue, body))

    def linked_pull_request(self, repository: str, issue: int) -> str | None:
        return None


def test_filters_untrusted_authors_and_claim_is_idempotent() -> None:
    client = FakeGhClient()
    source = GitHubIssuesSource("me/dots", Path("/tmp/dots"), ("alex",))
    tracker = GitHubIssuesTracker(client, source)
    tasks = tracker.list_ready()
    assert [task.external_id for task in tasks] == ["1"]
    first = tracker.claim(tasks[0])
    second = tracker.claim(tasks[0])
    assert first.claimed and first.task.state == TaskState.RUNNING
    assert not second.claimed
    assert client.labels == [(1, "factory:ready", "factory:running")]


def test_lists_and_atomically_claims_deferred_work() -> None:
    client = FakeGhClient()
    client.issues = [
        GhIssue(
            7,
            "Retry",
            "body",
            "https://issue/7",
            "alex",
            ("factory:deferred",),
        )
    ]
    tracker = GitHubIssuesTracker(
        client, GitHubIssuesSource("me/dots", Path("/tmp/dots"), ("alex",))
    )

    task = tracker.list_deferred()[0]
    claim = tracker.claim(task)

    assert task.state is TaskState.DEFERRED
    assert claim.claimed
    assert claim.task.state is TaskState.RUNNING
    assert client.labels == [(7, "factory:deferred", "factory:running")]
    assert tracker.list_deferred() == []


def test_transition_to_deferred_uses_configured_label_and_ai_authored_comment() -> None:
    client = FakeGhClient()
    tracker = GitHubIssuesTracker(
        client,
        GitHubIssuesSource(
            "me/dots",
            Path("/tmp/dots"),
            ("alex",),
            deferred_label="queue:later",
        ),
    )
    running = tracker.claim(tracker.list_ready()[0]).task

    tracker.transition(running, TaskState.DEFERRED, "retry next tick")

    assert client.labels[-1] == (1, "factory:running", "queue:later")
    assert client.comments == [(1, "AI-authored factory update: retry next tick")]
