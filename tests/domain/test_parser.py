"""Tests for the SKILL.md parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from groundskeeper.domain.errors import SkillValidationError
from groundskeeper.domain.parser import parse_skill_file


class TestParseValidSkills:
    def test_parse_basic_skill(self, fixtures_dir: Path):
        skill = parse_skill_file(fixtures_dir / "skills" / "valid-basic" / "SKILL.md")
        assert skill.name == "valid-basic"
        assert skill.description == "A basic valid skill for testing"
        assert "basic skill body" in skill.body
        assert skill.source.kind == "local"

    def test_parse_full_skill(self, fixtures_dir: Path):
        skill = parse_skill_file(fixtures_dir / "skills" / "valid-full" / "SKILL.md")
        assert skill.name == "valid-full"
        assert skill.description == "A fully-featured skill for testing"
        assert skill.argument_hint == "[mode] [scope]"
        assert skill.allowed_tools == ["Read", "Grep", "Glob", "Bash"]
        assert skill.tags == ["review", "quality"]
        assert skill.triggers == {
            "pull_request": ["ready_for_review", "synchronize", "reopened"]
        }
        assert "$ARGUMENTS" in skill.body

    def test_parse_builtin_source_kind(self, fixtures_dir: Path):
        skill = parse_skill_file(
            fixtures_dir / "skills" / "valid-basic" / "SKILL.md",
            source_kind="builtin",
        )
        assert skill.source.kind == "builtin"

    @pytest.mark.parametrize(
        "frontmatter_dict,expected_name",
        [
            ({"name": "simple", "description": "A skill"}, "simple"),
            (
                {"name": "with-tags", "description": "Tagged", "tags": ["a", "b"]},
                "with-tags",
            ),
            (
                {
                    "name": "with-tools",
                    "description": "Tooled",
                    "allowed-tools": ["Read"],
                },
                "with-tools",
            ),
        ],
    )
    def test_parse_valid_frontmatter_variations(
        self, tmp_path: Path, frontmatter_dict: dict, expected_name: str
    ):
        import yaml

        p = tmp_path / "SKILL.md"
        fm = yaml.dump(frontmatter_dict, default_flow_style=False)
        p.write_text(f"---\n{fm}---\n\nSome body text.")
        skill = parse_skill_file(p)
        assert skill.name == expected_name


class TestParseInvalidSkills:
    def test_missing_name(self, fixtures_dir: Path):
        with pytest.raises(SkillValidationError, match="name is required"):
            parse_skill_file(fixtures_dir / "skills" / "invalid-no-name" / "SKILL.md")

    def test_empty_body(self, fixtures_dir: Path):
        with pytest.raises(SkillValidationError, match="body is empty"):
            parse_skill_file(fixtures_dir / "skills" / "invalid-no-body" / "SKILL.md")

    def test_bad_frontmatter(self, fixtures_dir: Path):
        with pytest.raises(SkillValidationError):
            parse_skill_file(
                fixtures_dir / "skills" / "invalid-bad-frontmatter" / "SKILL.md"
            )

    def test_missing_description(self, tmp_path: Path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: no-desc\n---\n\nSome body.")
        with pytest.raises(SkillValidationError, match="description is required"):
            parse_skill_file(p)

    def test_non_kebab_case_name(self, tmp_path: Path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: NotKebab\ndescription: x\n---\n\nSome body.")
        with pytest.raises(SkillValidationError, match="kebab-case"):
            parse_skill_file(p)

    def test_no_frontmatter_at_all(self, tmp_path: Path):
        p = tmp_path / "SKILL.md"
        p.write_text("Just some text, no frontmatter.")
        with pytest.raises(SkillValidationError, match="missing or invalid"):
            parse_skill_file(p)

    def test_frontmatter_not_a_mapping(self, tmp_path: Path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\n- a list\n- not a dict\n---\n\nBody.")
        with pytest.raises(SkillValidationError, match="must be a YAML mapping"):
            parse_skill_file(p)

    @pytest.mark.parametrize(
        "name,expected_error",
        [
            ("UPPER", "kebab-case"),
            ("under_score", "kebab-case"),
            ("has space", "kebab-case"),
            ("-leading-dash", "kebab-case"),
            ("trailing-dash-", "kebab-case"),
        ],
    )
    def test_invalid_name_formats(self, tmp_path: Path, name: str, expected_error: str):
        p = tmp_path / "SKILL.md"
        p.write_text(f"---\nname: {name}\ndescription: x\n---\n\nBody.")
        with pytest.raises(SkillValidationError, match=expected_error):
            parse_skill_file(p)


class TestParseMetadata:
    def test_extra_fields_captured_in_metadata(self, tmp_path: Path):
        import yaml

        fm = yaml.dump(
            {
                "name": "meta-test",
                "description": "Testing metadata",
                "custom-field": "custom-value",
                "another": 42,
            },
            default_flow_style=False,
        )
        p = tmp_path / "SKILL.md"
        p.write_text(f"---\n{fm}---\n\nBody text.")
        skill = parse_skill_file(p)
        assert skill.metadata["custom-field"] == "custom-value"
        assert skill.metadata["another"] == 42
        assert "name" not in skill.metadata
        assert "description" not in skill.metadata
