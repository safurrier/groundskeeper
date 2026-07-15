from dataclasses import replace
from pathlib import Path

from groundskeeper.automation import AutomationService
from groundskeeper.domain.automation import (
    AutomationTask,
    ClaimResult,
    FailureDisposition,
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
        self.deferred_tasks: list[AutomationTask] = []
        self.claims: list[AutomationTask] = []
        self.transitions: list[tuple[TaskState, str]] = []
        self.pr: str | None = None
        self.policy_violation: str | None = None

    def list_ready(self) -> list[AutomationTask]:
        return self.tasks

    def list_running(self) -> list[AutomationTask]:
        return []

    def list_deferred(self) -> list[AutomationTask]:
        return self.deferred_tasks

    def claim(self, task: AutomationTask) -> ClaimResult:
        self.claims.append(task)
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
        self.recovery_calls: list[bool] = []

    def session_metadata(self, task: AutomationTask) -> SessionMetadata | None:
        return self.metadata

    def run(self, task: AutomationTask, recovery: bool = False) -> WorkResult:
        self.calls += 1
        self.recovery_calls.append(recovery)
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
        public_detail="Summary: should not replace PR authority",
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


def test_public_blocker_detail_is_used_when_no_draft_pr_exists() -> None:
    tracker = FakeTracker()
    worker = WorkResult(
        True,
        public_detail=(
            "Summary: Focused tests pass, but review requires a decision.\n\n"
            "Next action: Approve the recorded skip and resume."
        ),
        session_id="session-123",
        resume_command="pi --session session-123",
    )

    result = AutomationService(tracker, FakeRunner(worker)).tick(AUTOMATION)

    assert result.status == "blocked"
    assert result.detail == (
        "Summary: Focused tests pass, but review requires a decision.\n\n"
        "Next action: Approve the recorded skip and resume.\n\n"
        "Factory session: `session-123`\n"
        "Resume: `pi --session session-123`"
    )
    assert tracker.transitions == [(TaskState.BLOCKED, result.detail)]


def test_public_blocker_detail_is_used_for_durable_worker_failure() -> None:
    tracker = FakeTracker()
    worker = WorkResult(
        False,
        error="private process detail",
        exit_code=1,
        public_detail="Summary: Public-safe failure.\n\nNext action: Fix it.",
    )

    result = AutomationService(tracker, FakeRunner(worker)).tick(AUTOMATION)

    assert result.status == "blocked"
    assert result.detail == "Summary: Public-safe failure.\n\nNext action: Fix it."


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
    result = AutomationService(
        tracker,
        FakeRunner(WorkResult(True, public_detail="Summary: must not override policy")),
    ).tick(AUTOMATION)
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


def test_transient_failure_is_deferred_with_session_handoff() -> None:
    tracker = FakeTracker()
    worker = WorkResult(
        False,
        error="Codex usage limit reached; retry after 16:00 UTC",
        exit_code=1,
        session_id="session-123",
        session_name="gk-me-dots-7",
        resume_command="pi --session session-123",
        failure_disposition=FailureDisposition.DEFERRED,
    )

    result = AutomationService(tracker, FakeRunner(worker)).tick(AUTOMATION)

    assert result.status == "deferred"
    assert result.task is not None and result.task.state is TaskState.DEFERRED
    assert result.session_id == "session-123"
    assert "Transient provider exhaustion" in result.detail
    assert "next tick" in result.detail
    assert "Codex usage limit reached" in result.detail
    assert "Resume: `pi --session session-123`" in result.detail
    assert tracker.transitions == [(TaskState.DEFERRED, result.detail)]


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
    assert result.task is not None and result.task.state is TaskState.BLOCKED
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


def test_deferred_task_is_claimed_before_ready_and_resumed_on_next_tick() -> None:
    tracker = FakeTracker()
    deferred = replace(TASK, state=TaskState.DEFERRED)
    tracker.deferred_tasks = [deferred]
    pull_requests = iter([None, "https://github/pr/9"])
    tracker.find_pull_request = lambda task: next(pull_requests)  # type: ignore[method-assign]
    worker = WorkResult(
        True,
        session_id="session-123",
        session_name="gk-me-dots-7",
        resume_command="pi --session session-123",
    )
    runner = FakeRunner(worker)

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "review"
    assert result.task is not None and result.task.state is TaskState.REVIEW
    assert tracker.claims == [deferred]
    assert runner.recovery_calls == [True]
    assert result.session_id == "session-123"
    assert tracker.transitions == [(TaskState.REVIEW, result.detail)]


def test_repeat_transient_failure_returns_deferred_task_to_deferred() -> None:
    tracker = FakeTracker()
    deferred = replace(TASK, state=TaskState.DEFERRED)
    tracker.deferred_tasks = [deferred]
    worker = WorkResult(
        False,
        error="HTTP 429 Too Many Requests",
        exit_code=1,
        failure_disposition=FailureDisposition.DEFERRED,
    )
    runner = FakeRunner(worker)

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "deferred"
    assert result.task is not None and result.task.state is TaskState.DEFERRED
    assert tracker.claims == [deferred]
    assert runner.recovery_calls == [True]
    assert tracker.transitions == [(TaskState.DEFERRED, result.detail)]


def test_running_work_has_priority_over_deferred_and_ready() -> None:
    tracker = FakeTracker()
    running = replace(TASK, external_id="running", state=TaskState.RUNNING)
    deferred = replace(TASK, external_id="deferred", state=TaskState.DEFERRED)
    tracker.list_running = lambda: [running]  # type: ignore[method-assign]
    tracker.deferred_tasks = [deferred]
    tracker.pr = "https://github/pr/9"
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.task is not None
    assert result.task.external_id == running.external_id
    assert result.task.state is TaskState.REVIEW
    assert tracker.claims == []


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
