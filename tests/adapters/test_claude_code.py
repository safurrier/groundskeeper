"""Tests for the ClaudeCodeRunner adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from groundskeeper.adapters.claude_code import ClaudeCodeRunner
from groundskeeper.domain.models import RunContext, Skill, SkillSource


def _make_context(body: str = "do something", arguments: str = "") -> RunContext:
    skill = Skill(
        name="test-skill",
        description="A test skill",
        body=body,
        source=SkillSource(kind="local", path=Path("/fake")),
    )
    return RunContext(skill=skill, arguments=arguments)


class TestClaudeCodeRunner:
    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_success(self, mock_run: object) -> None:
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=0,
            stdout="output text",
            stderr="",
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is True
        assert result.output == "output text"
        assert result.exit_code == 0
        mock_run.assert_called_once()  # type: ignore[attr-defined]

    @patch("groundskeeper.adapters.claude_code.subprocess.run")
    def test_claude_code_failure(self, mock_run: object) -> None:
        mock_run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=["claude"],
            returncode=1,
            stdout="",
            stderr="error message",
        )
        context = _make_context()
        result = ClaudeCodeRunner().run(context)

        assert result.success is False
        assert result.error == "error message"
        assert result.exit_code == 1

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
