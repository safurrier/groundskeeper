from dataclasses import replace
from pathlib import Path

from groundskeeper.automation import AutomationService
from groundskeeper.domain.automation import (
    AutomationTask,
    ClaimResult,
    SessionMetadata,
    TaskState,
    WorkResult,
)
from groundskeeper.domain.config import Automation, GitHubIssuesSource, PiRunnerConfig

TASK = AutomationTask(
    "fake", "7", "Do work", "Details", "https://task/7", "alex", "me/dots"
)
AUTOMATION = Automation(
    "daily",
    GitHubIssuesSource("me/dots", Path("/tmp/dots"), ("alex",)),
    PiRunnerConfig(skill="issue-implementation"),
)


class FakeTracker:
    def __init__(self) -> None:
        self.tasks = [TASK]
        self.transitions: list[tuple[TaskState, str]] = []
        self.pr: str | None = None
        self.policy_violation: str | None = None

    def list_ready(self) -> list[AutomationTask]:
        return self.tasks

    def list_running(self) -> list[AutomationTask]:
        return []

    def claim(self, task: AutomationTask) -> ClaimResult:
        return ClaimResult(True, replace(task, state=TaskState.RUNNING))

    def transition(
        self, task: AutomationTask, state: TaskState, detail: str = ""
    ) -> None:
        self.transitions.append((state, detail))

    def find_pull_request(self, task: AutomationTask) -> str | None:
        return self.pr

    def find_policy_violation(self, task: AutomationTask) -> str | None:
        return self.policy_violation


def _raise_github_timeout(task: AutomationTask) -> str | None:
    raise RuntimeError("command timed out after 30 seconds: gh")


class FakeRunner:
    def __init__(
        self, result: WorkResult, metadata: SessionMetadata | None = None
    ) -> None:
        self.result = result
        self.metadata = metadata
        self.calls = 0

    def session_metadata(self, task: AutomationTask) -> SessionMetadata | None:
        return self.metadata

    def run(self, task: AutomationTask, recovery: bool = False) -> WorkResult:
        self.calls += 1
        return self.result


def test_dry_run_has_no_mutations_or_dispatch() -> None:
    tracker = FakeTracker()
    runner = FakeRunner(WorkResult(True))
    result = AutomationService(tracker, runner).tick(AUTOMATION, dry_run=True)
    assert result.status == "would-dispatch"
    assert tracker.transitions == []
    assert runner.calls == 0


def test_success_requires_pr_and_transitions_to_review() -> None:
    tracker = FakeTracker()
    tracker.pr = "https://github/pr/1"
    runner = FakeRunner(WorkResult(True))
    result = AutomationService(tracker, runner).tick(AUTOMATION)
    assert result.status == "review"
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/1")]


def test_successful_review_propagates_resumable_session_metadata() -> None:
    tracker = FakeTracker()
    pull_requests = iter([None, "https://github/pr/1"])
    tracker.find_pull_request = lambda task: next(pull_requests)  # type: ignore[method-assign]
    worker = WorkResult(
        True,
        session_id="session-123",
        session_name="gk-me-dots-7",
        resume_command="pi --session session-123",
    )

    result = AutomationService(tracker, FakeRunner(worker)).tick(AUTOMATION)

    assert result.status == "review"
    assert result.session_id == "session-123"
    assert result.session_name == "gk-me-dots-7"
    assert result.resume_command == "pi --session session-123"
    assert result.detail == (
        "https://github/pr/1\n\n"
        "Factory session: `session-123`\n"
        "Session name: `gk-me-dots-7`\n"
        "Resume: `pi --session session-123`"
    )
    assert tracker.transitions == [(TaskState.REVIEW, result.detail)]


def test_worker_failure_with_valid_draft_pr_reconciles_to_review() -> None:
    tracker = FakeTracker()
    tracker.pr = "https://github/pr/1"
    result = AutomationService(
        tracker, FakeRunner(WorkResult(False, error="worker timed out", exit_code=124))
    ).tick(AUTOMATION)
    assert result.status == "review"
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/1")]


