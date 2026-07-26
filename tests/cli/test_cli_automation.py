import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from groundskeeper.adapters.tick_lock import TickAlreadyRunningError
from groundskeeper.cli.main import _automation_lock_path, cli
from groundskeeper.domain.automation import (
    AutomationTask,
    GitHubIssueIdentity,
    TaskState,
    TickResult,
)
from groundskeeper.domain.config import get_automations


@pytest.fixture(autouse=True)
def _accept_target_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "groundskeeper.cli.main.validate_target_checkout",
        lambda process, repository_path, expected_repository, checkout: None,
    )
    monkeypatch.setattr(
        "groundskeeper.cli.main.prepare_target_checkout",
        lambda process, repository_path, expected_repository, checkout: None,
    )


CONFIG = """
automations:
  daily-dev:
    source:
      type: github-issues
      repository: me/queue
      trusted-authors: [alex]
    target:
      repository: me/dots
      repository-path: /tmp
    runner:
      type: pi
      skill: codex-code-review
      approval: allow
      session: deterministic
      timeout-seconds: 7200
    policy:
      concurrency: 1
      output: draft-pr
      merge: never
"""


def test_automation_list_json(tmp_path: Path) -> None:
    config_dir = tmp_path / ".groundskeeper"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(CONFIG)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "version": 2,
        "status": "ok",
        "data": {
            "automations": [
                {
                    "name": "daily-dev",
                    "source": {
                        "type": "github-issues",
                        "repository": "me/queue",
                        "trusted_authors": ["alex"],
                        "labels": {
                            "ready": "factory:ready",
                            "running": "factory:running",
                            "deferred": "factory:deferred",
                            "review": "factory:review",
                            "blocked": "factory:blocked",
                        },
                    },
                    "target": {
                        "repository": "me/dots",
                        "repository_path": str(Path("/tmp").resolve()),
                        "checkout": {
                            "mode": "existing",
                            "base_ref": None,
                            "refresh": "none",
                        },
                    },
                    "runner": {
                        "type": "pi",
                        "skill": "codex-code-review",
                        "approval": "allow",
                        "session": "deterministic",
                        "timeout_seconds": 7200,
                    },
                    "policy": {
                        "concurrency": 1,
                        "output": "draft-pr",
                        "merge": "never",
                    },
                }
            ]
        },
        "exit_code": 0,
    }


def test_repository_locks_are_host_scoped_and_identity_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GROUNDSKEEPER_STATE_HOME", str(tmp_path / "state"))
    config = CONFIG.replace("daily-dev:", "queue-a:") + CONFIG.replace(
        "daily-dev:", "queue-b:"
    ).replace("automations:\n", "")
    queue_a, queue_b = get_automations(yaml.safe_load(config))
    other_repo = get_automations(
        yaml.safe_load(
            CONFIG.replace("daily-dev:", "queue-c:").replace("me/queue", "me/other")
        )
    )[0]
    assert _automation_lock_path(queue_a) == _automation_lock_path(queue_b)
    assert _automation_lock_path(queue_a) != _automation_lock_path(other_repo)
    assert (
        _automation_lock_path(queue_a).parent
        == tmp_path / "state" / "groundskeeper" / "locks"
    )


@patch("groundskeeper.cli.main.PiClient.is_available", return_value=True)
def test_automation_show_and_validate_use_versioned_envelopes(
    mock_available: object, tmp_path: Path
) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        show = runner.invoke(cli, ["automation", "show", "daily-dev", "--json"])
        validate = runner.invoke(cli, ["automation", "validate", "daily-dev", "--json"])
    assert show.exit_code == 0
    skill_path = str(
        Path(__file__).parents[2]
        / "groundskeeper"
        / "builtins"
        / "skills"
        / "codex-code-review"
    )
    assert json.loads(show.output) == {
        "version": 2,
        "status": "ok",
        "data": {
            "automation": {
                "name": "daily-dev",
                "source": {
                    "type": "github-issues",
                    "repository": "me/queue",
                    "trusted_authors": ["alex"],
                    "labels": {
                        "ready": "factory:ready",
                        "running": "factory:running",
                        "deferred": "factory:deferred",
                        "review": "factory:review",
                        "blocked": "factory:blocked",
                    },
                },
                "target": {
                    "repository": "me/dots",
                    "repository_path": str(Path("/tmp").resolve()),
                    "checkout": {
                        "mode": "existing",
                        "base_ref": None,
                        "refresh": "none",
                    },
                },
                "runner": {
                    "type": "pi",
                    "skill": "codex-code-review",
                    "approval": "allow",
                    "session": "deterministic",
                    "timeout_seconds": 7200,
                },
                "policy": {
                    "concurrency": 1,
                    "output": "draft-pr",
                    "merge": "never",
                },
                "skill": {
                    "name": "codex-code-review",
                    "source_kind": "builtin",
                    "path": skill_path,
                },
            }
        },
        "exit_code": 0,
    }
    assert validate.exit_code == 0
    validated = json.loads(validate.output)
    assert validated["version"] == 2
    assert validated["data"]["automations"][0]["skill"]["name"] == "codex-code-review"


