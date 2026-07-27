from dataclasses import replace
from pathlib import Path

import pytest

from groundskeeper.automation import AutomationService
from groundskeeper.domain.automation import (
    AdmissionResult,
    AdmissionState,
    AdmittedTask,
    AutomationTask,
    ClaimResult,
    FailureDisposition,
    GitHubIssueIdentity,
    PullRequestReconciliation,
    ReviewPullRequest,
    ReviewPullRequestState,
    SessionMetadata,
    TaskState,
    WorkResult,
)
from groundskeeper.domain.config import (
    Automation,
    AutomationTarget,
    GitHubIssuesSource,
    PiRunnerConfig,
)
from groundskeeper.domain.task_contract import parse_factory_task

TASK_BODY = "## Factory Task\n\nSchema: 1\nKind: runnable\nMode: full\n\n## Dependencies\n\nNone\n"
TASK_CONTRACT = parse_factory_task(TASK_BODY)
TASK = AutomationTask(
    "fake",
    "Do work",
    TASK_BODY,
    "https://task/7",
    "alex",
    GitHubIssueIdentity("source/queue", 7),
    "target/repo",
)
AUTOMATION = Automation(
    "daily",
    GitHubIssuesSource("source/queue", ("alex",), ("alex",)),
    AutomationTarget("target/repo", Path("/tmp/repo")),
    PiRunnerConfig(skill="issue-implementation"),
)


class FakeTracker:
    def __init__(self) -> None:
        self.tasks = [TASK]
        self.deferred_tasks: list[AutomationTask] = []
        self.review_tasks: list[AutomationTask] = []
        self.closed_tasks: list[AutomationTask] = []
        self.claims: list[AutomationTask] = []
        self.transitions: list[tuple[TaskState, str]] = []
        self.pr: str | None = None
        self.policy_violation: str | None = None
        self.reconciliation_error: str | None = None
        self.review_pull_request: ReviewPullRequest | None = None
        self.closed: list[tuple[AutomationTask, bool]] = []

    def list_ready(self) -> list[AutomationTask]:
        return self.tasks

    def list_running(self) -> list[AutomationTask]:
        return []

    def list_deferred(self) -> list[AutomationTask]:
        return self.deferred_tasks

    def list_review(self) -> list[AutomationTask]:
        return self.review_tasks

    def list_closed(self) -> list[AutomationTask]:
        return self.closed_tasks

    def refresh(self, task: AutomationTask) -> AutomationTask:
        return task

    def admit(self, task: AutomationTask) -> AdmissionResult:
        contracted = replace(task, contract=TASK_CONTRACT)
        return AdmissionResult(
            True,
            contracted,
            AdmissionState.ADMITTED,
            admitted_task=AdmittedTask(contracted, TASK_CONTRACT),
        )

    def claim(self, task: AutomationTask) -> ClaimResult:
        self.claims.append(task)
        return ClaimResult(True, replace(task, state=TaskState.RUNNING))

    def transition(
        self,
        task: AutomationTask,
        state: TaskState,
        detail: str = "",
        *,
        pull_request_url: str | None = None,
    ) -> None:
        self.transitions.append((state, detail))

    def reconcile_pull_requests(
        self, task: AutomationTask
    ) -> PullRequestReconciliation:
        if self.reconciliation_error is not None:
            raise RuntimeError(self.reconciliation_error)
        return PullRequestReconciliation(self.pr, self.policy_violation)

    def reconcile_review(self, task: AutomationTask) -> ReviewPullRequest | None:
        return self.review_pull_request

    def close(self, task: AutomationTask, *, merged: bool) -> None:
        self.closed.append((task, merged))


def _raise_running_label_changed(task: AutomationTask) -> AutomationTask:
    raise RuntimeError("task no longer has lifecycle label factory:running")


