"""Composition for a configured automation skill and the Pi process adapter."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from groundskeeper.adapters.pi import PiExecutionSettings
from groundskeeper.domain.automation import AutomationTask, WorkResult
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

    def render(self, task: AutomationTask, recovery: bool) -> str:
        """Render a skill without changing ordinary skill rendering behavior."""
        return (
            f"{self._skill.render()}\n\n{self._context(task, recovery, self._policy)}"
        )

    @staticmethod
    def _context(task: AutomationTask, recovery: bool, policy: AutomationPolicy) -> str:
        recovery_context = (
            "Resume the deterministic session for this task and reconcile durable state."
            if recovery
            else "Start a new deterministic session for this task."
        )
        return "\n".join(
            (
                "AUTOMATION_CONTEXT",
                f"TASK_ID: {task.external_id}",
                f"TASK_TITLE: {task.title}",
                "TASK_BODY:",
                task.body,
                f"TASK_URL: {task.url}",
                f"REPOSITORY: {task.target_repository}",
                f"POLICY_CONCURRENCY: {policy.concurrency}",
                f"POLICY_OUTPUT: {policy.output}",
                f"POLICY_MERGE: {policy.merge}",
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

    def run(self, task: AutomationTask, recovery: bool = False) -> WorkResult:
        identity = f"groundskeeper:{task.target_repository}:{task.external_id}"
        settings = PiExecutionSettings(
            session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
            name=f"gk-{task.target_repository.replace('/', '-')}-{task.external_id}",
            approval=self._config.approval,
            timeout_seconds=self._config.timeout_seconds,
        )
        return self._client.run_prompt(
            self._renderer.render(task, recovery), self._repository_path, settings
        )
