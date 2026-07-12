import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from groundskeeper.adapters.tick_lock import TickAlreadyRunningError
from groundskeeper.cli.main import cli
from groundskeeper.domain.automation import TickResult

CONFIG = """
automations:
  daily-dev:
    source:
      type: github-issues
      repository: me/dots
      repository-path: /tmp/dots
      trusted-authors: [alex]
    runner:
      type: pi
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
    assert json.loads(result.output) == [
        {
            "name": "daily-dev",
            "repository": "me/dots",
            "runner": "pi",
            "concurrency": 1,
        }
    ]


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


def test_malformed_config_json_envelope(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text("automations: [bad]")
        result = runner.invoke(cli, ["automation", "tick", "daily", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "error"


@patch("groundskeeper.cli.main.AutomationService.tick")
def test_blocked_result_has_stable_exit_code(mock_tick: object, tmp_path: Path) -> None:
    mock_tick.return_value = TickResult("daily-dev", "blocked", detail="pi missing")  # type: ignore[attr-defined]
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])
    assert result.exit_code == 5
    assert json.loads(result.output)["status"] == "blocked"


@patch("groundskeeper.cli.main.TickLock.__enter__")
def test_lock_contention_json_envelope(mock_enter: object, tmp_path: Path) -> None:
    mock_enter.side_effect = TickAlreadyRunningError("busy")  # type: ignore[attr-defined]
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".groundskeeper").mkdir(exist_ok=True)
        Path(".groundskeeper/config.yml").write_text(CONFIG)
        result = runner.invoke(cli, ["automation", "tick", "daily-dev", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "busy"
