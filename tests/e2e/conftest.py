"""E2E test fixtures and skip logic for Groundskeeper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

requires_gk = pytest.mark.skipif(
    shutil.which("gk") is None,
    reason="gk CLI not installed (run: mise run install)",
)

requires_claude = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY") or shutil.which("claude") is None,
    reason="Claude not available (need ANTHROPIC_API_KEY + claude CLI)",
)


REPO_SUMMARY_SKILL = """\
---
name: repo-summary
description: Summarize a repository's structure and purpose
argument-hint: "[detail-level]"
allowed-tools:
  - Read
  - Glob
tags: [utility, summary]
---

Summarize this repository in under 200 words at detail level: $ARGUMENTS

List the main files and their purpose. Be concise.
"""

BASIC_CONFIG = """\
version: 1
runner: claude-code
ci: github-actions
workflows:
  summary:
    triggers:
      pull_request: [opened, synchronize]
    skills:
      - repo-summary
"""


@pytest.fixture
def e2e_repo(tmp_path: Path) -> Path:
    """Create a real git repo with a test skill for e2e testing."""
    # Init git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    # Create project files
    (tmp_path / "README.md").write_text("# Test Project\nA minimal test repo.\n")
    (tmp_path / "main.py").write_text(
        'def main():\n    print("hello")\n\nif __name__ == "__main__":\n    main()\n'
    )
    (tmp_path / "utils.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )

    # Create skill
    skill_dir = tmp_path / ".groundskeeper" / "skills" / "repo-summary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(REPO_SUMMARY_SKILL)

    # Create config
    (tmp_path / ".groundskeeper" / "config.yml").write_text(BASIC_CONFIG)

    # Initial commit (skip hooks — this is a test fixture, not user code)
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "initial"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    return tmp_path


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply 'e2e' marker to all tests in tests/e2e/."""
    e2e_dir = Path(__file__).parent
    for item in items:
        if e2e_dir in Path(item.fspath).parents or Path(item.fspath) == e2e_dir:
            item.add_marker(pytest.mark.e2e)
