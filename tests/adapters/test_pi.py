import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from groundskeeper.adapters.automation_runner import (
    AutomationSkillRenderer,
    PiAutomationRunner,
)
from groundskeeper.adapters.pi import (
    FACTORY_PUBLIC_DETAIL_MAX_CHARS,
    PiClient,
    PiExecutionSettings,
    _failure_disposition,
    _public_detail,
)
from groundskeeper.adapters.process import (
    PROCESS_ERROR_TAIL_CHARS,
    CommandResult,
    ProcessClient,
)
from groundskeeper.adapters.target_checkout import (
    TargetCheckoutError,
    TargetCheckoutManager,
    TargetWorkspace,
)
from groundskeeper.domain.automation import (
    AdmittedTask,
    AutomationTask,
    FailureDisposition,
    GitHubIssueIdentity,
    WorkResult,
)
from groundskeeper.domain.config import (
    AutomationCheckout,
    AutomationPolicy,
    PiRunnerConfig,
)
from groundskeeper.domain.models import Skill, SkillSource
from groundskeeper.domain.task_contract import (
    ExecutionMode,
    FactoryTaskContract,
    FactoryTaskKind,
)


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


class FailingCheckout:
    def readiness_for(self, task: AutomationTask) -> str | None:
        return None

    def workspace_for(self, task: AutomationTask) -> TargetWorkspace:
        raise TargetCheckoutError("local checkout detail")


def _skill() -> Skill:
    return Skill(
        "issue-implementation",
        "Implement an issue",
        "Implement the task below.",
        SkillSource("local", Path("/skills/issue-implementation")),
    )


def _contract() -> FactoryTaskContract:
    return FactoryTaskContract(1, FactoryTaskKind.RUNNABLE, ExecutionMode.FULL, ())


def _admitted(task: AutomationTask) -> AdmittedTask:
    contract = task.contract
    if contract is None:
        raise ValueError("test task requires a contract")
    return AdmittedTask(task, contract)


def _runner(client: FakePiClient) -> PiAutomationRunner:
    return PiAutomationRunner(
        client,
        TargetCheckoutManager(
            ProcessClient(),
            Path("/repos/dots"),
            "target/repo",
            AutomationCheckout(),
            None,
            Path("/state"),
        ),
        AutomationSkillRenderer(_skill(), AutomationPolicy(), AutomationCheckout()),
        PiRunnerConfig(skill="issue-implementation"),
    )


def test_pi_runner_renders_normalized_task_context_with_typed_settings() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
        contract=_contract(),
    )
    result = _runner(client).run(_admitted(task))
    assert result.success
    assert "Implement the task below." in client.prompt
    assert "TASK_ID: 3" in client.prompt
    assert "TASK_TITLE: Fix it" in client.prompt
    assert "TASK_BODY:\nAcceptance" in client.prompt
    assert "TASK_URL: https://issue/3" in client.prompt
    assert "REPOSITORY: target/repo" in client.prompt
    assert "FACTORY_CLOSING_REFERENCE: source/queue#3" in client.prompt
    assert "FACTORY_TASK_KIND: runnable" in client.prompt
    assert "FACTORY_EXECUTION_MODE: full" in client.prompt
    assert "FACTORY_DEPENDENCY_STATUS: resolved" in client.prompt
    assert "TARGET_CHECKOUT_MODE: existing" in client.prompt
    assert "TARGET_BASE_REF: " in client.prompt
    assert "TARGET_BASE_SHA: " in client.prompt
    assert "TARGET_REFRESH: none" in client.prompt
    assert "TARGET_WORKSPACE_PATH: /repos/dots" in client.prompt
    assert "RECOVERY_CONTEXT: Start a new deterministic session" in client.prompt
    assert client.cwd == Path("/repos/dots")
    assert client.settings is not None
    assert client.settings.session_id
    assert client.settings.name == "gk-source-queue-3-to-target-repo"
    assert client.settings.approval == "allow"
    assert client.settings.timeout_seconds == 7200
    assert "POLICY_OUTPUT: draft-pr" in client.prompt
    assert "POLICY_MERGE: never" in client.prompt
    assert "POLICY_LINK_SOURCE_ISSUE: true" in client.prompt
    assert "POLICY_INCLUDE_FACTORY_SESSION: true" in client.prompt
    assert "POLICY_INCLUDE_PI_RESUME: true" in client.prompt
    assert f"FACTORY_SESSION_ID: {client.settings.session_id}" in client.prompt
    assert "FACTORY_SESSION_NAME: gk-source-queue-3-to-target-repo" in client.prompt
    assert (
        f"FACTORY_RESUME_COMMAND: pi --session {client.settings.session_id}"
        in client.prompt
    )


