"""GitHub Issues implementation of the tracker port."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Literal

from groundskeeper.adapters.gh import GhClient, GhIssue
from groundskeeper.domain.automation import (
    AdmissionResult,
    AdmissionState,
    AdmittedTask,
    AutomationTask,
    ClaimResult,
    GitHubIssueIdentity,
    PullRequestReconciliation,
    ReviewPullRequest,
    TaskState,
    automation_task_branch,
)
from groundskeeper.domain.config import (
    AutomationCheckout,
    AutomationPolicy,
    GitHubIssuesSource,
)
from groundskeeper.domain.task_contract import (
    DependencyState,
    FactoryTaskKind,
    TaskContractError,
    parse_factory_task,
)


class GitHubIssuesTracker:
    def __init__(
        self,
        client: GhClient,
        source: GitHubIssuesSource,
        target_repository: str,
        policy: AutomationPolicy,
        checkout: AutomationCheckout,
    ) -> None:
        self._client = client
        self._source = source
        self._target_repository = target_repository
        self._policy = policy
        self._checkout = checkout

    def list_ready(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.ready_label, TaskState.READY)

    def list_running(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.running_label, TaskState.RUNNING)

    def list_deferred(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.deferred_label, TaskState.DEFERRED)

    def list_review(self) -> list[AutomationTask]:
        return self._list_in_state(
            self._source.review_label, TaskState.REVIEW, issue_state="all"
        )

    def list_closed(self) -> list[AutomationTask]:
        return self._list_in_state(self._source.closed_label, TaskState.CLOSED)

    def _list_in_state(
        self,
        label: str,
        state: TaskState,
        *,
        issue_state: Literal["open", "closed", "all"] = "open",
    ) -> list[AutomationTask]:
        issues = self._client.list_issues(
            self._source.repository, (label,), state=issue_state
        )
        return [
            self._task_from_issue(issue, state)
            for issue in issues
            if issue.author in self._source.trusted_authors
        ]

    def _task_from_issue(self, issue: GhIssue, state: TaskState) -> AutomationTask:
        return AutomationTask(
            provider="github-issues",
            title=issue.title,
            body=issue.body,
            url=issue.url,
            author=issue.author,
            source_issue=GitHubIssueIdentity(self._source.repository, issue.number),
            target_repository=self._target_repository,
            state=state,
            review_pull_request_url=self._review_pull_request_url(issue),
        )

    def _review_pull_request_url(self, issue: GhIssue) -> str | None:
        """Recover the exact PR accepted by Groundskeeper's review comment."""
        target_pull_request = re.compile(
            rf"https://(?P<host>github\.com|redirect\.github\.com)/"
            rf"{re.escape(self._target_repository)}/pull/(?P<number>[1-9]\d*)",
            re.IGNORECASE,
        )
        prefix = "AI-authored factory update:"
        trusted = {author.casefold() for author in self._source.trusted_authors}
        for comment in reversed(issue.comments):
            if comment.author.casefold() not in trusted or not comment.body.startswith(
                prefix
            ):
                continue
            matches = list(target_pull_request.finditer(comment.body))
            if len(matches) == 1:
                return (
                    f"https://github.com/{self._target_repository}/pull/"
                    f"{matches[0].group('number')}"
                )
        return None

    def refresh(self, task: AutomationTask) -> AutomationTask:
        """Read and verify the current task immediately before execution."""
        issue = self._client.get_issue(
            self._source.repository, task.source_issue.number
        )
        if issue.author not in self._source.trusted_authors:
            raise RuntimeError("task author is no longer trusted")
        if issue.state != "open":
            raise RuntimeError("task issue is no longer open")
        expected_label = {
            TaskState.READY: self._source.ready_label,
            TaskState.RUNNING: self._source.running_label,
            TaskState.DEFERRED: self._source.deferred_label,
            TaskState.REVIEW: self._source.review_label,
            TaskState.BLOCKED: self._source.blocked_label,
            TaskState.CLOSED: self._source.closed_label,
        }[task.state]
        if expected_label not in issue.labels:
            raise RuntimeError(f"task no longer has lifecycle label {expected_label}")
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
        current = {item.source_issue.number: item for item in list_tasks()}
        current_task = current.get(task.source_issue.number)
        if current_task is None:
            return ClaimResult(False, task, f"task is no longer {task.state.value}")
        self._client.replace_label(
            self._source.repository,
            task.source_issue.number,
            source_label,
            self._source.running_label,
        )
        try:
            refreshed = self.refresh(replace(current_task, state=TaskState.RUNNING))
        except RuntimeError as error:
            return ClaimResult(False, current_task, str(error))
        return ClaimResult(True, refreshed)

    def transition(
        self,
        task: AutomationTask,
        state: TaskState,
        detail: str = "",
        *,
        pull_request_url: str | None = None,
    ) -> None:
        if state is TaskState.CLOSED or task.state is TaskState.CLOSED:
            raise ValueError("closed lifecycle transitions require close()")
        rendered_detail = self._source_comment_detail(
            state,
            detail,
            pull_request_url=pull_request_url,
        )
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
        if state is TaskState.REVIEW and rendered_detail:
            self._client.comment(
                self._source.repository,
                task.source_issue.number,
                f"AI-authored factory update: {rendered_detail}",
            )
        self._client.replace_label(
            self._source.repository, task.source_issue.number, old, target
        )
        if rendered_detail and state is not TaskState.REVIEW:
            self._client.comment(
                self._source.repository,
                task.source_issue.number,
                f"AI-authored factory update: {rendered_detail}",
            )

    def _source_comment_detail(
        self,
        state: TaskState,
        detail: str,
        *,
        pull_request_url: str | None,
    ) -> str:
        """Render a clickable private result without creating a target backlink."""
        if self._policy.link_source_issue:
            return detail
        target_pull_request = re.compile(
            rf"https://github\.com/{re.escape(self._target_repository)}/pull/"
            r"(?P<number>[1-9]\d*)",
            re.IGNORECASE,
        )
        if state is TaskState.REVIEW:
            typed_match = (
                target_pull_request.fullmatch(pull_request_url)
                if pull_request_url is not None
                else None
            )
            detail_matches = list(target_pull_request.finditer(detail))
            if (
                typed_match is None
                or len(detail_matches) != 1
                or detail_matches[0].group("number") != typed_match.group("number")
            ):
                raise RuntimeError(
                    "private review transition requires an exact target pull request URL"
                )
        return target_pull_request.sub(
            lambda match: (
                f"https://redirect.github.com/{self._target_repository}/pull/"
                f"{match.group('number')}"
            ),
            detail,
        )

    def reconcile_pull_requests(
        self, task: AutomationTask
    ) -> PullRequestReconciliation:
        if not self._policy.link_source_issue:
            return self._client.reconcile_branch_pull_requests(
                task.target_repository,
                automation_task_branch(task, self._checkout.branch_prefix),
            )
        return self._client.reconcile_pull_requests(
            task.target_repository, task.source_issue
        )

    def reconcile_review(self, task: AutomationTask) -> ReviewPullRequest | None:
        """Read the exact review PR without reapplying creation-time draft policy."""
        if task.review_pull_request_url is None:
            return None
        if not self._policy.link_source_issue:
            return self._client.review_branch_pull_request(
                task.target_repository,
                automation_task_branch(task, self._checkout.branch_prefix),
                task.review_pull_request_url,
            )
        return self._client.review_pull_request(
            task.target_repository,
            task.source_issue,
            task.review_pull_request_url,
        )

    def close(self, task: AutomationTask, *, merged: bool) -> None:
        """Idempotently retain the terminal label and close the source issue."""
        issue = self._client.get_issue(
            self._source.repository, task.source_issue.number
        )
        if issue.author not in self._source.trusted_authors:
            raise RuntimeError("task author is no longer trusted")
        if self._source.review_label in issue.labels:
            self._client.replace_label(
                self._source.repository,
                task.source_issue.number,
                self._source.review_label,
                self._source.closed_label,
            )
            issue = self._client.get_issue(
                self._source.repository, task.source_issue.number
            )
        elif self._source.closed_label not in issue.labels:
            raise RuntimeError(
                "task no longer has lifecycle label "
                f"{self._source.review_label} or {self._source.closed_label}"
            )
        if issue.state == "open":
            self._client.close_issue(
                self._source.repository,
                task.source_issue.number,
                merged=merged,
            )
