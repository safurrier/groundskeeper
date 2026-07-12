from pathlib import Path

from groundskeeper.adapters.automation_runner import (
    AutomationSkillRenderer,
    PiAutomationRunner,
)
from groundskeeper.adapters.pi import PiClient, PiExecutionSettings
from groundskeeper.adapters.process import CommandResult
from groundskeeper.domain.automation import AutomationTask, WorkResult
from groundskeeper.domain.config import AutomationPolicy, PiRunnerConfig
from groundskeeper.domain.models import Skill, SkillSource


class FakePiClient:
    def __init__(self) -> None:
        self.prompt = ""
        self.cwd = Path("/")
        self.settings: PiExecutionSettings | None = None

    def run_prompt(
        self, prompt: str, cwd: Path, settings: PiExecutionSettings
    ) -> WorkResult:
        self.prompt, self.cwd, self.settings = prompt, cwd, settings
        return WorkResult(True)


def _skill() -> Skill:
    return Skill(
        "issue-implementation",
        "Implement an issue",
        "Implement the task below.",
        SkillSource("local", Path("/skills/issue-implementation")),
    )


def _runner(client: FakePiClient) -> PiAutomationRunner:
    return PiAutomationRunner(
        client,
        Path("/repos/dots"),
        AutomationSkillRenderer(_skill(), AutomationPolicy()),
        PiRunnerConfig(skill="issue-implementation"),
    )


def test_pi_runner_renders_normalized_task_context_with_typed_settings() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github", "3", "Fix it", "Acceptance", "https://issue/3", "alex", "me/dots"
    )
    result = _runner(client).run(task)
    assert result.success
    assert "Implement the task below." in client.prompt
    assert "TASK_ID: 3" in client.prompt
    assert "TASK_TITLE: Fix it" in client.prompt
    assert "TASK_BODY:\nAcceptance" in client.prompt
    assert "TASK_URL: https://issue/3" in client.prompt
    assert "REPOSITORY: me/dots" in client.prompt
    assert "RECOVERY_CONTEXT: Start a new deterministic session" in client.prompt
    assert client.cwd == Path("/repos/dots")
    assert client.settings is not None
    assert client.settings.session_id
    assert client.settings.name == "gk-me-dots-3"
    assert client.settings.approval == "allow"
    assert client.settings.timeout_seconds == 7200
    assert "POLICY_OUTPUT: draft-pr" in client.prompt
    assert "POLICY_MERGE: never" in client.prompt


def test_recovery_reuses_deterministic_session_and_changes_only_context() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github", "3", "Fix it", "Acceptance", "https://issue/3", "alex", "me/dots"
    )
    runner = _runner(client)
    runner.run(task)
    first_session = client.settings.session_id if client.settings else ""
    runner.run(task, recovery=True)
    assert client.settings is not None
    assert client.settings.session_id == first_session
    assert "RECOVERY_CONTEXT: Resume the deterministic session" in client.prompt


class FakeProcess:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] = ()
        self.timeout: int | None = None

    def run(
        self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
    ) -> CommandResult:
        self.argv, self.timeout = argv, timeout
        return CommandResult(argv, cwd, 0, "https://github.com/me/repo/pull/8", "")


def test_pi_client_receives_only_rendered_prompt_and_typed_settings() -> None:
    process = FakeProcess()
    settings = PiExecutionSettings("uuid", "run-name")
    result = PiClient(process).run_prompt("rendered task", Path("/repo"), settings)  # type: ignore[arg-type]
    assert process.argv == (
        "pi",
        "--session-id",
        "uuid",
        "--name",
        "run-name",
        "--approve",
        "-p",
        "rendered task",
    )
    assert result.pull_request_url == "https://github.com/me/repo/pull/8"
    assert process.timeout == 7200
