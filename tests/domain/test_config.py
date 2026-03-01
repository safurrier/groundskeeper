"""Tests for config loading and workflow extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from groundskeeper.domain.config import (
    ParallelGroup,
    SkillRef,
    get_workflow,
    get_workflows,
    load_config,
)
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
        assert wfs[0].all_skill_names == ["code-review"]
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
        assert wfs[0].all_skill_names == ["lint-check", "code-review", "context-files"]

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


class TestWorkflowAllowedTools:
    """Test allowed-tools precedence: step > workflow > skill frontmatter."""

    def test_workflow_level_tools(self) -> None:
        config = {
            "workflows": {
                "check": {
                    "triggers": {},
                    "allowed-tools": ["Read", "Grep", "Glob"],
                    "skills": ["code-review"],
                }
            }
        }
        wf = get_workflow(config, "check")
        assert wf is not None
        assert wf.allowed_tools == ["Read", "Grep", "Glob"]
        step = wf.steps[0]
        assert isinstance(step, SkillRef)
        assert wf.effective_tools(step) == ["Read", "Grep", "Glob"]

    def test_per_step_tools_override_workflow(self) -> None:
        config = {
            "workflows": {
                "check": {
                    "triggers": {},
                    "allowed-tools": ["Read", "Grep"],
                    "skills": [
                        {"name": "writer", "allowed-tools": ["Read", "Write", "Edit"]},
                        "reader",
                    ],
                }
            }
        }
        wf = get_workflow(config, "check")
        assert wf is not None
        assert wf.all_skill_names == ["writer", "reader"]
        writer = wf.steps[0]
        reader = wf.steps[1]
        assert isinstance(writer, SkillRef)
        assert isinstance(reader, SkillRef)
        assert wf.effective_tools(writer) == ["Read", "Write", "Edit"]
        assert wf.effective_tools(reader) == ["Read", "Grep"]

    def test_no_tools_anywhere_returns_none(self) -> None:
        config = {"workflows": {"bare": {"triggers": {}, "skills": ["code-review"]}}}
        wf = get_workflow(config, "bare")
        assert wf is not None
        step = wf.steps[0]
        assert isinstance(step, SkillRef)
        assert wf.effective_tools(step) is None

    def test_mixed_string_and_dict_skills(self) -> None:
        config = {
            "workflows": {
                "mixed": {
                    "triggers": {},
                    "skills": [
                        "simple-skill",
                        {"name": "detailed-skill", "allowed-tools": ["Bash"]},
                        "another-simple",
                    ],
                }
            }
        }
        wf = get_workflow(config, "mixed")
        assert wf is not None
        assert wf.all_skill_names == [
            "simple-skill",
            "detailed-skill",
            "another-simple",
        ]
        assert all(isinstance(s, SkillRef) for s in wf.steps)
        refs = [s for s in wf.steps if isinstance(s, SkillRef)]
        assert refs[0].allowed_tools == []
        assert refs[1].allowed_tools == ["Bash"]
        assert refs[2].allowed_tools == []


class TestParallelGroups:
    def test_flat_list_produces_skill_refs(self) -> None:
        config = {
            "workflows": {
                "seq": {"triggers": {}, "skills": ["a", "b", "c"]},
            }
        }
        wf = get_workflow(config, "seq")
        assert wf is not None
        assert len(wf.steps) == 3
        assert all(isinstance(s, SkillRef) for s in wf.steps)
        assert wf.all_skill_names == ["a", "b", "c"]

    def test_nested_list_produces_parallel_group(self) -> None:
        config = {
            "workflows": {
                "par": {"triggers": {}, "skills": [["lint", "typecheck"], "test"]},
            }
        }
        wf = get_workflow(config, "par")
        assert wf is not None
        assert len(wf.steps) == 2
        assert isinstance(wf.steps[0], ParallelGroup)
        assert isinstance(wf.steps[1], SkillRef)
        assert len(wf.steps[0].skills) == 2
        assert wf.steps[0].skills[0].name == "lint"
        assert wf.steps[0].skills[1].name == "typecheck"

    def test_parallel_group_with_dict_entries(self) -> None:
        config = {
            "workflows": {
                "par": {
                    "triggers": {},
                    "skills": [
                        ["lint", {"name": "typecheck", "allowed-tools": ["Bash"]}],
                        "test",
                    ],
                }
            }
        }
        wf = get_workflow(config, "par")
        assert wf is not None
        group = wf.steps[0]
        assert isinstance(group, ParallelGroup)
        assert group.skills[0] == SkillRef(name="lint")
        assert group.skills[1] == SkillRef(name="typecheck", allowed_tools=["Bash"])

    def test_all_skill_names_flattens(self) -> None:
        config = {
            "workflows": {
                "mixed": {"triggers": {}, "skills": [["a", "b"], "c", ["d", "e"]]},
            }
        }
        wf = get_workflow(config, "mixed")
        assert wf is not None
        assert wf.all_skill_names == ["a", "b", "c", "d", "e"]

    def test_all_skill_refs_flattens(self) -> None:
        config = {
            "workflows": {
                "mixed": {"triggers": {}, "skills": [["a", "b"], "c"]},
            }
        }
        wf = get_workflow(config, "mixed")
        assert wf is not None
        assert len(wf.all_skill_refs) == 3
        assert [s.name for s in wf.all_skill_refs] == ["a", "b", "c"]

    def test_empty_parallel_group_skipped(self) -> None:
        config = {
            "workflows": {
                "empty": {"triggers": {}, "skills": [[], "a"]},
            }
        }
        wf = get_workflow(config, "empty")
        assert wf is not None
        assert len(wf.steps) == 1
        assert isinstance(wf.steps[0], SkillRef)
        assert wf.steps[0].name == "a"


class TestReadOnlyDetection:
    def test_read_only_group(self) -> None:
        config = {
            "workflows": {
                "ro": {
                    "triggers": {},
                    "allowed-tools": ["Read", "Grep", "Glob"],
                    "skills": [["reviewer-a", "reviewer-b"]],
                }
            }
        }
        wf = get_workflow(config, "ro")
        assert wf is not None
        group = wf.steps[0]
        assert isinstance(group, ParallelGroup)
        assert wf.is_group_read_only(group) is True

    def test_write_capable_group(self) -> None:
        config = {
            "workflows": {
                "rw": {
                    "triggers": {},
                    "skills": [
                        [
                            {"name": "writer", "allowed-tools": ["Read", "Write"]},
                            {"name": "reader", "allowed-tools": ["Read", "Grep"]},
                        ]
                    ],
                }
            }
        }
        wf = get_workflow(config, "rw")
        assert wf is not None
        group = wf.steps[0]
        assert isinstance(group, ParallelGroup)
        assert wf.is_group_read_only(group) is False

    def test_no_tools_configured_assumes_writes(self) -> None:
        config = {
            "workflows": {
                "unknown": {
                    "triggers": {},
                    "skills": [["a", "b"]],
                }
            }
        }
        wf = get_workflow(config, "unknown")
        assert wf is not None
        group = wf.steps[0]
        assert isinstance(group, ParallelGroup)
        assert wf.is_group_read_only(group) is False
