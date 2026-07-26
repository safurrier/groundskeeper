---
id: config-and-skills
title: Config & Skills Deep Reference
description: >
  config.yml format, trigger types, allowed-tools precedence, SKILL.md frontmatter spec,
  directory layout, $ARGUMENTS substitution, and skill resolution order.
index:
  - id: groundskeeperconfigyml
  - id: triggers
  - id: skillmd-format
  - id: skill-resolution-order
---

# Config & Skills Deep Reference

## Local automations

`automations:` is separate from `workflows:`. Workflows describe skill chains
for local or GitHub Actions use; automations select trusted work from a tracker
and dispatch it through one named Groundskeeper skill.

The initial provider pair is `source.type: github-issues` and `runner.type: pi`.
A Pi runner must explicitly name `skill`, use `approval: allow`, and use
`session: deterministic`. Every automation requires an explicit `source` and
`target`. Source and target repositories must be canonical `owner/repo`
identities. The source requires `repository` and at least one `trusted-author`;
the target requires `repository` and an absolute `repository-path`. Validation,
dry-run, and live tick prove that path is a Git worktree whose `origin` GitHub
HTTPS or SSH remote matches the configured target before tracker access. An
optional strict `target.checkout` block selects an existing checkout or an
isolated Groundskeeper-managed task worktree. Safety
policy is strict: concurrency is `1`, output is `draft-pr`, and merge is `never`.
Groundskeeper validates this policy and enforces the accepted-result
postcondition: only an open draft pull request in the target repository that
closes the exact repository-qualified source issue reaches review. It does not
sandbox a user-authorized Pi process. An automation skill receives normalized
`TASK_ID`, `TASK_TITLE`, `TASK_BODY`, `TASK_URL`, target `REPOSITORY`, typed
`FACTORY_CLOSING_REFERENCE`, `RECOVERY_CONTEXT`, and `POLICY_CONCURRENCY`,
`POLICY_OUTPUT`, and `POLICY_MERGE`; checkout-aware workers also receive
`TARGET_CHECKOUT_MODE`, `TARGET_BASE_REF`, `TARGET_BASE_SHA`, and
`TARGET_REFRESH`, plus the selected `TARGET_WORKSPACE_PATH`. Ordinary skill
rendering is unchanged.

Every GitHub Issues automation task must begin with exactly these first two H2
sections. The contract sections cannot contain comments or fenced examples:

```markdown
## Factory Task

Schema: 1
Kind: runnable
Mode: focused

## Dependencies

None
```

`tracking` issues coordinate related work and are never runnable. A `runnable`
issue requires a `focused` or `full` mode. Dependencies are either exactly `None` or one full
GitHub issue or pull-request URL per bullet. Before every ready, running, or
deferred worker invocation, Groundskeeper parses this contract, resolves every
dependency, and blocks invalid, tracking, inaccessible, unresolved, or
closed-unmerged dependency work before Pi starts. This is a clean-break contract:
issues without it are blocked for recapture rather than inferred or upgraded.
The configured skill receives typed `FACTORY_TASK_KIND`,
`FACTORY_EXECUTION_MODE`, and `FACTORY_DEPENDENCY_STATUS=resolved` context.
Use `gk automation inspect NAME --json` to inspect admission without mutation.

A GitHub issue source has five distinct lifecycle labels. Defaults are shown
below; every override must be a non-empty string and all five must be distinct:

```yaml
source:
  type: github-issues
  repository: example/work-factory
  trusted-authors: [maintainer]
  labels:
    ready: factory:ready
    running: factory:running
    deferred: factory:deferred
    review: factory:review
    blocked: factory:blocked
target:
  repository: example/widgets
  repository-path: /srv/widgets
  checkout:
    mode: isolated-worktree
    base-ref: origin/main
    refresh: fetch
```

When `checkout` is omitted, `mode: existing` preserves the original behavior
and Pi runs in `repository-path`. `mode: isolated-worktree` requires an explicit
`base-ref`. `refresh: none` resolves the locally available ref without network
mutation. `refresh: fetch` requires an `origin/*` base and, for a live tick only,
fetches that exact branch under the repository lock before tracker access.
After claim, Groundskeeper atomically pins that resolved commit for the task,
creates a deterministic branch and worktree under its host state directory, and
runs Pi there. Deferred/running recovery reuses the same worktree and original
base even if the configured ref has advanced. The configured skill owns workflow
instructions, not Git checkout lifecycle. Groundskeeper never checks, cleans,
stashes, resets, checks out, or edits the donor working tree. Fetch failure or
an unresolved base stops before issue claim; worktree invariant failures stop
before worker launch and leave claimed work recoverable on the next tick.

Task worktrees, their local branches, and pinned base refs are retained after a
terminal transition so draft-PR review and deterministic recovery never lose
local state. Groundskeeper currently performs no automatic garbage collection.
Operators may remove a task worktree with normal `git worktree remove` and then
delete its `groundskeeper/task-*` branch and `refs/groundskeeper/bases/*` ref
only after the task and pull request no longer need recovery. Automated,
lock-protected retention policy remains future work.

### Host scheduler execution source

Use `gk automation ... run-scheduled` behind a host scheduler when automation
configuration and local skills are versioned in Git. The command takes an
explicit donor repository, ref, refresh policy, schedule identity, and daily
attempt limit. The config path remains the normal `automation --config` option,
but must be repository-relative:

```bash
gk automation \
  --config .groundskeeper/config.work.yml \
  run-scheduled discord-dev \
  --schedule-id work-factory \
  --source-repository-path /Users/you/src/dots \
  --source-ref origin/main \
  --source-refresh fetch \
  --daily-attempt-limit 1 \
  --rotation fixed \
  --json
```

