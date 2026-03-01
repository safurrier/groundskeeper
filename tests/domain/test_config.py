"""Tests for config loading and workflow extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from groundskeeper.domain.config import get_workflow, get_workflows, load_config
from groundskeeper.domain.errors import ConfigError


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text("version: 1\nrunner: claude-code\n")
        data = load_config(cfg)
        assert data["version"] == 1
        assert data["runner"] == "claude-code"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Config not found"):
            load_config(tmp_path / "nope.yml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text(": [[[invalid yaml")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_config(cfg)

    def test_load_non_mapping(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text("- just a list\n")
        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            load_config(cfg)


class TestGetWorkflows:
    def test_single_workflow(self) -> None:
        config = {
            "workflows": {
                "pr-review": {
                    "triggers": {"pull_request": ["synchronize"]},
                    "skills": ["code-review"],
                }
            }
        }
        wfs = get_workflows(config)
        assert len(wfs) == 1
        assert wfs[0].name == "pr-review"
        assert wfs[0].skills == ["code-review"]
        assert wfs[0].triggers == {"pull_request": ["synchronize"]}

    def test_multi_skill_workflow(self) -> None:
        config = {
            "workflows": {
                "full-check": {
                    "triggers": {"pull_request": ["ready_for_review"]},
                    "skills": ["lint-check", "code-review", "context-files"],
                }
            }
        }
        wfs = get_workflows(config)
        assert len(wfs) == 1
        assert wfs[0].skills == ["lint-check", "code-review", "context-files"]

    def test_multiple_workflows(self) -> None:
        config = {
            "workflows": {
                "review": {
                    "triggers": {"pull_request": ["synchronize"]},
                    "skills": ["code-review"],
                },
                "docs": {
                    "triggers": {"pull_request": ["ready_for_review"]},
                    "skills": ["context-files"],
                },
            }
        }
        wfs = get_workflows(config)
        assert len(wfs) == 2
        names = {wf.name for wf in wfs}
        assert names == {"review", "docs"}

    def test_no_workflows_key(self) -> None:
        assert get_workflows({"version": 1}) == []

    def test_empty_workflows(self) -> None:
        assert get_workflows({"workflows": {}}) == []

    def test_skips_workflow_with_no_skills(self) -> None:
        config = {
            "workflows": {
                "empty": {
                    "triggers": {"pull_request": ["synchronize"]},
                    "skills": [],
                }
            }
        }
        assert get_workflows(config) == []


class TestGetWorkflow:
    def test_found(self) -> None:
        config = {
            "workflows": {
                "pr-review": {
                    "triggers": {},
                    "skills": ["code-review"],
                }
            }
        }
        wf = get_workflow(config, "pr-review")
        assert wf is not None
        assert wf.name == "pr-review"

    def test_not_found(self) -> None:
        config = {
            "workflows": {
                "pr-review": {
                    "triggers": {},
                    "skills": ["code-review"],
                }
            }
        }
        assert get_workflow(config, "nonexistent") is None

    def test_empty_config(self) -> None:
        assert get_workflow({}, "anything") is None
