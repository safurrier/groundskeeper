"""E2E tests that invoke the real `gk` binary via subprocess."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from .conftest import requires_claude, requires_gk

FACTORY_TASK_BODY = "## Factory Task\n\nSchema: 1\nKind: runnable\nMode: full\n\n## Dependencies\n\nNone"


def _pull_request_graphql(*, draft: bool = True, include: bool = True) -> str:
    nodes: list[dict[str, object]] = []
    if include:
        nodes.append(
            {
                "url": "https://github.com/target/repo/pull/9",
                "isDraft": draft,
                "state": "OPEN",
                "repository": {"nameWithOwner": "target/repo"},
            }
        )
    return json.dumps(
        {
            "data": {
                "repository": {
                    "issue": {
                        "closedByPullRequestsReferences": {
                            "nodes": nodes,
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                        }
                    }
                }
            }
        },
        separators=(",", ":"),
    )


def _issue_object_json(state: str, body: str = FACTORY_TASK_BODY) -> str:
    return json.dumps(
        {
            "number": 7,
            "title": "Do work",
            "body": body,
            "url": "https://github.com/source/queue/issues/7",
            "author": {"login": "alex"},
            "labels": [{"name": f"factory:{state}"}],
            "state": "open",
        }
    )


def _issue_json(state: str) -> str:
    return json.dumps([json.loads(_issue_object_json(state))])


def _factory_repo(tmp_path: Path, gh_output: str) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "factory"
    (repo / ".groundskeeper").mkdir(parents=True)
    (repo / ".groundskeeper/skills/issue-implementation").mkdir(parents=True)
    (repo / ".groundskeeper/skills/issue-implementation/SKILL.md").write_text(
        "---\nname: issue-implementation\ndescription: Implement one issue\n---\n\nImplement it."
    )
    subprocess.run(
        ["git", "init", "-q"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:target/repo.git"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / ".groundskeeper/config.yml").write_text(
        f"""automations:
  daily:
    source:
      type: github-issues
      repository: source/queue
      trusted-authors: [alex]
    target:
      repository: target/repo
      repository-path: {repo}
    runner:
      type: pi
      skill: issue-implementation
      approval: allow
      session: deterministic
    policy:
      concurrency: 1
      output: draft-pr
      merge: never
