"""Protocols defining the ports for Groundskeeper adapters."""

from __future__ import annotations

from typing import Protocol

from groundskeeper.domain.automation import (
    AdmissionResult,
    AdmittedTask,
    AutomationTask,
    ClaimResult,
    PullRequestReconciliation,
    ReviewPullRequest,
    SessionMetadata,
    TaskState,
    WorkResult,
)
from groundskeeper.domain.models import RunContext, RunResult, Skill


class SkillStore(Protocol):
    """Loads skills from a source."""

    def list_skills(self) -> list[Skill]: ...

    def get_skill(self, name: str) -> Skill | None: ...


class AgentRunner(Protocol):
    """Executes a skill via an AI agent."""

    def run(self, context: RunContext) -> RunResult: ...

    def is_available(self) -> bool: ...


class CIProvider(Protocol):
    """Generates CI workflow files."""

    def generate_reusable_workflow(self) -> str: ...

    def generate_caller(
        self,
        skill_name: str,
        triggers: dict[str, list[str]],
        depends_on: list[str] | None = None,
    ) -> str: ...

    @property
    def workflow_directory(self) -> str: ...


class Tracker(Protocol):
    """Provider-neutral task ledger operations."""

    def list_ready(self) -> list[AutomationTask]: ...
    def list_running(self) -> list[AutomationTask]: ...
    def list_deferred(self) -> list[AutomationTask]: ...
    def list_review(self) -> list[AutomationTask]: ...
    def list_closed(self) -> list[AutomationTask]: ...
    def refresh(self, task: AutomationTask) -> AutomationTask: ...
    def admit(self, task: AutomationTask) -> AdmissionResult: ...
    def claim(self, task: AutomationTask) -> ClaimResult: ...
    def transition(
        self,
        task: AutomationTask,
        state: TaskState,
        detail: str = "",
        *,
        pull_request_url: str | None = None,
    ) -> None: ...
    def reconcile_pull_requests(
        self, task: AutomationTask
    ) -> PullRequestReconciliation: ...
    def reconcile_review(self, task: AutomationTask) -> ReviewPullRequest | None: ...
    def close(self, task: AutomationTask, *, merged: bool) -> None: ...


class AutomationRunner(Protocol):
    """Executes a normalized automation task."""

    def readiness(self, task: AutomationTask) -> str | None: ...
    def session_metadata(self, task: AutomationTask) -> SessionMetadata | None: ...
    def run(self, task: AdmittedTask, recovery: bool = False) -> WorkResult: ...
