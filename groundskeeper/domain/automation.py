"""Domain models for declarative outer-loop automations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from groundskeeper.domain.task_contract import FactoryTaskContract

_GITHUB_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+"
)


def canonical_github_repository(value: str) -> str:
    """Validate and return one canonical GitHub owner/repo identity."""
    if not _GITHUB_REPOSITORY_RE.fullmatch(value):
        raise ValueError("GitHub repository identity must be canonical owner/repo")
    return value


class TaskState(str, Enum):
    """Provider-neutral task lifecycle."""

    READY = "ready"
    RUNNING = "running"
    DEFERRED = "deferred"
    REVIEW = "review"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GitHubIssueIdentity:
    """Repository-qualified identity of one source queue issue."""

    repository: str
    number: int

    def __post_init__(self) -> None:
        canonical_github_repository(self.repository)
        if isinstance(self.number, bool) or self.number <= 0:
            raise ValueError("GitHub issue number must be positive")

    def closing_reference(self, target_repository: str) -> str:
        """Render the GitHub closing syntax required in the target pull request."""
        canonical_github_repository(target_repository)
        if self.repository.casefold() == target_repository.casefold():
            return f"#{self.number}"
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True)
class PullRequestReconciliation:
    """One coherent source-issue closing-PR snapshot in the target repository."""

    accepted_url: str | None = None
    policy_violation: str | None = None


@dataclass(frozen=True)
class AutomationTask:
    """A unit of work normalized from an external tracker."""

    provider: str
    title: str
    body: str
    url: str
    author: str
    source_issue: GitHubIssueIdentity
    target_repository: str
    state: TaskState = TaskState.READY
    contract: FactoryTaskContract | None = None


def automation_task_identity(task: AutomationTask) -> str:
    """Return the stable identity shared by sessions and task worktrees."""
    return (
        f"groundskeeper:{task.source_issue.repository.casefold()}:"
        f"{task.source_issue.number}:{task.target_repository.casefold()}"
    )


class AdmissionState(str, Enum):
    """Machine-readable reason that one task may or may not run."""

    ADMITTED = "admitted"
    CONTRACT_INVALID = "contract-invalid"
    TRACKING = "tracking"
    DEPENDENCY_UNRESOLVED = "dependency-unresolved"
    DEPENDENCY_INVALID = "dependency-invalid"
    DEPENDENCY_INACCESSIBLE = "dependency-inaccessible"


@dataclass(frozen=True)
class AdmittedTask:
    """A normalized task whose contract and dependencies passed admission."""

    task: AutomationTask
    contract: FactoryTaskContract

    def __post_init__(self) -> None:
        if self.task.contract != self.contract:
            raise ValueError("admitted task requires its normalized contract")


@dataclass(frozen=True)
class AdmissionResult:
    """Deterministic result of checking one task before claim or recovery."""

    eligible: bool
    task: AutomationTask
    state: AdmissionState
    reason: str = ""
    admitted_task: AdmittedTask | None = None

    def __post_init__(self) -> None:
        if self.eligible:
            if self.state is not AdmissionState.ADMITTED or self.admitted_task is None:
                raise ValueError("eligible admission requires an admitted task")
            if self.admitted_task.task != self.task:
                raise ValueError("admitted task must wrap the admission task")
            if self.task.contract != self.admitted_task.contract:
                raise ValueError(
                    "admitted task contract must match the normalized task"
                )
        elif self.state is AdmissionState.ADMITTED or self.admitted_task is not None:
            raise ValueError("rejected admission cannot carry an admitted task")


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of attempting to exclusively claim a task."""

    claimed: bool
    task: AutomationTask
    reason: str = ""


@dataclass(frozen=True)
class SessionMetadata:
    """Stable identity needed to inspect or resume an automation session."""

    session_id: str
    session_name: str
    resume_command: str


class FailureDisposition(str, Enum):
    """Typed lifecycle disposition for an unsuccessful worker result."""

    BLOCKED = "blocked"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class WorkResult:
    """Result returned by an automation worker."""

    success: bool
    output: str = ""
    error: str = ""
    exit_code: int = 0
    pull_request_url: str | None = None
    session_id: str | None = None
    session_name: str | None = None
    resume_command: str | None = None
    public_detail: str | None = None
    failure_disposition: FailureDisposition = FailureDisposition.BLOCKED


@dataclass(frozen=True)
class TickResult:
    """Structured result of one bounded reconciliation tick."""

    automation: str
    status: str
    task: AutomationTask | None = None
    pull_request_url: str | None = None
    detail: str = ""
    session_id: str | None = None
    session_name: str | None = None
    resume_command: str | None = None
    operator_detail: str | None = None