"""
    )
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    gh = binary_dir / "gh"
    gh.write_text(f"#!/bin/sh\nprintf '%s' '{gh_output}'\n")
    gh.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{binary_dir}:{env['PATH']}"
    return repo, env


def _factory_flow_repo(
    tmp_path: Path, state: str, pi_success: bool = True, draft: bool = True
) -> tuple[Path, dict[str, str]]:
    repo, env = _factory_repo(tmp_path, "[]")
    binary_dir = tmp_path / "bin"
    pr_created = tmp_path / "pr-created"
    issue = _issue_json(state)
    issue_view = _issue_object_json("running")
    pull_requests = _pull_request_graphql(draft=draft)
    no_pull_requests = _pull_request_graphql(include=False)
    (binary_dir / "gh").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f"  *\"issue view\"*) printf '%s' '{issue_view}' ;;\n"
        f"  *\"issue list\"*\"factory:{state}\"*) printf '%s' '{issue}' ;;\n"
        "  *\"issue list\"*) printf '%s' '[]' ;;\n"
        f"  *\"api graphql\"*) if [ -f '{pr_created}' ]; then "
        f"printf '%s' '{pull_requests}'; "
        f"else printf '%s' '{no_pull_requests}'; fi ;;\n"
        "  *) printf '%s' '' ;;\n"
        "esac\n"
    )
    pi = binary_dir / "pi"
    pi.write_text(
        "#!/bin/sh\n"
        + (
            f"touch '{pr_created}'\nprintf '%s' 'https://github.com/target/repo/pull/9'\n"
            if pi_success
            else "echo 'worker failed' >&2\nexit 1\n"
        )
    )
    pi.chmod(0o755)
    return repo, env


def _public_blocker_factory_repo(
    tmp_path: Path,
) -> tuple[Path, dict[str, str], Path]:
    repo, env = _factory_repo(tmp_path, "[]")
    binary_dir = tmp_path / "bin"
    gh_log = tmp_path / "public-blocker-gh.log"
    issue = _issue_json("ready")
    issue_view = _issue_object_json("running")
    no_pull_requests = _pull_request_graphql(include=False)
    (binary_dir / "gh").write_text(
        "#!/bin/sh\n"
        f"LOG='{gh_log}'\n"
        'case "$*" in\n'
        f"  *\"issue view\"*) printf '%s' '{issue_view}' ;;\n"
        f"  *\"issue list\"*\"factory:ready\"*) printf '%s' '{issue}' ;;\n"
        "  *\"issue list\"*) printf '%s' '[]' ;;\n"
        f"  *\"api graphql\"*) printf '%s' '{no_pull_requests}' ;;\n"
        '  *"issue comment"*) printf \'%s\' "$*" >> "$LOG" ;;\n'
        "  *) printf '%s' '' ;;\n"
        "esac\n"
    )
    marker = json.dumps(
        {
            "status": "blocked",
            "summary": "Focused tests pass; review requires a decision.",
            "next_action": "Approve the recorded skip, then resume.",
        },
        separators=(",", ":"),
    )
    pi = binary_dir / "pi"
    pi.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'private worker analysis must not be published'\n"
        f"printf '%s\\n' 'FACTORY_RESULT_JSON={marker}'\n"
    )
    pi.chmod(0o755)
    return repo, env, gh_log


def _deferred_factory_repo(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    repo, env = _factory_repo(tmp_path, "[]")
    binary_dir = tmp_path / "bin"
    state = tmp_path / "issue-state"
    state.write_text("ready")
    gh_log = tmp_path / "deferred-gh.log"
    pi_log = tmp_path / "deferred-pi.log"
    attempts = tmp_path / "pi-attempts"
    attempts.write_text("0")
    pr_created = tmp_path / "pr-created"
    issue_view = _issue_object_json("running")
    pull_requests = _pull_request_graphql()
    no_pull_requests = _pull_request_graphql(include=False)
    issue_prefix = json.dumps(
        [
            {
                "number": 7,
                "title": "Do work",
                "body": FACTORY_TASK_BODY,
                "url": "https://github.com/source/queue/issues/7",
                "author": {"login": "alex"},
                "state": "open",
                "labels": [{"name": "factory:"}],
            }
        ]
    )[:-5]
    (binary_dir / "gh").write_text(
        "#!/bin/sh\n"
        f"STATE='{state}'\nLOG='{gh_log}'\nCURRENT=$(cat \"$STATE\")\n"
        'case "$*" in\n'
        f"  *\"issue view\"*) printf '%s' '{issue_view}' ;;\n"
        '  *"issue list"*)\n'
        '    case "$*" in *"factory:$CURRENT"*) '
        f"printf '%s%s%s' '{issue_prefix}' \"$CURRENT\" '"
        + '"}]}]'
        + "' ;; *) printf '%s' '[]' ;; esac ;;\n"
        '  *"issue edit"*"factory:ready"*"factory:running"*) '
        'echo \'ready->running\' >> "$LOG"; echo running > "$STATE" ;;\n'
        '  *"issue edit"*"factory:deferred"*"factory:running"*) '
        'echo \'deferred->running\' >> "$LOG"; echo running > "$STATE" ;;\n'
        '  *"issue edit"*"factory:running"*"factory:deferred"*) '
        'echo \'running->deferred\' >> "$LOG"; echo deferred > "$STATE" ;;\n'
        '  *"issue edit"*"factory:running"*"factory:review"*) '
        'echo \'running->review\' >> "$LOG"; echo review > "$STATE" ;;\n'
        '  *"issue edit"*"factory:running"*"factory:blocked"*) '
        'echo \'running->blocked\' >> "$LOG"; echo blocked > "$STATE" ;;\n'
        '  *"issue comment"*) echo \'comment\' >> "$LOG" ;;\n'
        f"  *\"api graphql\"*) if [ -f '{pr_created}' ]; then "
        f"printf '%s' '{pull_requests}'; "
        f"else printf '%s' '{no_pull_requests}'; fi ;;\n"
        "esac\n"
    )
    pi = binary_dir / "pi"
    pi.write_text(
        "#!/bin/sh\n"
        f"echo \"cwd=$(pwd)\" >> '{pi_log}'\n"
        f"echo \"$*\" >> '{pi_log}'\n"
        f"COUNT=$(cat '{attempts}')\nCOUNT=$((COUNT + 1))\necho $COUNT > '{attempts}'\n"
        f"if [ \"$COUNT\" -lt 3 ]; then echo 'Codex usage limit reached; retry later' >&2; exit 1; fi\n"
        f"touch '{pr_created}'\n"
        "printf '%s' 'https://github.com/target/repo/pull/9'\n"
    )
    pi.chmod(0o755)
    return repo, env, gh_log, pi_log


def _stateful_factory_repo(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    repo, env = _factory_repo(tmp_path, "[]")
    binary_dir = tmp_path / "bin"
    state = tmp_path / "issue-state"
    state.write_text("ready")
    gh_log = tmp_path / "gh.log"
    pi_log = tmp_path / "pi.log"
    started = tmp_path / "pi-started"
    pr_created = tmp_path / "pr-created"
    issue_view = _issue_object_json("running")
    pull_requests = _pull_request_graphql()
    no_pull_requests = _pull_request_graphql(include=False)
    issue_prefix = json.dumps(
        [
            {
                "number": 7,
                "title": "Do work",
                "body": FACTORY_TASK_BODY,
                "url": "https://github.com/source/queue/issues/7",
                "author": {"login": "alex"},
                "state": "open",
                "labels": [{"name": "factory:"}],
            }
        ]
    )[:-5]
    (binary_dir / "gh").write_text(
        "#!/bin/sh\n"
        f"STATE='{state}'\nLOG='{gh_log}'\nCURRENT=$(cat \"$STATE\")\n"
        'case "$*" in\n'
        f"  *\"issue view\"*) printf '%s' '{issue_view}' ;;\n"
        '  *"issue list"*)\n'
        '    case "$*" in *"factory:$CURRENT"*) '
        f"printf '%s%s%s' '{issue_prefix}' \"$CURRENT\" '" + "\"}]}]' ;; "
        "*) printf '%s' '[]' ;; esac ;;\n"
        '  *"issue edit"*"factory:ready"*"factory:running"*) '
        'echo \'ready->running\' >> "$LOG"; echo running > "$STATE" ;;\n'
        '  *"issue edit"*"factory:running"*"factory:review"*) '
        'echo \'running->review\' >> "$LOG"; echo review > "$STATE" ;;\n'
        '  *"issue comment"*) echo \'comment\' >> "$LOG" ;;\n'
        f"  *\"api graphql\"*) if [ -f '{pr_created}' ]; then "
        f"printf '%s' '{pull_requests}'; "
        f"else printf '%s' '{no_pull_requests}'; fi ;;\n"
        "esac\n"
    )
    pi = binary_dir / "pi"
    pi.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{pi_log}'\n"
        f"if [ ! -f '{started}' ]; then touch '{started}'; kill -9 $PPID; exit 137; fi\n"
        f"touch '{pr_created}'\n"
        "printf '%s' 'https://github.com/target/repo/pull/9'\n"
    )
    pi.chmod(0o755)
    return repo, env, gh_log, pi_log


@requires_gk
class TestAutomationE2E:
    def test_list_show_and_validate_through_real_process(self, tmp_path: Path) -> None:
        repo, env = _factory_repo(tmp_path, "[]")
        listed = run_gk("automation", "list", "--json", cwd=repo, env=env)
        shown = run_gk("automation", "show", "daily", "--json", cwd=repo, env=env)
        validated = run_gk(
            "automation", "validate", "daily", "--json", cwd=repo, env=env
        )
        assert listed.returncode == shown.returncode == validated.returncode == 0
        assert json.loads(listed.stdout)["data"]["automations"][0]["name"] == "daily"
        shown_automation = json.loads(shown.stdout)["data"]["automation"]
        assert shown_automation["runner"]["skill"] == "issue-implementation"
        assert shown_automation["source"]["repository"] == "source/queue"
        assert shown_automation["target"]["repository"] == "target/repo"
        assert shown_automation["policy"] == {
            "concurrency": 1,
            "output": "draft-pr",
            "merge": "never",
            "link_source_issue": True,
            "include_factory_session": True,
            "include_pi_resume": True,
        }
        validated_automation = json.loads(validated.stdout)["data"]["automations"][0]
        assert validated_automation["source"]["repository"] == "source/queue"
        assert validated_automation["target"]["repository"] == "target/repo"
        assert json.loads(validated.stdout)["version"] == 2

    def test_validate_resolves_isolated_checkout_base_through_real_process(
        self, tmp_path: Path
    ) -> None:
        repo, env = _factory_repo(tmp_path, "[]")
        (repo / "tracked.txt").write_text("base\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "base",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=repo,
            check=True,
        )
        config_path = repo / ".groundskeeper/config.yml"
        config = yaml.safe_load(config_path.read_text())
        config["automations"]["daily"]["target"]["checkout"] = {
            "mode": "isolated-worktree",
            "base-ref": "origin/main",
            "refresh": "none",
            "branch-prefix": "changes/task",
        }
        config["automations"]["daily"]["policy"].update(
            {
                "link-source-issue": False,
                "include-factory-session": False,
                "include-pi-resume": False,
            }
        )
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))

        validated = run_gk(
            "automation", "validate", "daily", "--json", cwd=repo, env=env
        )

        assert validated.returncode == 0
        checkout = json.loads(validated.stdout)["data"]["automations"][0]["target"][
            "checkout"
        ]
        assert checkout == {
            "mode": "isolated-worktree",
            "base_ref": "origin/main",
            "refresh": "none",
            "branch_prefix": "changes/task",
        }
        assert json.loads(validated.stdout)["data"]["automations"][0]["policy"] == {
            "concurrency": 1,
            "output": "draft-pr",
            "merge": "never",
            "link_source_issue": False,
            "include_factory_session": False,
            "include_pi_resume": False,
        }

    def test_live_tick_fetches_immutable_base_without_modifying_dirty_donor(
        self, tmp_path: Path
    ) -> None:
        repo, env, _, pi_log = _deferred_factory_repo(tmp_path)
        state_home = tmp_path / "state"
        env["GROUNDSKEEPER_STATE_HOME"] = str(state_home)
        real_git = shutil.which("git")
        assert real_git is not None
        remote = tmp_path / "remote.git"
        updater = tmp_path / "updater"
        subprocess.run([real_git, "init", "--bare", "-q", remote], check=True)
        subprocess.run(
            [real_git, "remote", "set-url", "origin", remote], cwd=repo, check=True
        )
        subprocess.run([real_git, "switch", "-c", "main"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("donor base\n")
        subprocess.run([real_git, "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(
            [
                real_git,
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "donor base",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            [real_git, "push", "-q", "-u", "origin", "main"], cwd=repo, check=True
        )
        donor_head = subprocess.run(
            [real_git, "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (repo / "unrelated.txt").write_text("preserve me\n")

        subprocess.run(
            [real_git, "clone", "-q", "-b", "main", remote, updater], check=True
        )
        (updater / "tracked.txt").write_text("remote advance\n")
        subprocess.run([real_git, "add", "tracked.txt"], cwd=updater, check=True)
        subprocess.run(
            [
                real_git,
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "remote advance",
            ],
            cwd=updater,
            check=True,
        )
        subprocess.run(
            [real_git, "push", "-q", "origin", "main"], cwd=updater, check=True
        )
        remote_head = subprocess.run(
            [real_git, "rev-parse", "HEAD"],
            cwd=updater,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        config_path = repo / ".groundskeeper/config.yml"
        config = yaml.safe_load(config_path.read_text())
        config["automations"]["daily"]["target"]["checkout"] = {
            "mode": "isolated-worktree",
            "base-ref": "origin/main",
            "refresh": "fetch",
        }
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))

        binary_dir = tmp_path / "bin"
        git_wrapper = binary_dir / "git"
        git_wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$*" = "remote get-url origin" ]; then\n'
            "  printf '%s\\n' 'git@github.com:target/repo.git'\n"
            "  exit 0\n"
            "fi\n"
            f"exec '{real_git}' \"$@\"\n"
        )
        git_wrapper.chmod(0o755)

        first = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)

        (updater / "tracked.txt").write_text("remote moves again\n")
        subprocess.run([real_git, "add", "tracked.txt"], cwd=updater, check=True)
        subprocess.run(
            [
                real_git,
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "remote moves again",
            ],
            cwd=updater,
            check=True,
        )
        subprocess.run(
            [real_git, "push", "-q", "origin", "main"], cwd=updater, check=True
        )
        newer_remote_head = subprocess.run(
            [real_git, "rev-parse", "HEAD"],
            cwd=updater,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        second = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        third = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)

        assert first.returncode == second.returncode == third.returncode == 0
        assert [
            json.loads(first.stdout)["status"],
            json.loads(second.stdout)["status"],
            json.loads(third.stdout)["status"],
        ] == ["deferred", "deferred", "review"]
        pi_calls = pi_log.read_text().splitlines()
        workspaces = {
            line.removeprefix("cwd=") for line in pi_calls if line.startswith("cwd=")
        }
        assert len(workspaces) == 1
        workspace = Path(workspaces.pop())
        assert workspace != repo
        assert workspace.is_relative_to(state_home / "groundskeeper" / "worktrees")
        prompt_log = "\n".join(pi_calls)
        assert prompt_log.count("TARGET_CHECKOUT_MODE: isolated-worktree") == 3
        assert prompt_log.count("TARGET_BASE_REF: origin/main") == 3
        assert prompt_log.count(f"TARGET_BASE_SHA: {remote_head}") == 3
        assert newer_remote_head not in prompt_log
        assert (
            subprocess.run(
                [real_git, "rev-parse", "HEAD"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            == remote_head
        )
        assert (
            subprocess.run(
                [real_git, "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            == donor_head
        )
        assert (repo / "tracked.txt").read_text() == "donor base\n"
        assert (repo / "unrelated.txt").read_text() == "preserve me\n"

    def test_live_tick_reuses_managed_linked_worktree_through_real_process(
        self, tmp_path: Path
    ) -> None:
        repo, env, gh_log, pi_log = _deferred_factory_repo(tmp_path)
        managed = tmp_path / "managed"
        state_home = tmp_path / "state"
        env["GROUNDSKEEPER_STATE_HOME"] = str(state_home)
        subprocess.run(["git", "switch", "-q", "-c", "main"], cwd=repo, check=True)
        config_path = repo / ".groundskeeper/config.yml"
        config = yaml.safe_load(config_path.read_text())
        config["automations"]["daily"]["target"]["repository-path"] = str(managed)
        config["automations"]["daily"]["target"]["checkout"] = {
            "mode": "managed-worktree",
            "base-ref": "origin/main",
            "refresh": "none",
        }
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "factory config",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", managed, "HEAD"],
            cwd=repo,
            check=True,
        )

        first = run_gk("automation", "tick", "daily", "--json", cwd=managed, env=env)
        second = run_gk("automation", "tick", "daily", "--json", cwd=managed, env=env)
        third = run_gk("automation", "tick", "daily", "--json", cwd=managed, env=env)

        assert first.returncode == second.returncode == third.returncode == 0
        assert [
            json.loads(first.stdout)["status"],
            json.loads(second.stdout)["status"],
            json.loads(third.stdout)["status"],
        ] == ["deferred", "deferred", "review"]
        pi_calls = pi_log.read_text().splitlines()
        assert {line for line in pi_calls if line.startswith("cwd=")} == {
            f"cwd={managed}"
        }
        assert "\n".join(pi_calls).count("TARGET_CHECKOUT_MODE: managed-worktree") == 3
        assert not (state_home / "groundskeeper" / "worktrees").exists()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=managed,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert branch.startswith("groundskeeper/task-")

        wip = managed / "wip.txt"
        wip.write_text("task seven work\n")
        (tmp_path / "issue-state").write_text("ready")
        (tmp_path / "pr-created").unlink()
        gh = tmp_path / "bin/gh"
        gh.write_text(
            gh.read_text()
            .replace('"number": 7', '"number": 8')
            .replace("/issues/7", "/issues/8")
        )
        transitions_before = gh_log.read_text()

        waiting = run_gk("automation", "tick", "daily", "--json", cwd=managed, env=env)

        waiting_payload = json.loads(waiting.stdout)
        assert waiting.returncode == 4
        assert waiting_payload["status"] == "not-claimed"
        assert (
            "uncommitted work for another task"
            in waiting_payload["data"]["operator_detail"]
        )
        assert gh_log.read_text() == transitions_before
        assert wip.read_text() == "task seven work\n"

    def test_post_claim_checkout_failure_blocks_with_operator_diagnostic(
        self, tmp_path: Path
    ) -> None:
        repo, env, gh_log, _ = _deferred_factory_repo(tmp_path)
        managed = tmp_path / "managed"
        subprocess.run(["git", "switch", "-q", "-c", "main"], cwd=repo, check=True)
        config_path = repo / ".groundskeeper/config.yml"
        config = yaml.safe_load(config_path.read_text())
        config["automations"]["daily"]["target"]["repository-path"] = str(managed)
        config["automations"]["daily"]["target"]["checkout"] = {
            "mode": "managed-worktree",
            "base-ref": "origin/main",
            "refresh": "none",
        }
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "factory config",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", managed, "HEAD"],
            cwd=repo,
            check=True,
        )
        real_git = shutil.which("git")
        assert real_git is not None
        git_wrapper = tmp_path / "bin/git"
        git_wrapper.write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            '  "switch --create groundskeeper/task-"*) '
            "echo 'injected checkout failure' >&2; exit 1 ;;\n"
            f"  *) exec '{real_git}' \"$@\" ;;\n"
            "esac\n"
        )
        git_wrapper.chmod(0o755)

        result = run_gk("automation", "tick", "daily", "--json", cwd=managed, env=env)

        payload = json.loads(result.stdout)
        assert result.returncode == 5
        assert payload["status"] == "blocked"
        assert (
            payload["data"]["detail"]
            == "Target workspace preparation failed before worker launch"
        )
        assert "injected checkout failure" in payload["data"]["operator_detail"]
        assert gh_log.read_text().splitlines()[:2] == [
            "ready->running",
            "running->blocked",
        ]

    def test_scheduled_run_uses_origin_snapshot_and_preserves_dirty_source(
        self, tmp_path: Path
    ) -> None:
        repo, env = _factory_flow_repo(tmp_path, "ready")
        state_home = tmp_path / "state"
        env["GROUNDSKEEPER_STATE_HOME"] = str(state_home)
        real_git = shutil.which("git")
        assert real_git is not None
        remote = tmp_path / "source.git"
        subprocess.run([real_git, "init", "--bare", "-q", remote], check=True)
        subprocess.run([real_git, "switch", "-c", "main"], cwd=repo, check=True)
        subprocess.run([real_git, "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                real_git,
                "-c",
                "user.name=Groundskeeper Test",
                "-c",
                "user.email=groundskeeper@example.com",
                "commit",
                "-qm",
                "scheduled source",
            ],
            cwd=repo,
            check=True,
        )
        source_commit = subprocess.run(
            [real_git, "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            [real_git, "remote", "set-url", "origin", remote], cwd=repo, check=True
        )
        subprocess.run(
            [real_git, "push", "-q", "-u", "origin", "main"], cwd=repo, check=True
        )
        config_path = repo / ".groundskeeper/config.yml"
        config_path.write_text("dirty: [invalid\n")
        (repo / "local-notes.txt").write_text("preserve me\n")

        binary_dir = tmp_path / "bin"
        git_wrapper = binary_dir / "git"
        git_wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$*" = "remote get-url origin" ]; then\n'
            "  printf '%s\\n' 'git@github.com:target/repo.git'\n"
            "  exit 0\n"
            "fi\n"
            f"exec '{real_git}' \"$@\"\n"
        )
        git_wrapper.chmod(0o755)

        scheduled_args = (
            "automation",
            "--config",
            ".groundskeeper/config.yml",
            "run-scheduled",
            "daily",
            "--schedule-id",
            "test-factory",
            "--source-repository-path",
            str(repo),
            "--daily-attempt-limit",
            "1",
            "--rotation",
            "fixed",
            "--json",
        )
        result = run_gk(
            *scheduled_args,
            cwd=repo,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
        assert payload["data"]["execution_source"]["commit"] == source_commit
        workspace = Path(payload["data"]["execution_source"]["workspace"])
        assert workspace.is_relative_to(
            state_home / "groundskeeper" / "execution-sources"
        )
        assert (
            yaml.safe_load((workspace / ".groundskeeper/config.yml").read_text())[
                "automations"
            ]["daily"]["runner"]["skill"]
            == "issue-implementation"
        )
        assert payload["data"]["runs"][0]["status"] == "review"
        assert payload["data"]["consumed_today"] == 1
        assert config_path.read_text() == "dirty: [invalid\n"
        assert (repo / "local-notes.txt").read_text() == "preserve me\n"

        quota_path = (
            state_home
            / "groundskeeper"
            / "schedules"
            / "test-factory"
            / "daily-quota.json"
        )
        quota_path.write_text('{"date":null,"consumed":1}')
        corrupt = run_gk(*scheduled_args, cwd=repo, env=env)

        assert corrupt.returncode == 2
        corrupt_payload = json.loads(corrupt.stdout)
        assert corrupt_payload["status"] == "error"
        assert corrupt_payload["exit_code"] == 2
        assert "Traceback" not in corrupt.stderr

        state_parent_file = tmp_path / "invalid-state-root"
        state_parent_file.write_text("occupied")
        invalid_state_env = dict(env)
        invalid_state_env["GROUNDSKEEPER_STATE_HOME"] = str(state_parent_file)
        invalid_state = run_gk(*scheduled_args, cwd=repo, env=invalid_state_env)

        assert invalid_state.returncode == 2
        invalid_state_payload = json.loads(invalid_state.stdout)
        assert invalid_state_payload["status"] == "error"
        assert invalid_state_payload["exit_code"] == 2
        assert "Traceback" not in invalid_state.stderr

    def test_dry_run_is_compact_and_does_not_launch_pi_or_write_state(
        self, tmp_path: Path
    ) -> None:
        repo, env = _factory_flow_repo(tmp_path, "ready")
        state_home = tmp_path / "state"
        env["GROUNDSKEEPER_STATE_HOME"] = str(state_home)

        result = run_gk(
            "automation", "tick", "daily", "--dry-run", "--json", cwd=repo, env=env
        )

        assert result.returncode == 0
        assert json.loads(result.stdout)["status"] == "would-dispatch"
        assert "Factory Task" not in result.stdout
        assert not state_home.exists()

    def test_inspect_reports_typed_admission_without_mutation(
        self, tmp_path: Path
    ) -> None:
        repo, env = _factory_flow_repo(tmp_path, "ready")
        binary_dir = Path(env["PATH"].split(os.pathsep)[0])
        gh_log = tmp_path / "inspect-gh.log"
        pi_started = tmp_path / "inspect-pi-started"
        issue = _issue_json("ready")
        (binary_dir / "gh").write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{gh_log}'\n"
            'case "$*" in\n'
            f"  *\"issue list\"*\"factory:ready\"*) printf '%s' '{issue}' ;;\n"
            "  *\"issue list\"*) printf '%s' '[]' ;;\n"
            '  *"issue edit"*|*"issue comment"*) exit 99 ;;\n'
            "esac\n"
        )
        (binary_dir / "pi").write_text(f"#!/bin/sh\ntouch '{pi_started}'\nexit 99\n")
        (binary_dir / "pi").chmod(0o755)

        result = run_gk("automation", "inspect", "daily", "--json", cwd=repo, env=env)

        assert result.returncode == 0
        data = json.loads(result.stdout)["data"]
        task = data["tasks"][0]
        assert data["source"]["repository"] == "source/queue"
        assert data["target"]["repository"] == "target/repo"
        assert task["source"] == {"repository": "source/queue", "issue": 7}
        assert task["target"] == {"repository": "target/repo"}
        assert task["admitted"] is True
        assert task["kind"] == "runnable"
        assert task["mode"] == "full"
        assert task["dependency_status"] == "resolved"
        commands = gh_log.read_text()
        assert "issue edit" not in commands
        assert "issue comment" not in commands
        assert not pi_started.exists()

    def test_malformed_contract_blocks_before_pi_in_live_tick(
        self, tmp_path: Path
    ) -> None:
        repo, env = _factory_repo(tmp_path, "[]")
        binary_dir = tmp_path / "bin"
        pi_started = tmp_path / "pi-started"
        bad_body = "## Dependencies\\n\\nNone"
        issue = json.dumps(
            [
                {
                    "number": 7,
                    "title": "Bad contract",
                    "body": bad_body,
                    "url": "https://github.com/source/queue/issues/7",
                    "author": {"login": "alex"},
                    "labels": [{"name": "factory:ready"}],
                    "state": "open",
                }
            ]
        )
        issue_view = _issue_object_json("running", bad_body)
        no_pull_requests = _pull_request_graphql(include=False)
        (binary_dir / "gh").write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            f"  *\"issue view\"*) printf '%s' '{issue_view}' ;;\n"
            f"  *\"issue list\"*\"factory:ready\"*) printf '%s' '{issue}' ;;\n"
            "  *\"issue list\"*) printf '%s' '[]' ;;\n"
            '  *"issue edit"*|*"issue comment"*) : ;;\n'
            f"  *\"api graphql\"*) printf '%s' '{no_pull_requests}' ;;\n"
            "esac\n"
        )
        pi = binary_dir / "pi"
        pi.write_text(f"#!/bin/sh\\ntouch '{pi_started}'\\n")
        pi.chmod(0o755)

        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)

        assert result.returncode == 5
        assert json.loads(result.stdout)["status"] == "blocked"
        assert not pi_started.exists()

    def test_no_work_json_through_real_process(self, tmp_path: Path) -> None:
        repo, env = _factory_repo(tmp_path, "[]")
        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "no-work"
        assert payload["version"] == 2

    def test_malformed_gh_json_is_structured_error(self, tmp_path: Path) -> None:
        repo, env = _factory_repo(tmp_path, "not-json")
        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert result.returncode == 2
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"

    def test_success_dispatches_and_returns_pr(self, tmp_path: Path) -> None:
        repo, env = _factory_flow_repo(tmp_path, "ready")
        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "review"
        assert (
            payload["data"]["pull_request_url"]
            == "https://github.com/target/repo/pull/9"
        )
        assert payload["data"]["source"]["repository"] == "source/queue"
        assert payload["data"]["target"]["repository"] == "target/repo"

    def test_non_draft_closing_pr_is_blocked_as_policy_violation(
        self, tmp_path: Path
    ) -> None:
        repo, env = _factory_flow_repo(tmp_path, "ready", draft=False)
        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert result.returncode == 5
        assert "not a draft" in json.loads(result.stdout)["data"]["detail"]

    def test_worker_failure_is_blocked(self, tmp_path: Path) -> None:
        repo, env = _factory_flow_repo(tmp_path, "ready", pi_success=False)
        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert result.returncode == 5
        assert json.loads(result.stdout)["status"] == "blocked"

    def test_public_blocker_marker_reaches_comment_without_private_output(
        self, tmp_path: Path
    ) -> None:
        repo, env, gh_log = _public_blocker_factory_repo(tmp_path)

        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)

        assert result.returncode == 5
        payload = json.loads(result.stdout)
        assert payload["status"] == "blocked"
        assert "Focused tests pass" in payload["data"]["detail"]
        comment = gh_log.read_text()
        assert "Focused tests pass" in comment
        assert "Approve the recorded skip" in comment
        assert "private worker analysis" not in comment
        assert "FACTORY_RESULT_JSON" not in comment

    def test_running_issue_resumes_to_review(self, tmp_path: Path) -> None:
        repo, env = _factory_flow_repo(tmp_path, "running")
        result = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert result.returncode == 0
        assert json.loads(result.stdout)["status"] == "review"

    def test_transient_failure_defers_retries_and_eventually_reaches_review(
        self, tmp_path: Path
    ) -> None:
        repo, env, gh_log, pi_log = _deferred_factory_repo(tmp_path)

        first = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        second = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        third = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)

        first_payload = json.loads(first.stdout)
        second_payload = json.loads(second.stdout)
        third_payload = json.loads(third.stdout)
        assert first.returncode == second.returncode == third.returncode == 0
        assert [
            first_payload["status"],
            second_payload["status"],
            third_payload["status"],
        ] == [
            "deferred",
            "deferred",
            "review",
        ]
        assert (
            first_payload["data"]["session_id"] == second_payload["data"]["session_id"]
        )
        assert (
            second_payload["data"]["session_id"] == third_payload["data"]["session_id"]
        )
        assert first_payload["data"]["resume_command"].startswith("pi --session ")
        assert first_payload["data"]["task"]["state"] == "deferred"
        assert second_payload["data"]["task"]["state"] == "deferred"
        assert third_payload["data"]["task"]["state"] == "review"
        assert gh_log.read_text().splitlines() == [
            "ready->running",
            "running->deferred",
            "comment",
            "deferred->running",
            "running->deferred",
            "comment",
            "deferred->running",
            "running->review",
            "comment",
        ]
        pi_calls = pi_log.read_text()
        assert pi_calls.count("--session-id ") == 3
        assert pi_calls.count("RECOVERY_CONTEXT: Resume the deterministic session") == 2

    def test_crash_after_claim_resumes_same_session_without_second_claim(
        self, tmp_path: Path
    ) -> None:
        repo, env, gh_log, pi_log = _stateful_factory_repo(tmp_path)
        first = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert first.returncode < 0

        second = run_gk("automation", "tick", "daily", "--json", cwd=repo, env=env)
        assert second.returncode == 0
        assert json.loads(second.stdout)["data"]["pull_request_url"].endswith("/pull/9")

        mutations = gh_log.read_text().splitlines()
        assert mutations == ["ready->running", "running->review", "comment"]
        pi_log_text = pi_log.read_text()
        pi_calls = [
            line
            for line in pi_log_text.splitlines()
            if line.startswith("--session-id ")
        ]
        assert len(pi_calls) == 2
        first_session = pi_calls[0].split("--session-id ", 1)[1].split()[0]
        second_session = pi_calls[1].split("--session-id ", 1)[1].split()[0]
        assert first_session == second_session
        assert "RECOVERY_CONTEXT: Resume the deterministic session" in pi_log_text


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
