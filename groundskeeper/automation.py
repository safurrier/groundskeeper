"""Application service for one bounded automation tick."""

from __future__ import annotations

from groundskeeper.domain.automation import (
    AutomationTask,
    TaskState,
    TickResult,
    WorkResult,
)
from groundskeeper.domain.config import Automation
from groundskeeper.protocols import AutomationRunner, Tracker


class AutomationService:
    def __init__(self, tracker: Tracker, runner: AutomationRunner) -> None:
        self._tracker = tracker
        self._runner = runner

    def tick(self, automation: Automation, dry_run: bool = False) -> TickResult:
        running = self._tracker.list_running()
        if running:
            task = running[0]
            try:
                existing_pr = self._tracker.find_pull_request(task)
            except RuntimeError as error:
                return self._block(automation.name, task, str(error))
            if existing_pr:
                if not dry_run:
                    self._tracker.transition(task, TaskState.REVIEW, existing_pr)
                return TickResult(automation.name, "review", task, existing_pr)
            if dry_run:
                return TickResult(automation.name, "would-resume", task)
            return self._finish(
                automation.name, task, self._runner.run(task, recovery=True)
            )
        tasks = self._tracker.list_ready()
        if not tasks:
            return TickResult(automation.name, "no-work")
        task = tasks[0]
        if dry_run:
            return TickResult(automation.name, "would-dispatch", task)
        claim = self._tracker.claim(task)
        if not claim.claimed:
            return TickResult(automation.name, "not-claimed", task, detail=claim.reason)
        try:
            existing_pr = self._tracker.find_pull_request(claim.task)
        except RuntimeError as error:
            return self._block(automation.name, claim.task, str(error))
        if existing_pr:
            self._tracker.transition(claim.task, TaskState.REVIEW, existing_pr)
            return TickResult(automation.name, "review", claim.task, existing_pr)
        result = self._runner.run(claim.task)
        return self._finish(automation.name, claim.task, result)

    def _finish(
        self, name: str, task: AutomationTask, result: WorkResult
    ) -> TickResult:
        if not result.success:
            detail = result.error.strip() or f"worker exited {result.exit_code}"
            return self._block(name, task, detail)
        try:
            pr_url = self._tracker.find_pull_request(task)
            if not pr_url:
                detail = self._tracker.find_policy_violation(task)
                if detail is None:
                    detail = "Worker completed without an open draft pull request"
                return self._block(name, task, detail)
        except RuntimeError as error:
            return self._block(name, task, str(error))
        self._tracker.transition(task, TaskState.REVIEW, pr_url)
        return TickResult(name, "review", task, pr_url)

    def _block(self, name: str, task: AutomationTask, detail: str) -> TickResult:
        """Record an actionable terminal failure for a claimed or running task."""
        self._tracker.transition(task, TaskState.BLOCKED, detail)
        return TickResult(name, "blocked", task, detail=detail)
