import json
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from groundskeeper.adapters.tick_lock import TickAlreadyRunningError
from groundskeeper.cli.main import _automation_lock_path, cli
from groundskeeper.domain.automation import AutomationTask, TickResult
from groundskeeper.domain.config import get_automations

CONFIG = """
automations:
  daily-dev:
    source:
      type: github-issues
      repository: me/dots
      repository-path: /tmp
      trusted-authors: [alex]
    runner:
      type: pi
      skill: codex-code-review
      approval: allow
      session: deterministic
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
    assert payload["version"] == 1
    assert payload["status"] == "ok"
    assert payload["data"]["automations"][0]["runner"]["skill"] == "codex-code-review"


def test_same_repository_automations_share_one_host_lock() -> None:
    config = CONFIG.replace("daily-dev:", "queue-a:") + CONFIG.replace(
        "daily-dev:", "queue-b:"
    ).replace("automations:\n", "")
    queue_a, queue_b = get_automations(yaml.safe_load(config))
    config_path = Path("/tmp/.groundskeeper/config.yml")
    assert _automation_lock_path(config_path, queue_a) == _automation_lock_path(
        config_path, queue_b
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
    assert json.loads(show.output)["data"]["automation"]["name"] == "daily-dev"
    assert validate.exit_code == 0
    assert json.loads(validate.output)["version"] == 1


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
        "7",
        "Do work",
        "private acceptance criteria",
        "https://github.com/me/dots/issues/7",
        "alex",
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
    assert json.loads(result.output)["data"]["task"]["id"] == "7"


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
    assert payload["version"] == 1


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
