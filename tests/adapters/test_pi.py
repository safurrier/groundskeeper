from pathlib import Path

from groundskeeper.adapters.pi import PiAutomationRunner, PiClient
from groundskeeper.adapters.process import CommandResult
from groundskeeper.domain.automation import AutomationTask, WorkResult


class FakePiClient:
    def __init__(self) -> None:
        self.prompt = ""
        self.cwd = Path("/")
        self.session_id = ""
        self.name = ""

    def run_prompt(
        self, prompt: str, cwd: Path, session_id: str, name: str
    ) -> WorkResult:
        self.prompt, self.cwd = prompt, cwd
        self.session_id, self.name = session_id, name
        return WorkResult(True)


def test_pi_runner_builds_supervised_auto_task_packet() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github", "3", "Fix it", "Acceptance", "https://issue/3", "alex", "me/dots"
    )
    result = PiAutomationRunner(client, {"me/dots": Path("/repos/dots")}).run(task)
    assert result.success
    assert "supervised-dev-cycle skill" in client.prompt
    assert "auto mode" in client.prompt
    assert "Never merge" in client.prompt
    assert "Closes #3" in client.prompt
    assert "https://issue/3" in client.prompt
    assert client.cwd == Path("/repos/dots")
    assert client.session_id
    assert client.name == "gk-me-dots-3"


def test_recovery_reuses_deterministic_session_and_prompt() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github", "3", "Fix it", "Acceptance", "https://issue/3", "alex", "me/dots"
    )
    runner = PiAutomationRunner(client, {"me/dots": Path("/repos/dots")})
    runner.run(task)
    first_session = client.session_id
    runner.run(task, recovery=True)
    assert client.session_id == first_session
    assert "Resume this persisted factory run" in client.prompt


class FakeProcess:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] = ()

    def run(self, argv: tuple[str, ...], cwd: Path) -> CommandResult:
        self.argv = argv
        return CommandResult(argv, cwd, 0, "https://github.com/me/repo/pull/8", "")


def test_pi_client_passes_identity_approval_and_extracts_pr() -> None:
    process = FakeProcess()
    result = PiClient(process).run_prompt("task", Path("/repo"), "uuid", "run-name")  # type: ignore[arg-type]
    assert process.argv == (
        "pi",
        "--session-id",
        "uuid",
        "--name",
        "run-name",
        "--approve",
        "-p",
        "task",
    )
    assert result.pull_request_url == "https://github.com/me/repo/pull/8"