@pytest.mark.parametrize(
    ("state", "merged", "detail"),
    [
        (ReviewPullRequestState.MERGED, True, "Merged target pull request"),
        (
            ReviewPullRequestState.CLOSED,
            False,
            "Target pull request closed without merge",
        ),
    ],
)
def test_terminal_review_closes_without_dispatch(
    state: ReviewPullRequestState, merged: bool, detail: str
) -> None:
    tracker = FakeTracker()
    review = replace(TASK, state=TaskState.REVIEW)
    tracker.tasks = []
    tracker.review_tasks = [review]
    tracker.review_pull_request = ReviewPullRequest("https://github/pr/9", state)
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "closed"
    assert result.task is not None and result.task.state is TaskState.CLOSED
    assert result.pull_request_url == "https://github/pr/9"
    assert detail in result.detail
    assert tracker.closed == [(review, merged)]
    assert runner.calls == 0


def test_open_review_stays_review_and_does_not_block_other_ready_work() -> None:
    tracker = FakeTracker()
    tracker.review_tasks = [replace(TASK, state=TaskState.REVIEW)]
    tracker.review_pull_request = ReviewPullRequest(
        "https://github/pr/9", ReviewPullRequestState.OPEN
    )
    tracker.pr = "https://github/pr/10"
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "review"
    assert tracker.closed == []


def test_open_closed_label_recovers_partial_terminal_mutation() -> None:
    tracker = FakeTracker()
    pending = replace(TASK, state=TaskState.CLOSED)
    tracker.tasks = []
    tracker.closed_tasks = [pending]
    tracker.review_pull_request = ReviewPullRequest(
        "https://github/pr/9", ReviewPullRequestState.MERGED
    )

    result = AutomationService(tracker, FakeRunner(WorkResult(True))).tick(AUTOMATION)

    assert result.status == "closed"
    assert tracker.closed == [(pending, True)]


def test_missing_review_pr_preserves_lifecycle_without_mutation() -> None:
    tracker = FakeTracker()
    tracker.tasks = []
    tracker.review_tasks = [replace(TASK, state=TaskState.REVIEW)]

    result = AutomationService(tracker, FakeRunner(WorkResult(True))).tick(AUTOMATION)

    assert result.status == "no-work"
    assert tracker.closed == []


class FakeRunner:
    def __init__(
        self,
        result: WorkResult,
        metadata: SessionMetadata | None = None,
        readiness: str | None = None,
    ) -> None:
        self.result = result
        self.metadata = metadata
        self.readiness_reason = readiness
        self.calls = 0
        self.recovery_calls: list[bool] = []

    def readiness(self, task: AutomationTask) -> str | None:
        return self.readiness_reason

    def session_metadata(self, task: AutomationTask) -> SessionMetadata | None:
        return self.metadata

    def run(self, task: AdmittedTask, recovery: bool = False) -> WorkResult:
        self.calls += 1
        self.recovery_calls.append(recovery)
        return self.result


@pytest.mark.parametrize("state", [TaskState.RUNNING, TaskState.DEFERRED])
def test_invalid_recovery_admission_blocks_without_runner(
    state: TaskState,
) -> None:
    tracker = FakeTracker()
    task = replace(TASK, state=state)
    if state is TaskState.RUNNING:
        tracker.list_running = lambda: [task]  # type: ignore[method-assign]
    else:
        tracker.tasks = []
        tracker.deferred_tasks = [task]
    tracker.admit = lambda value: AdmissionResult(  # type: ignore[method-assign]
        False,
        value,
        AdmissionState.CONTRACT_INVALID,
        "missing exact ## Factory Task section",
    )
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "blocked"
    assert runner.calls == 0
    assert tracker.transitions == [
        (TaskState.BLOCKED, "missing exact ## Factory Task section")
    ]