@patch("groundskeeper.cli.main.GitHubIssuesTracker")
def test_inspect_full_versioned_payload(mock_tracker: object, tmp_path: Path) -> None:
    tracker = mock_tracker.return_value  # type: ignore[attr-defined]
    tracker.list_running.return_value = []
    tracker.list_deferred.return_value = []
    tracker.list_ready.return_value = []
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "inspect", "daily-dev", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "version": 2,
        "status": "ok",
        "data": {
            "automation": "daily-dev",
            "source": {"repository": "me/queue"},
            "target": {
                "repository": "me/dots",
                "repository_path": str(Path("/tmp").resolve()),
                "checkout": {
                    "mode": "existing",
                    "base_ref": None,
                    "refresh": "none",
                },
            },
            "tasks": [],
        },
        "exit_code": 0,
    }


def test_automation_leaf_help_has_configured_examples() -> None:
    runner = CliRunner()
    for command in ("list", "show", "validate"):
        result = runner.invoke(cli, ["automation", command, "--help"])
        assert result.exit_code == 0
        assert "gk automation --config" in result.output
        assert "Exit codes:" in result.output


def test_validate_rejects_missing_skill_before_tick(tmp_path: Path) -> None:
    missing_skill = CONFIG.replace("skill: codex-code-review", "skill: missing-skill")
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(missing_skill)
        result = runner.invoke(cli, ["automation", "validate", "daily-dev", "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert "missing-skill" in payload["data"]["error"]


@patch("groundskeeper.cli.main.AutomationService.tick")
def test_tick_dry_run_omits_issue_body_from_json(
    mock_tick: object, tmp_path: Path
) -> None:
    task = AutomationTask(
        "github",
        "Do work",
        "private acceptance criteria",
        "https://github.com/me/queue/issues/7",
        "alex",
        GitHubIssueIdentity("me/queue", 7),
        "me/dots",
    )
    mock_tick.return_value = TickResult("daily-dev", "would-dispatch", task)  # type: ignore[attr-defined]
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(
            cli, ["automation", "tick", "daily-dev", "--dry-run", "--json"]
        )
    assert result.exit_code == 0
    assert "private acceptance criteria" not in result.output
    assert json.loads(result.output) == {
        "version": 2,
        "status": "would-dispatch",
        "data": {
            "automation": "daily-dev",
            "source": {"repository": "me/queue"},
            "target": {
                "repository": "me/dots",
                "repository_path": str(Path("/tmp").resolve()),
                "checkout": {
                    "mode": "existing",
                    "base_ref": None,
                    "refresh": "none",
                },
            },
            "task": {
                "id": "7",
                "title": "Do work",
                "url": "https://github.com/me/queue/issues/7",
                "source": {"repository": "me/queue", "issue": 7},
                "target": {"repository": "me/dots"},
                "state": "ready",
            },
            "pull_request_url": None,
            "detail": "",
            "session_id": None,
            "session_name": None,
            "resume_command": None,
        },
        "exit_code": 0,
    }


@patch("groundskeeper.cli.main.PiClient.supports_automation", return_value=False)
@patch("groundskeeper.cli.main.PiClient.is_available", return_value=True)
@patch("groundskeeper.cli.main.AutomationService.tick")
def test_unsupported_host_fails_before_tracker_mutation(
    mock_tick: object,
    mock_available: object,
    mock_supported: object,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert "requires POSIX process-group isolation" in payload["data"]["error"]
    mock_available.assert_called_once()  # type: ignore[attr-defined]
    mock_supported.assert_called_once()  # type: ignore[attr-defined]
    mock_tick.assert_not_called()  # type: ignore[attr-defined]


def test_text_errors_use_documented_exit_code(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "missing"])
    assert result.exit_code == 2
    assert "gk automation list" in result.output


@patch("groundskeeper.cli.main.PiClient.is_available", return_value=False)
@patch("groundskeeper.cli.main.AutomationService.tick")
def test_dry_run_does_not_require_pi(
    mock_tick: object, mock_available: object, tmp_path: Path
) -> None:
    mock_tick.return_value = TickResult("daily-dev", "no-work")  # type: ignore[attr-defined]
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(
            cli, ["automation", "tick", "daily-dev", "--dry-run", "--json"]
        )
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "no-work"
    mock_available.assert_not_called()  # type: ignore[attr-defined]


def test_tick_unknown_name_is_actionable(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "missing"])
    assert result.exit_code != 0
    assert "gk automation list" in result.output


def test_tick_unknown_name_json_envelope(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "missing", "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert payload["status"] == "error" and payload["exit_code"] == 2
    assert payload["version"] == 2


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("timeout-seconds", "timeout_seconds"),
        ("concurrency", "concurency"),
    ],
)
def test_strict_config_errors_offer_copyable_corrections(
    replacement: str, expected: str, tmp_path: Path
) -> None:
    invalid_config = CONFIG.replace(replacement, expected)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(invalid_config)
        result = runner.invoke(cli, ["automation", "validate", "daily-dev", "--json"])
    assert result.exit_code == 2
    assert f"Use '{replacement}'." in json.loads(result.output)["data"]["error"]


