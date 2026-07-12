"""Pi command client and supervised development runner."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import AutomationTask, WorkResult


class PiClient:
    """Typed facade over non-interactive Pi execution."""

    def __init__(self, process: ProcessClient) -> None:
        self._process = process

    def run_prompt(
        self, prompt: str, cwd: Path, session_id: str, name: str
    ) -> WorkResult:
        result = self._process.run(
            (
                "pi",
                "--session-id",
                session_id,
                "--name",
                name,
                "--approve",
                "-p",
                prompt,
            ),
            cwd,
        )
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", result.stdout)
        return WorkResult(
            success=result.success,
            output=result.stdout,
            error=result.stderr,
            exit_code=result.exit_code,
            pull_request_url=match.group(0) if match else None,
        )


class PiAutomationRunner:
    """Dispatches a normalized task through supervised-dev-cycle auto mode."""

    def __init__(self, client: PiClient, repositories: dict[str, Path]) -> None:
        self._client = client
        self._repositories = repositories

    def run(self, task: AutomationTask, recovery: bool = False) -> WorkResult:
        cwd = self._repositories.get(task.target_repository)
        if cwd is None:
            return WorkResult(
                success=False,
                error=f"No local path configured for {task.target_repository}",
                exit_code=2,
            )
        issue_number = task.external_id
        base = (
            "Load the supervised-dev-cycle skill and run it in auto mode. "
            "Implement the following trusted GitHub issue end to end, including "
            "an isolated worktree, validation, review, push, and a draft pull request. "
            f"Never merge. The pull request body must contain `Closes #{issue_number}`. "
            "Print the final pull request URL.\n\n"
            f"Repository: {task.target_repository}\n"
            f"Issue: {task.url}\nTitle: {task.title}\n\n{task.body}"
        )
        prompt = (
            "Resume this persisted factory run. Inspect existing session/worktree/PR state, "
            "continue from the last durable point, and honor the original task contract.\n\n"
            + base
            if recovery
            else base
        )
        identity = f"groundskeeper:{task.target_repository}:{task.external_id}"
        session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        name = f"gk-{task.target_repository.replace('/', '-')}-{task.external_id}"
        return self._client.run_prompt(prompt, cwd, session_id, name)
