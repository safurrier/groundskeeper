"""E2E tests that invoke the real `gk` binary via subprocess."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from .conftest import requires_claude, requires_gk


def run_gk(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run gk as a subprocess and return the result."""
    return subprocess.run(
        ["gk", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


@requires_gk
class TestGkListE2E:
    """Test `gk list` via real binary."""

    def test_list_shows_builtins(self, e2e_repo: Path) -> None:
        result = run_gk("list", cwd=e2e_repo)
        assert result.returncode == 0
        assert "codex-code-review" in result.stdout
        assert "context-files" in result.stdout

    def test_list_shows_local_skill(self, e2e_repo: Path) -> None:
        result = run_gk("list", cwd=e2e_repo)
        assert result.returncode == 0
        assert "repo-summary" in result.stdout
        assert "local" in result.stdout


@requires_gk
class TestGkCheckE2E:
    """Test `gk check` via real binary."""

    def test_check_local_skill(self, e2e_repo: Path) -> None:
        result = run_gk("check", "repo-summary", cwd=e2e_repo)
        assert result.returncode == 0

    def test_check_builtin_skill(self, e2e_repo: Path) -> None:
        result = run_gk("check", "codex-code-review", cwd=e2e_repo)
        assert result.returncode == 0

    def test_check_nonexistent_skill(self, e2e_repo: Path) -> None:
        result = run_gk("check", "does-not-exist", cwd=e2e_repo)
        assert result.returncode != 0


@requires_gk
class TestGkRenderE2E:
    """Test `gk render` via real binary."""

    def test_render_with_args(self, e2e_repo: Path) -> None:
        result = run_gk("render", "repo-summary", "--args", "brief", cwd=e2e_repo)
        assert result.returncode == 0
        assert "brief" in result.stdout
        # No frontmatter in output
        assert "---" not in result.stdout

    def test_render_without_args(self, e2e_repo: Path) -> None:
        result = run_gk("render", "repo-summary", cwd=e2e_repo)
        assert result.returncode == 0
        assert "Summarize this repository" in result.stdout

    def test_render_nonexistent_skill(self, e2e_repo: Path) -> None:
        result = run_gk("render", "does-not-exist", cwd=e2e_repo)
        assert result.returncode != 0


@requires_gk
class TestGkRunDryRunE2E:
    """Test `gk run --dry-run` via real binary."""

    def test_dry_run_with_args(self, e2e_repo: Path) -> None:
        result = run_gk(
            "run", "repo-summary", "--dry-run", "--args", "brief", cwd=e2e_repo
        )
        assert result.returncode == 0
        assert "brief" in result.stdout

    def test_dry_run_without_args(self, e2e_repo: Path) -> None:
        result = run_gk("run", "repo-summary", "--dry-run", cwd=e2e_repo)
        assert result.returncode == 0
        assert "Summarize this repository" in result.stdout


@requires_gk
class TestGkShowE2E:
    """Test `gk show` via real binary."""

    def test_show_local_skill(self, e2e_repo: Path) -> None:
        result = run_gk("show", "repo-summary", cwd=e2e_repo)
        assert result.returncode == 0
        assert "repo-summary" in result.stdout

    def test_show_builtin_skill(self, e2e_repo: Path) -> None:
        result = run_gk("show", "codex-code-review", cwd=e2e_repo)
        assert result.returncode == 0
        assert "codex-code-review" in result.stdout


@requires_gk
class TestGkInitE2E:
    """Test `gk init` via real binary."""

    def test_init_creates_config_only(self, tmp_path: Path) -> None:
        # Fresh repo without existing .groundskeeper
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        result = run_gk("init", "--non-interactive", cwd=tmp_path)
        assert result.returncode == 0
        assert (tmp_path / ".groundskeeper" / "config.yml").exists()
        # init should NOT generate CI workflow files
        assert not (tmp_path / ".github" / "workflows").exists()
        assert "gk generate" in result.stdout


@requires_gk
class TestGkGenerateE2E:
    """Test `gk generate` via real binary."""

    def test_generate_produces_valid_yaml(self, e2e_repo: Path) -> None:
        result = run_gk("generate", cwd=e2e_repo)
        assert result.returncode == 0
        wf_dir = e2e_repo / ".github" / "workflows"
        assert wf_dir.exists()
        # Should have generated at least gk_agent.yml
        yml_files = list(wf_dir.glob("*.yml"))
        assert len(yml_files) > 0
        # All generated YAML should be parseable
        for yml_file in yml_files:
            parsed = yaml.safe_load(yml_file.read_text())
            assert parsed is not None


@requires_gk
class TestGkGenerateScheduleE2E:
    """Test `gk generate` with schedule triggers via real binary."""

    def test_generate_schedule_workflow(self, e2e_schedule_repo: Path) -> None:
        result = run_gk("generate", cwd=e2e_schedule_repo)
        assert result.returncode == 0

        wf_path = e2e_schedule_repo / ".github" / "workflows" / "gk_health-check.yml"
        assert wf_path.exists()

        parsed = yaml.safe_load(wf_path.read_text())

        # Schedule trigger present with cron
        assert parsed["on"]["schedule"] == [{"cron": "0 8 * * 1"}]
        # workflow_dispatch auto-added
        assert "workflow_dispatch" in parsed["on"]
        # No PR-specific draft check
        job = next(iter(parsed["jobs"].values()))
        assert "if" not in job

    def test_generate_schedule_no_pr_concurrency(self, e2e_schedule_repo: Path) -> None:
        run_gk("generate", cwd=e2e_schedule_repo)
        wf_path = e2e_schedule_repo / ".github" / "workflows" / "gk_health-check.yml"
        content = wf_path.read_text()
        # Should NOT reference pull_request.number in concurrency group
        assert "pull_request.number" not in content

    def test_run_workflow_dry_run_schedule(self, e2e_schedule_repo: Path) -> None:
        result = run_gk(
            "run-workflow", "health-check", "--dry-run", cwd=e2e_schedule_repo
        )
        assert result.returncode == 0
        assert "Summarize this repository" in result.stdout


@requires_gk
class TestGkRunWorkflowE2E:
    """Test `gk run-workflow --dry-run` via real binary."""

    def test_run_workflow_dry_run(self, e2e_repo: Path) -> None:
        result = run_gk("run-workflow", "full-check", "--dry-run", cwd=e2e_repo)
        assert result.returncode == 0
        assert "repo-summary" in result.stdout
        assert "greeting" in result.stdout
        assert "completed successfully" in result.stdout

    def test_run_workflow_dry_run_with_args(self, e2e_repo: Path) -> None:
        result = run_gk(
            "run-workflow",
            "full-check",
            "--dry-run",
            "--args",
            "brief",
            cwd=e2e_repo,
        )
        assert result.returncode == 0
        assert "brief" in result.stdout

    def test_run_workflow_nonexistent(self, e2e_repo: Path) -> None:
        result = run_gk("run-workflow", "nope", "--dry-run", cwd=e2e_repo)
        assert result.returncode != 0


@requires_gk
class TestGkSkillPathE2E:
    """Test --skill-path via real binary."""

    def test_list_with_skill_path(self, e2e_repo: Path, tmp_path: Path) -> None:
        # Create an external skill in a separate directory
        ext_dir = tmp_path / "external-skills"
        ext_skill_dir = ext_dir / "ext-hello"
        ext_skill_dir.mkdir(parents=True)
        (ext_skill_dir / "SKILL.md").write_text(
            "---\nname: ext-hello\ndescription: External greeting\n---\n\nHello from external!\n"
        )
        result = run_gk("--skill-path", str(ext_dir), "list", cwd=e2e_repo)
        assert result.returncode == 0
        assert "ext-hello" in result.stdout
        assert "external" in result.stdout

    def test_run_dry_run_with_skill_path(self, e2e_repo: Path, tmp_path: Path) -> None:
        ext_dir = tmp_path / "external-skills"
        ext_skill_dir = ext_dir / "ext-hello"
        ext_skill_dir.mkdir(parents=True)
        (ext_skill_dir / "SKILL.md").write_text(
            "---\nname: ext-hello\ndescription: External greeting\n---\n\nHello from external!\n"
        )
        result = run_gk(
            "--skill-path",
            str(ext_dir),
            "run",
            "ext-hello",
            "--dry-run",
            cwd=e2e_repo,
        )
        assert result.returncode == 0
        assert "Hello from external!" in result.stdout


@requires_gk
@requires_claude
class TestGkRunClaudeE2E:
    """Test `gk run` with real Claude execution.

    These tests require:
    - ANTHROPIC_API_KEY environment variable
    - `claude` CLI on PATH
    - `gk` CLI on PATH
    """

    def test_run_repo_summary(self, e2e_repo: Path) -> None:
        # Unset CLAUDECODE to allow running claude from within a Claude session
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = run_gk("run", "repo-summary", "--args", "brief", cwd=e2e_repo, env=env)
        assert result.returncode == 0
        assert len(result.stdout.strip()) > 0
