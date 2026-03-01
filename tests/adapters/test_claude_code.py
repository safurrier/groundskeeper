"""Tests for the ClaudeCodeRunner adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from groundskeeper.adapters.claude_code import ClaudeCodeRunner
from groundskeeper.domain.models import RunContext, Skill, SkillSource


def _make_context(
    body: str = "do something",
    arguments: str = "",
    allowed_tools: list[str] | None = None,
    skip_permissions: bool = False,
    allowed_tools_override: list[str] | None = None,
) -> RunContext:
    skill = Skill(
        name="test-skill",
        description="A test skill",
        body=body,
        source=SkillSource(kind="local", path=Path("/fake")),
        allowed_tools=allowed_tools or [],
    )
    return RunContext(
        skill=skill,
        arguments=arguments,
        skip_permissions=skip_permissions,
        allowed_tools_override=allowed_tools_override,
    )


class TestClaudeCodeRunner:
    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_success(self, mock_run: object) -> None:
        stdout = json.dumps(
            {
                "result": "output text",
                "is_error": False,
                "session_id": "abc",
                "num_turns": 3,
                "total_cost_usd": 0.05,
            }
        )
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is True
        assert result.output == "output text"
        assert result.exit_code == 0
        assert result.metadata["session_id"] == "abc"
        assert result.metadata["num_turns"] == 3
        assert result.metadata["total_cost_usd"] == 0.05
        mock_run.assert_called_once()  # type: ignore[attr-defined]

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_failure_is_error(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "", "is_error": True})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is False
        assert result.exit_code == 0

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_failure_nonzero_returncode(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=1,
            stdout=stdout,
            stderr="error message",
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is False
        assert result.error == "error message"
        assert result.exit_code == 1

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_command_includes_json_output_format(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context()
        ClaudeCodeRunner().run(context)

        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_allowed_tools_in_command(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context(allowed_tools=["Read", "Bash"])
        ClaudeCodeRunner().run(context)

        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        # Find all --allowedTools flags and their values
        allowed: list[str] = []
        for i, arg in enumerate(cmd):
            if arg == "--allowedTools":
                allowed.append(cmd[i + 1])
        assert allowed == ["Read", "Bash"]

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_empty_allowed_tools_not_in_command(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context(allowed_tools=[])
        ClaudeCodeRunner().run(context)

        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        assert "--allowedTools" not in cmd

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_skip_permissions_in_command(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context(skip_permissions=True)
        ClaudeCodeRunner().run(context)

        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        assert "--dangerously-skip-permissions" in cmd

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_skip_permissions_overrides_allowed_tools(self, mock_run: object) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        context = _make_context(allowed_tools=["Read", "Bash"], skip_permissions=True)
        ClaudeCodeRunner().run(context)

        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        assert "--dangerously-skip-permissions" in cmd
        assert "--allowedTools" not in cmd

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_allowed_tools_override_beats_skill_frontmatter(
        self, mock_run: object
    ) -> None:
        stdout = json.dumps({"result": "ok", "is_error": False})
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        # Skill has Read,Bash but override says Write,Edit
        context = _make_context(
            allowed_tools=["Read", "Bash"],
            allowed_tools_override=["Write", "Edit"],
        )
        ClaudeCodeRunner().run(context)

        cmd = mock_run.call_args[0][0]  # type: ignore[attr-defined]
        assert cmd.count("--allowedTools") == 2
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Write"

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_json_parse_failure_falls_back_to_plain_text(
        self, mock_run: object
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout="plain text output, not JSON",
            stderr="",
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is True
        assert result.output == "plain text output, not JSON"
        assert result.exit_code == 0
        assert result.metadata == {}

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_not_found(self, mock_run: object) -> None:
        mock_run.side_effect = FileNotFoundError  # type: ignore[attr-defined]
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is False
        assert "claude CLI not found" in result.error
        assert result.exit_code == 1

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_timeout(self, mock_run: object) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(  # type: ignore[attr-defined]
            cmd="claude", timeout=600
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is False
        assert "timed out" in result.error
        assert result.exit_code == 1

    @patch("groundskeeper.adapters.claude_code.shutil.which")
    def test_claude_code_is_available(self, mock_which: object) -> None:
        mock_which.return_value = "/usr/bin/claude"  # type: ignore[attr-defined]
        assert ClaudeCodeRunner().is_available() is True

    @patch("groundskeeper.adapters.claude_code.shutil.which")
    def test_claude_code_is_not_available(self, mock_which: object) -> None:
        mock_which.return_value = None  # type: ignore[attr-defined]
        assert ClaudeCodeRunner().is_available() is False