def test_pi_renderer_includes_isolated_checkout_contract() -> None:
    checkout = AutomationCheckout("isolated-worktree", "origin/main", "fetch")
    renderer = AutomationSkillRenderer(_skill(), AutomationPolicy(), checkout)
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
        contract=_contract(),
    )
    settings = PiExecutionSettings(
        session_id="session-id",
        name="session-name",
        timeout_seconds=7200,
    )

    prompt = renderer.render(
        _admitted(task),
        False,
        settings,
        TargetWorkspace(Path("/workspaces/task"), "abc123"),
    )

    assert "TARGET_CHECKOUT_MODE: isolated-worktree" in prompt
    assert "TARGET_BASE_REF: origin/main" in prompt
    assert "TARGET_BASE_SHA: abc123" in prompt
    assert "TARGET_REFRESH: fetch" in prompt
    assert "TARGET_WORKSPACE_PATH: /workspaces/task" in prompt


def test_same_repository_task_renders_short_closing_reference() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("target/repo", 3),
        "target/repo",
        contract=_contract(),
    )

    _runner(client).run(_admitted(task))

    assert "FACTORY_CLOSING_REFERENCE: #3" in client.prompt


def test_public_handoff_policy_omits_closing_reference() -> None:
    policy = AutomationPolicy(
        link_source_issue=False,
        include_factory_session=False,
        include_pi_resume=False,
    )
    renderer = AutomationSkillRenderer(_skill(), policy, AutomationCheckout())
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
        contract=_contract(),
    )
    settings = PiExecutionSettings(
        session_id="session-id",
        name="session-name",
        timeout_seconds=7200,
    )

    prompt = renderer.render(
        _admitted(task),
        False,
        settings,
        TargetWorkspace(Path("/repos/dots"), None),
    )

    assert "POLICY_LINK_SOURCE_ISSUE: false" in prompt
    assert "POLICY_INCLUDE_FACTORY_SESSION: false" in prompt
    assert "POLICY_INCLUDE_PI_RESUME: false" in prompt
    assert "FACTORY_CLOSING_REFERENCE:" not in prompt
    assert "FACTORY_SESSION_ID: session-id" in prompt
    assert "FACTORY_SESSION_NAME: session-name" in prompt
    assert "FACTORY_RESUME_COMMAND: pi --session session-id" in prompt


def test_admitted_task_rejects_mismatched_contract() -> None:
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
    )

    with pytest.raises(ValueError, match="requires its normalized contract"):
        AdmittedTask(task, _contract())


def test_pi_runner_exposes_session_metadata_without_starting_worker() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
        contract=_contract(),
    )

    metadata = _runner(client).session_metadata(task)

    assert metadata.session_id
    assert metadata.session_name == "gk-source-queue-3-to-target-repo"
    assert metadata.resume_command == f"pi --session {metadata.session_id}"
    assert client.settings is None


def test_pi_runner_normalizes_target_workspace_failure() -> None:
    client = FakePiClient()
    runner = PiAutomationRunner(
        client,
        FailingCheckout(),
        AutomationSkillRenderer(_skill(), AutomationPolicy(), AutomationCheckout()),
        PiRunnerConfig(skill="issue-implementation"),
    )
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
        contract=_contract(),
    )

    result = runner.run(_admitted(task))

    assert not result.success
    assert result.exit_code == 2
    assert result.error == "local checkout detail"
    assert (
        result.public_detail
        == "Target workspace preparation failed before worker launch"
    )
    assert client.settings is None


def test_deterministic_session_identity_resists_cross_source_collisions() -> None:
    client = FakePiClient()
    first = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("first/queue", 3),
        "target/repo",
        contract=_contract(),
    )
    second_source = replace(first, source_issue=GitHubIssueIdentity("second/queue", 3))
    second_target = replace(first, target_repository="other/target")
    runner = _runner(client)

    first_session = runner.session_metadata(first).session_id
    assert first_session != runner.session_metadata(second_source).session_id
    assert first_session != runner.session_metadata(second_target).session_id