def test_invalid_admission_blocks_without_runner_and_dry_run_is_mutation_free() -> None:
    tracker = FakeTracker()
    tracker.admit = lambda task: AdmissionResult(  # type: ignore[method-assign]
        False,
        task,
        AdmissionState.CONTRACT_INVALID,
        "missing exact ## Factory Task section",
    )
    runner = FakeRunner(WorkResult(True))
    dry = AutomationService(tracker, runner).tick(AUTOMATION, dry_run=True)
    assert dry.status == "would-block"
    assert dry.pull_request_url is None
    assert dry.detail == "missing exact ## Factory Task section"
    assert tracker.transitions == []
    assert runner.calls == 0

    result = AutomationService(tracker, runner).tick(AUTOMATION)
    assert result.status == "blocked"
    assert tracker.transitions == [
        (TaskState.BLOCKED, "missing exact ## Factory Task section")
    ]
    assert runner.calls == 0


def test_occupied_managed_workspace_waits_without_claiming_unrelated_task() -> None:
    tracker = FakeTracker()
    reason = "managed target has uncommitted work for another task"
    runner = FakeRunner(WorkResult(True), readiness=reason)

    dry = AutomationService(tracker, runner).tick(AUTOMATION, dry_run=True)
    live = AutomationService(tracker, runner).tick(AUTOMATION)

    assert dry.status == "would-wait"
    assert live.status == "not-claimed"
    assert dry.detail == live.detail == reason
    assert live.operator_detail == reason
    assert tracker.claims == []
    assert tracker.transitions == []
    assert runner.calls == 0


def test_contract_change_after_claim_blocks_before_runner() -> None:
    tracker = FakeTracker()
    changed = replace(TASK, body="changed", state=TaskState.RUNNING)

    def claim(task: AutomationTask) -> ClaimResult:
        tracker.claims.append(task)
        return ClaimResult(True, changed)

    tracker.claim = claim  # type: ignore[method-assign]
    tracker.admit = lambda task: AdmissionResult(  # type: ignore[method-assign]
        False,
        task,
        AdmissionState.CONTRACT_INVALID,
        "Factory Task changed after admission",
    )
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "blocked"
    assert runner.calls == 0
    assert tracker.claims == [TASK]
    assert tracker.transitions == [
        (TaskState.BLOCKED, "Factory Task changed after admission")
    ]


def test_running_task_relabel_stops_without_overwriting_lifecycle() -> None:
    tracker = FakeTracker()
    running = replace(TASK, state=TaskState.RUNNING)
    tracker.list_running = lambda: [running]  # type: ignore[method-assign]
    tracker.pr = "https://github/pr/1"
    tracker.refresh = _raise_running_label_changed  # type: ignore[method-assign]
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "not-claimed"
    assert "factory:running" in result.detail
    assert tracker.transitions == []
    assert runner.calls == 0


def test_running_pr_reconciles_before_invalid_contract_blocks_recovery() -> None:
    tracker = FakeTracker()
    running = replace(TASK, state=TaskState.RUNNING)
    tracker.list_running = lambda: [running]  # type: ignore[method-assign]
    tracker.pr = "https://github/pr/1"
    tracker.admit = lambda task: AdmissionResult(  # type: ignore[method-assign]
        False, task, AdmissionState.CONTRACT_INVALID, "invalid contract"
    )
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "review"
    assert result.pull_request_url == "https://github/pr/1"
    assert runner.calls == 0
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/1")]


def test_dry_run_has_no_mutations_or_dispatch() -> None:
    tracker = FakeTracker()
    runner = FakeRunner(WorkResult(True))
    result = AutomationService(tracker, runner).tick(AUTOMATION, dry_run=True)
    assert result.status == "would-dispatch"
    assert tracker.transitions == []
    assert runner.calls == 0


def test_claimed_task_pr_reconciles_before_invalid_contract_blocks() -> None:
    tracker = FakeTracker()
    tracker.pr = "https://github/pr/1"
    tracker.admit = lambda task: AdmissionResult(  # type: ignore[method-assign]
        False, task, AdmissionState.CONTRACT_INVALID, "invalid contract"
    )
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "review"
    assert result.pull_request_url == "https://github/pr/1"
    assert runner.calls == 0
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/1")]


