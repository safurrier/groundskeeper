"""Composition for a configured automation skill and the Pi process adapter."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from groundskeeper.adapters.pi import PiExecutionSettings
from groundskeeper.domain.automation import (
    AdmittedTask,
    AutomationTask,
    SessionMetadata,
    WorkResult,
)
from groundskeeper.domain.config import AutomationPolicy, PiRunnerConfig
from groundskeeper.domain.models import Skill


class PiPromptExecutor(Protocol):
    """Small semantic seam for executing an already-rendered Pi prompt."""

    def run_prompt(
        self, prompt: str, cwd: Path, settings: PiExecutionSettings
    ) -> WorkResult: ...


class AutomationSkillRenderer:
    """Adds normalized task context to a configured Groundskeeper skill."""

    def __init__(self, skill: Skill, policy: AutomationPolicy) -> None:
        self._skill = skill
        self._policy = policy

    def render(
        self,
        task: AdmittedTask,
        recovery: bool,
        settings: PiExecutionSettings,
    ) -> str:
        """Render a skill without changing ordinary skill rendering behavior."""
        return (
            f"{self._skill.render()}\n\n"
            f"{self._context(task, recovery, self._policy, settings)}"
        )

    @staticmethod
    def _context(
        task: AdmittedTask,
        recovery: bool,
        policy: AutomationPolicy,
        settings: PiExecutionSettings,
    ) -> str:
        recovery_context = (
            "Resume the deterministic session for this task and reconcile durable state."
            if recovery
            else "Start a new deterministic session for this task."
        )
        normalized = task.task
        contract = task.contract
        return "\n".join(
            (
                "AUTOMATION_CONTEXT",
                f"TASK_ID: {normalized.external_id}",
                f"TASK_TITLE: {normalized.title}",
                "TASK_BODY:",
                normalized.body,
                f"TASK_URL: {normalized.url}",
                f"REPOSITORY: {normalized.target_repository}",
                f"FACTORY_TASK_KIND: {contract.kind.value}",
                f"FACTORY_EXECUTION_MODE: {contract.mode.value if contract.mode else ''}",
                "FACTORY_DEPENDENCY_STATUS: resolved",
                f"POLICY_CONCURRENCY: {policy.concurrency}",
                f"POLICY_OUTPUT: {policy.output}",
                f"POLICY_MERGE: {policy.merge}",
                f"FACTORY_SESSION_ID: {settings.session_id}",
                f"FACTORY_SESSION_NAME: {settings.name}",
                f"FACTORY_RESUME_COMMAND: pi --session {settings.session_id}",
                f"RECOVERY_CONTEXT: {recovery_context}",
            )
        )


class PiAutomationRunner:
    """Runs a configured skill with stable Pi session identity."""

    def __init__(
        self,
        client: PiPromptExecutor,
        repository_path: Path,
        renderer: AutomationSkillRenderer,
        config: PiRunnerConfig,
    ) -> None:
        self._client = client
        self._repository_path = repository_path
        self._renderer = renderer
        self._config = config

    def _settings(self, task: AutomationTask) -> PiExecutionSettings:
        identity = f"groundskeeper:{task.target_repository}:{task.external_id}"
        return PiExecutionSettings(
            session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
            name=f"gk-{task.target_repository.replace('/', '-')}-{task.external_id}",
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

    def run(self, task: AdmittedTask, recovery: bool = False) -> WorkResult:
        settings = self._settings(task.task)
        return self._client.run_prompt(
            self._renderer.render(task, recovery, settings),
            self._repository_path,
            settings,
        )