def test_deterministic_session_identity_is_case_insensitive() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("Source/Queue", 3),
        "Target/Repo",
        contract=_contract(),
    )
    differently_cased = replace(
        task,
        source_issue=GitHubIssueIdentity("source/queue", 3),
        target_repository="target/repo",
    )
    runner = _runner(client)

    assert (
        runner.session_metadata(task).session_id
        == runner.session_metadata(differently_cased).session_id
    )


def test_recovery_reuses_deterministic_session_and_changes_only_context() -> None:
    client = FakePiClient()
    task = AutomationTask(
        "github",
        "Fix it",
        "Acceptance",
        "https://issue/3",
        "alex",
        GitHubIssueIdentity("source/queue", 3),
        "target/repo",
        contract=_contract(),
    )
    runner = _runner(client)
    runner.run(_admitted(task))
    first_session = client.settings.session_id if client.settings else ""
    runner.run(_admitted(task), recovery=True)
    assert client.settings is not None
    assert client.settings.session_id == first_session
    assert "RECOVERY_CONTEXT: Resume the deterministic session" in client.prompt


class FakeProcess:
    def __init__(self, result: CommandResult | None = None) -> None:
        self.argv: tuple[str, ...] = ()
        self.timeout: int | None = None
        self.streaming = False
        self.result = result

    def run_streaming(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        timeout: int | None = None,
        stream: object | None = None,
    ) -> CommandResult:
        self.argv, self.timeout = argv, timeout
        self.streaming = True
        return self.result or CommandResult(
            argv, cwd, 0, "https://github.com/me/repo/pull/8", ""
        )


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
    assert result.session_id == "uuid"
    assert result.session_name == "run-name"
    assert result.resume_command == "pi --session uuid"
    assert process.timeout == 7200
    assert process.streaming is True


def test_public_detail_uses_exactly_one_final_bounded_blocker_marker() -> None:
    final = json.dumps(
        {
            "status": "blocked",
            "summary": "Focused tests pass; @reviewer input is required.",
            "next_action": "Approve the recorded validation skip, then resume.",
        }
    )

    detail = _public_detail(
        f"arbitrary private worker output\nFACTORY_RESULT_JSON={final}\n"
    )

    assert detail == (
        "Summary: Focused tests pass; @\u200breviewer input is required.\n\n"
        "Next action: Approve the recorded validation skip, then resume."
    )


@pytest.mark.parametrize(
    "output",
    [
        "arbitrary worker output",
        "FACTORY_RESULT_JSON=not-json",
        'FACTORY_RESULT_JSON={"status":"review","summary":"x","next_action":"y"}',
        'FACTORY_RESULT_JSON={"status":"blocked","summary":"x"}',
        'FACTORY_RESULT_JSON={"status":"blocked","summary":"","next_action":"y"}',
        "FACTORY_RESULT_JSON=" + ("x" * (FACTORY_PUBLIC_DETAIL_MAX_CHARS + 1)),
        "FACTORY_RESULT_JSON="
        + json.dumps(
            {"status": "blocked", "summary": "unsafe\nline", "next_action": "retry"}
        ),
        "FACTORY_RESULT_JSON="
        + json.dumps(
            {"status": "blocked", "summary": "unsafe\ttab", "next_action": "retry"}
        ),
        "FACTORY_RESULT_JSON="
        + json.dumps(
            {"status": "blocked", "summary": "unsafe\u001b", "next_action": "retry"}
        ),
        "FACTORY_RESULT_JSON="
        + json.dumps(
            {"status": "blocked", "summary": "unsafe\u007f", "next_action": "retry"}
        ),
        "FACTORY_RESULT_JSON="
        + json.dumps(
            {"status": "blocked", "summary": "unsafe\u0085", "next_action": "retry"}
        ),
        'FACTORY_RESULT_JSON={"status":"blocked","status":"blocked",'
        '"summary":"x","next_action":"y"}',
        'FACTORY_RESULT_JSON={"status":"blocked","summary":"old","next_action":"x"}\n'
        'FACTORY_RESULT_JSON={"status":"blocked","summary":"new","next_action":"y"}',
        'FACTORY_RESULT_JSON={"status":"blocked","summary":"x","next_action":"y"}\n'
        "later private output",
        'FACTORY_RESULT_JSON={"status":"blocked","summary":"x","next_action":"y"}\n'
        "FACTORY_RESULT_JSON=malformed",
        "FACTORY_RESULT_JSON="
        + json.dumps(
            {"status": "blocked", "summary": "@" * 3_990, "next_action": "retry"}
        ),
    ],
)
def test_public_detail_rejects_untrusted_or_invalid_worker_output(output: str) -> None:
    assert _public_detail(output) is None


