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

    def list_issues(self, repository: str, labels: tuple[str, ...]) -> list[GhIssue]:
        return [
            item
            for item in self.issues
            if all(label in item.labels for label in labels)
        ]

    def replace_label(self, repository: str, issue: int, old: str, new: str) -> None:
        self.labels.append((issue, old, new))
        self.issues = [item for item in self.issues if item.number != issue]

    def comment(self, repository: str, issue: int, body: str) -> None:
        pass

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
