"""Tests for the ClaudeCodeRunner semantic process adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from groundskeeper.adapters.claude_code import ClaudeCodeRunner
from groundskeeper.adapters.process import CommandResult
from groundskeeper.domain.models import RunContext, Skill, SkillSource


def _context(
    allowed_tools: list[str] | None = None,
    skip_permissions: bool = False,
    override: list[str] | None = None,
) -> RunContext:
    return RunContext(
        skill=Skill(
            name="test-skill",
            description="test",
            body="do it",
            source=SkillSource(kind="local", path=Path("/fake")),
            allowed_tools=allowed_tools or [],
        ),
        skip_permissions=skip_permissions,
        allowed_tools_override=override,
    )


class FakeProcess:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.argv: tuple[str, ...] = ()
        self.timeout: int | None = None

    def run(
        self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
    ) -> CommandResult:
        self.argv, self.timeout = argv, timeout
        return self.result


def _process(stdout: str, exit_code: int = 0, stderr: str = "") -> FakeProcess:
    return FakeProcess(CommandResult(("claude",), Path("."), exit_code, stdout, stderr))


def test_success_parses_json_and_metadata() -> None:
    process = _process(
        json.dumps(
            {
                "result": "output",
                "is_error": False,
                "session_id": "abc",
                "num_turns": 3,
            }
        )
    )
    result = ClaudeCodeRunner(process).run(_context())  # type: ignore[arg-type]
    assert result.success and result.output == "output"
    assert result.metadata == {"session_id": "abc", "num_turns": 3}
    assert process.timeout == 600


@pytest.mark.parametrize(
    ("exit_code", "is_error"),
    [(1, False), (0, True)],
)
def test_failure_signals_are_preserved(exit_code: int, is_error: bool) -> None:
    process = _process(
        json.dumps({"result": "", "is_error": is_error}), exit_code, "bad"
    )
    result = ClaudeCodeRunner(process).run(_context())  # type: ignore[arg-type]
    assert not result.success
    assert result.exit_code == exit_code


def test_command_applies_tool_permissions_and_override() -> None:
    process = _process('{"result":"ok","is_error":false}')
    ClaudeCodeRunner(process).run(  # type: ignore[arg-type]
        _context(["Read"], override=["Write", "Edit"])
    )
    assert process.argv.count("--allowedTools") == 2
    assert "Write" in process.argv and "Edit" in process.argv


def test_skip_permissions_omits_tool_allowlist() -> None:
    process = _process('{"result":"ok","is_error":false}')
    ClaudeCodeRunner(process).run(_context(["Read"], skip_permissions=True))  # type: ignore[arg-type]
    assert "--dangerously-skip-permissions" in process.argv
    assert "--allowedTools" not in process.argv


def test_plain_text_fallback() -> None:
    result = ClaudeCodeRunner(_process("plain text")).run(_context())  # type: ignore[arg-type]
    assert result.success and result.output == "plain text"


@pytest.mark.parametrize(
    ("exit_code", "message"),
    [(127, "not found"), (124, "timed out")],
)
def test_process_boundary_errors_are_actionable(exit_code: int, message: str) -> None:
    result = ClaudeCodeRunner(_process("", exit_code)).run(_context())  # type: ignore[arg-type]
    assert not result.success and message in result.error


@patch("groundskeeper.adapters.claude_code.shutil.which")
def test_availability_uses_path(mock_which: object) -> None:
    mock_which.return_value = "/usr/bin/claude"  # type: ignore[attr-defined]
    assert ClaudeCodeRunner().is_available()