def test_malformed_config_json_envelope(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text("automations: [bad]")
        result = runner.invoke(cli, ["automation", "tick", "daily", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "error"


@patch("groundskeeper.cli.main.PiClient.is_available", return_value=True)
@patch("groundskeeper.cli.main.AutomationService.tick")
def test_blocked_result_has_stable_exit_code(
    mock_tick: object, mock_available: object, tmp_path: Path
) -> None:
    mock_tick.return_value = TickResult("daily-dev", "blocked", detail="pi missing")  # type: ignore[attr-defined]
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])
    assert result.exit_code == 5
    assert json.loads(result.output)["status"] == "blocked"


@patch("groundskeeper.cli.main.PiClient.is_available", return_value=True)
@patch("groundskeeper.cli.main.AutomationService.tick")
def test_deferred_result_is_structured_and_nonfatal(
    mock_tick: object, mock_available: object, tmp_path: Path
) -> None:
    task = AutomationTask(
        "github",
        "Do work",
        "body",
        "https://github.com/me/queue/issues/7",
        "alex",
        GitHubIssueIdentity("me/queue", 7),
        "me/dots",
        TaskState.DEFERRED,
    )
    mock_tick.return_value = TickResult(  # type: ignore[attr-defined]
        "daily-dev",
        "deferred",
        task=task,
        detail="provider exhausted; retry next tick",
        session_id="session-123",
        session_name="gk-me-dots-7",
        resume_command="pi --session session-123",
    )
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["status"] == "deferred"
    assert payload["exit_code"] == 0
    assert payload["data"]["resume_command"] == "pi --session session-123"
    assert payload["data"]["task"]["state"] == "deferred"


@patch("groundskeeper.cli.main.PiClient.is_available", return_value=True)
@patch("groundskeeper.cli.main.AutomationService.tick")
def test_json_result_exposes_resumable_factory_session(
    mock_tick: object, mock_available: object, tmp_path: Path
) -> None:
    mock_tick.return_value = TickResult(  # type: ignore[attr-defined]
        "daily-dev",
        "review",
        pull_request_url="https://github/pr/9",
        session_id="session-123",
        session_name="gk-me-dots-7",
        resume_command="pi --session session-123",
    )
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["data"]["session_id"] == "session-123"
    assert payload["data"]["session_name"] == "gk-me-dots-7"
    assert payload["data"]["resume_command"] == "pi --session session-123"


@patch("groundskeeper.cli.main.PiClient.is_available", return_value=True)
@patch("groundskeeper.cli.main.TickLock.__enter__")
def test_lock_contention_json_envelope(
    mock_enter: object, mock_available: object, tmp_path: Path
) -> None:
    mock_enter.side_effect = TickAlreadyRunningError("busy")  # type: ignore[attr-defined]
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["data"]["error"] == "busy"
