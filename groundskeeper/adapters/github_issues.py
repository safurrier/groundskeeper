"""GitHub Issues implementation of the tracker port."""

from __future__ import annotations

from dataclasses import replace

from groundskeeper.adapters.gh import GhClient, GhIssue
from groundskeeper.domain.automation import (
    AdmissionResult,
    AdmissionState,
    AdmittedTask,
    AutomationTask,
    ClaimResult,
    TaskState,
)
from groundskeeper.domain.config import GitHubIssuesSource
from groundskeeper.domain.task_contract import (
    DependencyState,
    FactoryTaskKind,
    TaskContractError,
    parse_factory_task,
)


class GitHubIssuesTracker:
    def __init__(self, client: GhClient, source: GitHubIssuesSource) -> None:
        self._client = client
        self._source = source

    def list_ready(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.ready_label, TaskState.READY)

    def list_running(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.running_label, TaskState.RUNNING)

    def list_deferred(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.deferred_label, TaskState.DEFERRED)

    def _list_in_state(self, label: str, state: TaskState) -> list[AutomationTask]:
        issues = self._client.list_issues(self._source.repository, (label,))
        return [
            self._task_from_issue(issue, state)
            for issue in issues
            if issue.author in self._source.trusted_authors
        ]

    def _task_from_issue(self, issue: GhIssue, state: TaskState) -> AutomationTask:
        return AutomationTask(
            provider="github-issues",
            external_id=str(issue.number),
            title=issue.title,
            body=issue.body,
            url=issue.url,
            author=issue.author,
            target_repository=self._source.repository,
            state=state,
        )

    def refresh(self, task: AutomationTask) -> AutomationTask:
        """Read the current task body immediately before admission and execution."""
        issue = self._client.get_issue(self._source.repository, int(task.external_id))
        if issue.author not in self._source.trusted_authors:
            raise RuntimeError("task author is no longer trusted")
        return self._task_from_issue(issue, task.state)

    def admit(self, task: AutomationTask) -> AdmissionResult:
        """Parse and resolve the clean-break task contract before runner use."""
        try:
            contract = parse_factory_task(task.body)
        except TaskContractError as error:
            return AdmissionResult(
                False, task, AdmissionState.CONTRACT_INVALID, str(error)
            )
        contracted_task = replace(task, contract=contract)
        if contract.kind is FactoryTaskKind.TRACKING:
            return AdmissionResult(
                False,
                contracted_task,
                AdmissionState.TRACKING,
                "Tracking issue is not runnable",
            )
        for dependency in contract.dependencies:
            state, reason = self._client.dependency_state(dependency)
            if state is not DependencyState.COMPLETE:
                admission_state = {
                    DependencyState.UNRESOLVED: AdmissionState.DEPENDENCY_UNRESOLVED,
                    DependencyState.INVALID: AdmissionState.DEPENDENCY_INVALID,
                    DependencyState.INACCESSIBLE: AdmissionState.DEPENDENCY_INACCESSIBLE,
                }[state]
                return AdmissionResult(False, contracted_task, admission_state, reason)
        return AdmissionResult(
            True,
            contracted_task,
            AdmissionState.ADMITTED,
            admitted_task=AdmittedTask(contracted_task, contract),
        )

    def claim(self, task: AutomationTask) -> ClaimResult:
        # Re-read the selected queue immediately before the single label mutation.
        # This makes retries safe and narrows the race between scheduled ticks.
        queues = {
            TaskState.READY: (self.list_ready, self._source.ready_label),
            TaskState.DEFERRED: (self.list_deferred, self._source.deferred_label),
        }
        queue = queues.get(task.state)
        if queue is None:
            return ClaimResult(
                False, task, f"task cannot be claimed from {task.state.value}"
            )
        list_tasks, source_label = queue
        current = {item.external_id: item for item in list_tasks()}
        current_task = current.get(task.external_id)
        if current_task is None:
            return ClaimResult(False, task, f"task is no longer {task.state.value}")
        self._client.replace_label(
            self._source.repository,
            int(task.external_id),
            source_label,
            self._source.running_label,
        )
        return ClaimResult(
            True,
            self.refresh(replace(current_task, state=TaskState.RUNNING)),
        )

    def transition(
        self, task: AutomationTask, state: TaskState, detail: str = ""
    ) -> None:
        target = {
            TaskState.REVIEW: self._source.review_label,
            TaskState.BLOCKED: self._source.blocked_label,
            TaskState.DEFERRED: self._source.deferred_label,
            TaskState.RUNNING: self._source.running_label,
            TaskState.READY: self._source.ready_label,
        }[state]
        old = {
            TaskState.READY: self._source.ready_label,
            TaskState.RUNNING: self._source.running_label,
            TaskState.DEFERRED: self._source.deferred_label,
            TaskState.REVIEW: self._source.review_label,
            TaskState.BLOCKED: self._source.blocked_label,
        }[task.state]
        self._client.replace_label(
            self._source.repository, int(task.external_id), old, target
        )
        if detail:
            self._client.comment(
                self._source.repository,
                int(task.external_id),
                f"AI-authored factory update: {detail}",
            )

    def find_pull_request(self, task: AutomationTask) -> str | None:
        return self._client.linked_pull_request(
            self._source.repository, int(task.external_id)
        )

    def find_policy_violation(self, task: AutomationTask) -> str | None:
        return self._client.closing_pr_policy_violation(
            self._source.repository, int(task.external_id)
        )