def test_success_requires_pr_and_transitions_to_review() -> None:
    tracker = FakeTracker()
    tracker.pr = "https://github/pr/1"
    runner = FakeRunner(WorkResult(True))
    result = AutomationService(tracker, runner).tick(AUTOMATION)
    assert result.status == "review"
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/1")]


def test_successful_review_propagates_resumable_session_metadata() -> None:
    tracker = FakeTracker()
    pull_requests = iter(
        [
            PullRequestReconciliation(),
            PullRequestReconciliation(),
            PullRequestReconciliation("https://github/pr/1"),
        ]
    )
    tracker.reconcile_pull_requests = lambda task: next(pull_requests)  # type: ignore[method-assign]
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
    assert result.operator_detail == "private process detail"


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


@pytest.mark.parametrize(
    "state", [TaskState.READY, TaskState.RUNNING, TaskState.DEFERRED]
)
def test_preexisting_policy_violation_blocks_without_dispatch(state: TaskState) -> None:
    tracker = FakeTracker()
    task = replace(TASK, state=state)
    tracker.tasks = [task] if state is TaskState.READY else []
    tracker.deferred_tasks = [task] if state is TaskState.DEFERRED else []
    if state is TaskState.RUNNING:
        tracker.list_running = lambda: [task]  # type: ignore[method-assign]
    tracker.policy_violation = "Closing pull request is not open"
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "blocked"
    assert runner.calls == 0
    assert tracker.claims == []
    assert tracker.transitions == [
        (TaskState.BLOCKED, "Closing pull request is not open")
    ]


def test_accepted_open_draft_wins_when_violating_pr_also_exists() -> None:
    tracker = FakeTracker()
    tracker.pr = "https://github/pr/accepted"
    tracker.policy_violation = "Closing pull request is not a draft"
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.status == "review"
    assert result.pull_request_url == "https://github/pr/accepted"
    assert runner.calls == 0
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/accepted")]


@pytest.mark.parametrize(
    "detail",
    [
        "command timed out after 30 seconds: gh",
        "closing pull request query exceeded the supported page limit",
    ],
)
def test_indeterminate_reconciliation_does_not_dispatch_or_transition(
    detail: str,
) -> None:
    tracker = FakeTracker()
    tracker.reconciliation_error = detail
    runner = FakeRunner(WorkResult(True))

    with pytest.raises(RuntimeError, match=detail):
        AutomationService(tracker, runner).tick(AUTOMATION)

    assert runner.calls == 0
    assert tracker.transitions == []


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
    pull_requests = iter(
        [
            PullRequestReconciliation(),
            PullRequestReconciliation(),
            PullRequestReconciliation("https://github/pr/9"),
        ]
    )
    tracker.reconcile_pull_requests = lambda task: next(pull_requests)  # type: ignore[method-assign]
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
    running = replace(
        TASK,
        source_issue=GitHubIssueIdentity("source/queue", 8),
        state=TaskState.RUNNING,
    )
    deferred = replace(
        TASK,
        source_issue=GitHubIssueIdentity("source/queue", 9),
        state=TaskState.DEFERRED,
    )
    tracker.list_running = lambda: [running]  # type: ignore[method-assign]
    tracker.deferred_tasks = [deferred]
    tracker.pr = "https://github/pr/9"
    runner = FakeRunner(WorkResult(True))

    result = AutomationService(tracker, runner).tick(AUTOMATION)

    assert result.task is not None
    assert result.task.source_issue == running.source_issue
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
    pull_requests = iter(
        [
            PullRequestReconciliation(),
            PullRequestReconciliation("https://github/pr/9"),
        ]
    )
    tracker.reconcile_pull_requests = lambda task: next(pull_requests)  # type: ignore[method-assign]
    runner = FakeRunner(WorkResult(True, pull_request_url="https://github/pr/9"))
    result = AutomationService(tracker, runner).tick(AUTOMATION)
    assert result.status == "review"
    assert runner.calls == 1
    assert tracker.transitions == [(TaskState.REVIEW, "https://github/pr/9")]