Groundskeeper executes the config and local skills from a managed detached
snapshot of the resolved commit. It does not execute repository scripts or read
working-tree configuration from the donor. A dirty donor therefore does not
block a scheduled run or override reviewed configuration. Paths that are
absolute or escape the snapshot are rejected, as are external `--skill-path`
directories and local-skill symlinks that resolve outside the snapshot. The
operating-system schedule, secret injection, and runtime-specific Pi wrapper
remain host policy and are not part of the automation YAML schema.

Pi runners accept optional positive `timeout-seconds` (default: `7200`) for
long-running development work. GitHub CLI operations use a fixed 30-second
timeout. Before dispatch and after any worker return, Groundskeeper reconciles the exact
source issue's closing-PR connection, filtered to the target repository. An
accepted open draft wins if accepted and violating PRs coexist; otherwise an
exact non-draft, closed, or merged target PR blocks without dispatch. Explicit
Codex usage-limit, rate-limit/HTTP 429, and
temporary provider-capacity failures then move running work to deferred for a
deterministic-session resume on the next tick. Authentication, missing or
misconfigured models, policy failures, ordinary worker failures, and Pi timeouts
remain blocked. For a no-PR outcome, the skill may end with one strict
`FACTORY_RESULT_JSON` line containing `status: blocked`, a public-safe summary,
and a next action. Valid bounded content becomes the blocked issue explanation;
malformed, oversized, or absent markers use the generic fallback, and arbitrary
worker stdout remains local. The host lock is released when the tick exits.
Automation entries are strict:
unknown keys are rejected at the entry, source, target, runner, policy, and
labels levels. The removed source-level `repository-path` is rejected as an unknown
key. For example, use target `repository-path`, `timeout-seconds`, not
`timeout_seconds`, and `concurrency`, not `concurency`. A misspelled top-level `automation:` key is
rejected; legacy top-level workflow configuration remains valid. See the README
for a complete configuration.

## .groundskeeper/config.yml

The config file defines workflows — named chains of skills that run in CI or locally.

```yaml
version: 1
runner: claude-code
ci: github-actions

workflows:
  <workflow-name>:
    triggers:
      pull_request: [ready_for_review, synchronize]
    allowed-tools: [Read, Grep]     # optional: workflow-level default
    report-mode: pr                 # optional: "pr" (default) or "issue"
    skills:
      - skill-name                  # simple string format
      - name: another-skill         # dict format with per-step tools
        allowed-tools: [Read, Write, Edit, Grep, Glob, Bash]
```

## Triggers

Triggers determine when a workflow runs. Three types are supported:

```yaml
# PR event trigger — runs on GitHub PR events
triggers:
  pull_request: [ready_for_review, synchronize, reopened]

# Schedule trigger — cron syntax, auto-adds workflow_dispatch for manual runs
triggers:
  schedule: "0 8 * * 1"    # Monday 8am UTC

# Manual trigger only
triggers:
  workflow_dispatch: true

# Mixed — schedule + PR events
triggers:
  pull_request: [synchronize]
  schedule: "0 15 * * 1"
```

Internally, triggers are parsed into typed dataclasses (`EventTrigger`, `ScheduleTrigger`, `ManualTrigger`) defined in `groundskeeper/domain/triggers.py`. Schedule workflows automatically get a `ManualTrigger` injected so they can also be triggered via the GitHub Actions UI.

PR-triggered workflows get draft-PR checks (`if: !github.event.pull_request.draft`) and PR-specific concurrency groups. Scheduled/manual workflows skip both.

### report-mode

Controls how skills report results. Passed to the skill via workflow context.

- `pr` (default): skill creates a branch and opens a PR
- `issue`: skill creates a GitHub Issue

Useful for scheduled health checks in repos where PR-based reporting would conflict with other automation (e.g., obsidian-git auto-sync).

### allowed-tools precedence (highest wins)

1. **Per-step** `allowed-tools` in the skills list dict
2. **Workflow-level** `allowed-tools` on the workflow
3. **Skill frontmatter** `allowed-tools` (used when neither above is set)

Implemented in `groundskeeper/domain/config.py:Workflow.effective_tools()`.

## SKILL.md format

YAML frontmatter + markdown body, parsed by `groundskeeper/domain/parser.py`.

### Required fields

| Field | Type | Notes |
|---|---|---|
| `name` | string | Must match `^[a-z][a-z0-9]*(-[a-z0-9]+)*$` (kebab-case) |
| `description` | string | What the skill does |

### Optional fields

| Field | Type | Notes |
|---|---|---|
| `allowed-tools` | list[str] | Tools the agent can use (e.g., `[Read, Grep, Glob, Bash]`) |
| `argument-hint` | string | Documents `$ARGUMENTS` usage |
| `tags` | list[str] | Categorization tags |
| `triggers` | dict | CI event triggers (e.g., `pull_request: [opened, synchronize]`) |

Extra fields are captured in `skill.metadata` dict.

### Skill directory layout

```
<name>/
├── SKILL.md          # required
├── scripts/          # optional executables
├── references/       # optional context docs
└── assets/           # optional output files
```

### $ARGUMENTS substitution

`$ARGUMENTS` in the body is replaced with the value of `--args` at runtime.
If `$ARGUMENTS` is used but `argument-hint` is not set, no warning is raised (future: will warn).

## Skill resolution order

1. Local: `.groundskeeper/skills/<name>/`
2. External: directories passed via `--skill-path`
3. Builtin: `groundskeeper/builtins/skills/<name>/`

First match wins. Local skills shadow builtins with the same name.