def test_worker_output_pr_must_match_tracker_closing_reference() -> None:
    tracker = FakeTracker()
    result = AutomationService(
        tracker,
        FakeRunner(
            WorkResult(True, pull_request_url="https://github.com/other/repo/pull/9")
        ),
    ).tick(AUTOMATION)
    assert result.status == "blocked"
    assert tracker.transitions == [
        (TaskState.BLOCKED, "Worker completed without an open draft pull request")
    ]


def test_noncompliant_closing_pr_is_blocked_as_policy_violation() -> None:
    tracker = FakeTracker()
    tracker.policy_violation = "Closing pull request is not a draft"
    result = AutomationService(tracker, FakeRunner(WorkResult(True))).tick(AUTOMATION)
    assert result.status == "blocked"
    assert tracker.transitions == [
        (TaskState.BLOCKED, "Closing pull request is not a draft")
    ]


def test_post_worker_github_timeout_blocks_claimed_task() -> None:
    tracker = FakeTracker()
    tracker.find_pull_request = _raise_github_timeout  # type: ignore[method-assign]
    result = AutomationService(tracker, FakeRunner(WorkResult(True))).tick(AUTOMATION)
    assert result.status == "blocked"
    assert tracker.transitions == [
        (TaskState.BLOCKED, "command timed out after 30 seconds: gh")
    ]


def test_timed_out_worker_is_blocked_with_actionable_detail() -> None:
    tracker = FakeTracker()
    result = AutomationService(
        tracker,
        FakeRunner(
            WorkResult(
                False, error="command timed out after 7200 seconds: pi", exit_code=124
            )
        ),
    ).tick(AUTOMATION)
    assert result.status == "blocked"
    assert tracker.transitions == [
        (TaskState.BLOCKED, "command timed out after 7200 seconds: pi")
    ]


def test_worker_failure_includes_resumable_session_metadata() -> None:
    tracker = FakeTracker()
    worker = WorkResult(
        False,
        error="boom",
        exit_code=1,
        session_id="session-123",
        session_name="gk-me-dots-7",
        resume_command="pi --session session-123",
    )

    result = AutomationService(tracker, FakeRunner(worker)).tick(AUTOMATION)

    assert result.status == "blocked"
    assert result.session_id == "session-123"
    assert result.detail.startswith("boom\n\nFactory session: `session-123`")
    assert tracker.transitions == [(TaskState.BLOCKED, result.detail)]


def test_worker_failure_is_visible_and_recoverable() -> None:
    tracker = FakeTracker()
    result = AutomationService(
        tracker, FakeRunner(WorkResult(False, error="boom", exit_code=1))
    ).tick(AUTOMATION)
    assert result.status == "blocked"
    assert tracker.transitions == [(TaskState.BLOCKED, "boom")]


def test_existing_pr_reconciles_without_dispatch() -> None:
    tracker = FakeTracker()
    tracker.pr = "https://github/pr/1"
    runner = FakeRunner(WorkResult(True))
    result = AutomationService(tracker, runner).tick(AUTOMATION)
    assert result.status == "review"
    assert runner.calls == 0


def test_running_task_with_existing_pr_preserves_session_handoff() -> None:
    tracker = FakeTracker()
    running = replace(TASK, state=TaskState.RUNNING)
    tracker.list_running = lambda: [running]  # type: ignore[method-assign]
    tracker.pr = "https://github/pr/9"
    metadata = SessionMetadata(
        "session-123", "gk-me-dots-7", "pi --session session-123"
    )
    runner = FakeRunner(WorkResult(True), metadata)

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "review"
    assert result.session_id == "session-123"
    assert result.detail.startswith("https://github/pr/9\n\nFactory session")
    assert tracker.transitions == [(TaskState.REVIEW, result.detail)]
    assert runner.calls == 0


def test_running_task_resumes_persisted_worker_and_reconciles() -> None:
    tracker = FakeTracker()
    tracker.list_running = lambda: [replace(TASK, state=TaskState.RUNNING)]  # type: ignore[method-assign]
    pull_requests = iter([None, "https://github/pr/9"])
    tracker.find_pull_request = lambda task: next(pull_requests)  # type: ignore[method-assign]
    runner = FakeRunner(WorkResult(True, pull_request_url="https://github/pr/9"))
    result = AutomationService(tracker, runner).tick(AUTOMATION)
    assert result.status == "review"
    assert runner.calls == 1
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/9")]
