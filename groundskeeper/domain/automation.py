"""Domain models for declarative outer-loop automations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskState(str, Enum):
    """Provider-neutral task lifecycle."""

    READY = "ready"
    RUNNING = "running"
    DEFERRED = "deferred"
    REVIEW = "review"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AutomationTask:
    """A unit of work normalized from an external tracker."""

    provider: str
    external_id: str
    title: str
    body: str
    url: str
    author: str
    target_repository: str
    state: TaskState = TaskState.READY


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
