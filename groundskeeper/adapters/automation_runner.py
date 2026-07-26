"""Composition for a configured automation skill and the Pi process adapter."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from groundskeeper.adapters.pi import PiExecutionSettings
from groundskeeper.adapters.target_checkout import (
    TargetCheckoutError,
    TargetWorkspace,
)
from groundskeeper.domain.automation import (
    AdmittedTask,
    AutomationTask,
    SessionMetadata,
    WorkResult,
    automation_task_identity,
)
from groundskeeper.domain.config import (
    AutomationCheckout,
    AutomationPolicy,
    PiRunnerConfig,
)
from groundskeeper.domain.models import Skill


class PiPromptExecutor(Protocol):
    """Small semantic seam for executing an already-rendered Pi prompt."""

    def run_prompt(
        self, prompt: str, cwd: Path, settings: PiExecutionSettings
    ) -> WorkResult: ...


class TargetWorkspaceProvider(Protocol):
    """Prepare the deterministic workspace for one admitted task."""

    def readiness_for(self, task: AutomationTask) -> str | None: ...
    def workspace_for(self, task: AutomationTask) -> TargetWorkspace: ...


class AutomationSkillRenderer:
    """Adds normalized task context to a configured Groundskeeper skill."""

    def __init__(
        self,
        skill: Skill,
        policy: AutomationPolicy,
        checkout: AutomationCheckout,
    ) -> None:
        self._skill = skill
        self._policy = policy
        self._checkout = checkout

    def render(
        self,
        task: AdmittedTask,
        recovery: bool,
        settings: PiExecutionSettings,
        workspace: TargetWorkspace,
    ) -> str:
        """Render a skill without changing ordinary skill rendering behavior."""
        return (
            f"{self._skill.render()}\n\n"
            f"{self._context(task, recovery, self._policy, self._checkout, workspace, settings)}"
        )

    @staticmethod
    def _context(
        task: AdmittedTask,
        recovery: bool,
        policy: AutomationPolicy,
        checkout: AutomationCheckout,
        workspace: TargetWorkspace,
        settings: PiExecutionSettings,
    ) -> str:
        recovery_context = (
            "Resume the deterministic session for this task and reconcile durable state."
            if recovery
            else "Start a new deterministic session for this task."
        )
        normalized = task.task
        contract = task.contract
        context = [
            "AUTOMATION_CONTEXT",
            f"TASK_ID: {normalized.source_issue.number}",
            f"TASK_TITLE: {normalized.title}",
            "TASK_BODY:",
            normalized.body,
            f"TASK_URL: {normalized.url}",
            f"REPOSITORY: {normalized.target_repository}",
            f"POLICY_LINK_SOURCE_ISSUE: {str(policy.link_source_issue).lower()}",
            "POLICY_INCLUDE_FACTORY_SESSION: "
            f"{str(policy.include_factory_session).lower()}",
            f"POLICY_INCLUDE_PI_RESUME: {str(policy.include_pi_resume).lower()}",
        ]
        if policy.link_source_issue:
            context.extend(
                (
                    "FACTORY_CLOSING_REFERENCE: "
                    f"{normalized.source_issue.closing_reference(normalized.target_repository)}",
                )
            )
        context.extend(
            (
                f"FACTORY_TASK_KIND: {contract.kind.value}",
                f"FACTORY_EXECUTION_MODE: {contract.mode.value if contract.mode else ''}",
                "FACTORY_DEPENDENCY_STATUS: resolved",
                f"TARGET_CHECKOUT_MODE: {checkout.mode}",
                f"TARGET_BASE_REF: {checkout.base_ref or ''}",
                f"TARGET_BASE_SHA: {workspace.base_sha or ''}",
                f"TARGET_REFRESH: {checkout.refresh}",
                f"TARGET_WORKSPACE_PATH: {workspace.path}",
                f"POLICY_CONCURRENCY: {policy.concurrency}",
                f"POLICY_OUTPUT: {policy.output}",
                f"POLICY_MERGE: {policy.merge}",
                f"FACTORY_SESSION_ID: {settings.session_id}",
                f"FACTORY_SESSION_NAME: {settings.name}",
                f"FACTORY_RESUME_COMMAND: pi --session {settings.session_id}",
                f"RECOVERY_CONTEXT: {recovery_context}",
            )
        )
        return "\n".join(context)


class PiAutomationRunner:
    """Runs a configured skill with stable Pi session identity."""

    def __init__(
        self,
        client: PiPromptExecutor,
        checkout_manager: TargetWorkspaceProvider,
        renderer: AutomationSkillRenderer,
        config: PiRunnerConfig,
    ) -> None:
        self._client = client
        self._checkout_manager = checkout_manager
        self._renderer = renderer
        self._config = config

    def _settings(self, task: AutomationTask) -> PiExecutionSettings:
        source_repository = task.source_issue.repository.casefold()
        target_repository = task.target_repository.casefold()
        identity = automation_task_identity(task)
        return PiExecutionSettings(
            session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
            name=(
                f"gk-{source_repository.replace('/', '-')}-{task.source_issue.number}"
                f"-to-{target_repository.replace('/', '-')}"
            ),
            approval=self._config.approval,
            timeout_seconds=self._config.timeout_seconds,
        )

    def session_metadata(self, task: AutomationTask) -> SessionMetadata:
        """Return stable session identity without starting or resuming Pi."""
        settings = self._settings(task)
        return SessionMetadata(
            settings.session_id,
            settings.name,
            f"pi --session {settings.session_id}",
        )

    def readiness(self, task: AutomationTask) -> str | None:
        """Return a nonmutating checkout blocker before tracker claim."""
        return self._checkout_manager.readiness_for(task)

    def run(self, task: AdmittedTask, recovery: bool = False) -> WorkResult:
        settings = self._settings(task.task)
        try:
            workspace = self._checkout_manager.workspace_for(task.task)
        except TargetCheckoutError as error:
            return WorkResult(
                False,
                error=str(error),
                exit_code=2,
                public_detail="Target workspace preparation failed before worker launch",
            )
        return self._client.run_prompt(
            self._renderer.render(task, recovery, settings, workspace),
            workspace.path,
            settings,
        )