@pytest.mark.parametrize(
    "error",
    [
        "Codex usage limit reached. Try again after 4:00 PM.",
        "You've hit your usage limit. Try again at 4:00 PM.",
        "Rate limit exceeded for provider; retry later",
        "HTTP 429 Too Many Requests",
        "API Error: 429; retry later",
        "The provider is temporarily at capacity. Please try again later.",
    ],
)
def test_pi_client_classifies_explicit_provider_exhaustion_as_deferred(
    error: str,
) -> None:
    process = FakeProcess(CommandResult((), Path("/repo"), 1, "", error))

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert not result.success
    assert result.failure_disposition is FailureDisposition.DEFERRED


@pytest.mark.parametrize(
    "error",
    [
        "No API key found for openai-codex. Use /login.",
        "Authentication failed: 401 Unauthorized",
        "Access forbidden (HTTP 403)",
        "Model gpt-missing was not found",
        "Unknown model gpt-missing",
        "Invalid model selection",
        "The configured model is unavailable for this account",
        "Request rejected by organization policy",
        "Request blocked by policy",
        "Provider policy violation",
        "worker failed while editing tests",
        "command timed out after 7200 seconds: pi",
    ],
)
def test_pi_client_keeps_auth_model_policy_worker_and_timeout_failures_blocked(
    error: str,
) -> None:
    process = FakeProcess(CommandResult((), Path("/repo"), 1, "", error))

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert not result.success
    assert result.failure_disposition is FailureDisposition.BLOCKED


def test_streaming_boundary_does_not_classify_transient_worker_stdout(
    tmp_path: Path,
) -> None:
    process_result = ProcessClient().run_streaming(
        (
            sys.executable,
            "-c",
            "import sys; "
            "print('Added a test for HTTP 429 Too Many Requests'); "
            "print('ordinary worker failure', file=sys.stderr); "
            "raise SystemExit(1)",
        ),
        tmp_path,
        stream=io.StringIO(),
    )

    assert "HTTP 429" in process_result.stdout
    assert "HTTP 429" not in process_result.stderr
    assert _failure_disposition(process_result.stderr, process_result.exit_code) is (
        FailureDisposition.BLOCKED
    )


def test_pi_client_ignores_transient_text_in_worker_output() -> None:
    process = FakeProcess(
        CommandResult(
            (),
            Path("/repo"),
            1,
            "Added a test for HTTP 429 Too Many Requests",
            "worker failed while editing tests",
        )
    )

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert result.failure_disposition is FailureDisposition.BLOCKED


def test_pi_client_never_uses_unmarked_stdout_as_public_failure_detail() -> None:
    process = FakeProcess(
        CommandResult((), Path("/repo"), 1, "private worker failure analysis", "")
    )

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert result.error == ""
    assert result.public_detail is None
    assert result.output == "private worker failure analysis"


def test_pi_client_prioritizes_durable_diagnostic_over_transient_text() -> None:
    process = FakeProcess(
        CommandResult(
            (),
            Path("/repo"),
            1,
            "",
            "Codex usage limit reached, but no API key found for openai-codex",
        )
    )

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert result.failure_disposition is FailureDisposition.BLOCKED


def test_pi_client_classifies_full_stderr_before_display_tail_truncation() -> None:
    full_error = (
        "Authentication failed: 401 Unauthorized\n"
        + ("diagnostic filler\n" * 700)
        + "HTTP 429 Too Many Requests"
    )
    process = FakeProcess(CommandResult((), Path("/repo"), 1, "", full_error))

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert result.failure_disposition is FailureDisposition.BLOCKED
    assert len(result.error) <= PROCESS_ERROR_TAIL_CHARS
    assert "HTTP 429" in result.error


def test_pi_client_keeps_timeout_blocked_even_after_transient_output() -> None:
    process = FakeProcess(
        CommandResult(
            (),
            Path("/repo"),
            124,
            "Codex usage limit reached",
            "command timed out after 7200 seconds: pi",
        )
    )

    result = PiClient(process).run_prompt(  # type: ignore[arg-type]
        "rendered task", Path("/repo"), PiExecutionSettings("uuid", "run-name")
    )

    assert result.failure_disposition is FailureDisposition.BLOCKED
