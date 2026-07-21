"""Application service for one bounded automation tick."""

from __future__ import annotations

from dataclasses import replace

from groundskeeper.domain.automation import (
    AdmissionResult,
    AdmittedTask,
    AutomationTask,
    FailureDisposition,
    SessionMetadata,
    TaskState,
    TickResult,
    WorkResult,
)
from groundskeeper.domain.config import Automation
from groundskeeper.protocols import AutomationRunner, Tracker


def _require_admitted(admission: AdmissionResult) -> AdmittedTask:
    """Narrow one eligible admission to the runner-safe task type."""
    if not admission.eligible or admission.admitted_task is None:
        raise RuntimeError("eligible admission did not provide an admitted task")
    return admission.admitted_task


def _work_result_for_session(metadata: SessionMetadata | None) -> WorkResult:
    """Represent known session identity when no worker process result is available."""
    if metadata is None:
        return WorkResult(True)
    return WorkResult(
        True,
        session_id=metadata.session_id,
        session_name=metadata.session_name,
        resume_command=metadata.resume_command,
    )


def _result_detail(primary: str, result: WorkResult) -> str:
    """Combine a terminal result with resumable Pi session metadata."""
    lines = [primary]
    if result.session_id:
        lines.extend(("", f"Factory session: `{result.session_id}`"))
    if result.session_name:
        lines.append(f"Session name: `{result.session_name}`")
    if result.resume_command:
        lines.append(f"Resume: `{result.resume_command}`")
    return "\n".join(lines)


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
                if dry_run:
                    return TickResult(automation.name, "review", task, existing_pr)
                return self._review(
                    automation.name,
                    task,
                    existing_pr,
                    _work_result_for_session(self._runner.session_metadata(task)),
                )
            try:
                task = self._tracker.refresh(task)
            except RuntimeError as error:
                return TickResult(
                    automation.name, "not-claimed", task, detail=str(error)
                )
            admission = self._tracker.admit(task)
            if not admission.eligible:
                if dry_run:
                    return TickResult(
                        automation.name,
                        "would-block",
                        task,
                        detail=admission.reason,
                    )
                return self._block(automation.name, task, admission.reason)
            admitted = _require_admitted(admission)
            task = admitted.task
            if dry_run:
                return TickResult(automation.name, "would-resume", task)
            return self._finish(
                automation.name, task, self._runner.run(admitted, recovery=True)
            )
        deferred = self._tracker.list_deferred()
        if deferred:
            task = deferred[0]
            if dry_run:
                admission = self._tracker.admit(task)
                if not admission.eligible:
                    return TickResult(
                        automation.name,
                        "would-block",
                        task,
                        detail=admission.reason,
                    )
                return TickResult(automation.name, "would-resume", admission.task)
            return self._claim_and_run(automation.name, task, recovery=True)

        ready = self._tracker.list_ready()
        if not ready:
            return TickResult(automation.name, "no-work")
        task = ready[0]
        if dry_run:
            admission = self._tracker.admit(task)
            if not admission.eligible:
                return TickResult(
                    automation.name,
                    "would-block",
                    task,
                    detail=admission.reason,
                )
            return TickResult(automation.name, "would-dispatch", admission.task)
        return self._claim_and_run(automation.name, task, recovery=False)

    def _claim_and_run(
        self, name: str, task: AutomationTask, recovery: bool
    ) -> TickResult:
        """Atomically admit queued work before starting or resuming its session."""
        claim = self._tracker.claim(task)
        if not claim.claimed:
            return TickResult(name, "not-claimed", task, detail=claim.reason)
        try:
            existing_pr = self._tracker.find_pull_request(claim.task)
        except RuntimeError as error:
            return self._block(name, claim.task, str(error))
        if existing_pr:
            return self._review(
                name,
                claim.task,
                existing_pr,
                _work_result_for_session(self._runner.session_metadata(claim.task)),
            )
        admission = self._tracker.admit(claim.task)
        if not admission.eligible:
            return self._block(name, claim.task, admission.reason)
        admitted = _require_admitted(admission)
        admitted_task = admitted.task
        result = self._runner.run(admitted, recovery=recovery)
        return self._finish(name, admitted_task, result)

    def _finish(
        self, name: str, task: AutomationTask, result: WorkResult
    ) -> TickResult:
        """Reconcile durable GitHub state before trusting worker process status."""
        try:
            pr_url = self._tracker.find_pull_request(task)
            if pr_url:
                return self._review(name, task, pr_url, result)
            violation = self._tracker.find_policy_violation(task)
        except RuntimeError as error:
            return self._block(name, task, str(error), result)

        if violation is not None:
            return self._block(name, task, violation, result)
        if not result.success:
            detail = result.error.strip() or f"worker exited {result.exit_code}"
            if result.failure_disposition is FailureDisposition.DEFERRED:
                return self._defer(name, task, detail, result)
            return self._block(name, task, result.public_detail or detail, result)
        return self._block(
            name,
            task,
            result.public_detail
            or "Worker completed without an open draft pull request",
            result,
        )

    def _review(
        self,
        name: str,
        task: AutomationTask,
        pull_request_url: str,
        result: WorkResult,
    ) -> TickResult:
        """Record a review transition with available session handoff metadata."""
        detail = _result_detail(pull_request_url, result)
        self._tracker.transition(task, TaskState.REVIEW, detail)
        return TickResult(
            name,
            "review",
            replace(task, state=TaskState.REVIEW),
            pull_request_url,
            detail,
            result.session_id,
            result.session_name,
            result.resume_command,
        )

    def _defer(
        self,
        name: str,
        task: AutomationTask,
        detail: str,
        result: WorkResult,
    ) -> TickResult:
        """Return transient provider exhaustion to the resumable queue."""
        actionable = (
            "Transient provider exhaustion; Groundskeeper will resume this "
            f"deterministic session on the next tick.\n\n{detail}"
        )
        rendered = _result_detail(actionable, result)
        self._tracker.transition(task, TaskState.DEFERRED, rendered)
        return TickResult(
            name,
            "deferred",
            replace(task, state=TaskState.DEFERRED),
            detail=rendered,
            session_id=result.session_id,
            session_name=result.session_name,
            resume_command=result.resume_command,
        )

    def _block(
        self,
        name: str,
        task: AutomationTask,
        detail: str,
        result: WorkResult | None = None,
    ) -> TickResult:
        """Record an actionable terminal failure for a claimed or running task."""
        rendered = _result_detail(detail, result) if result is not None else detail
        self._tracker.transition(task, TaskState.BLOCKED, rendered)
        return TickResult(
            name,
            "blocked",
            replace(task, state=TaskState.BLOCKED),
            detail=rendered,
            session_id=result.session_id if result else None,
            session_name=result.session_name if result else None,
            resume_command=result.resume_command if result else None,
        )
