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
`session: deterministic`. Each source requires `repository`, `repository-path`,
and at least one `trusted-author`. Safety policy is strict: concurrency is `1`,
output is `draft-pr`, and merge is `never`. Groundskeeper validates this policy
and enforces the accepted-result postcondition: only an open draft pull request
with the exact closing reference reaches review. It does not sandbox a
user-authorized Pi process. An automation skill receives normalized `TASK_ID`,
`TASK_TITLE`, `TASK_BODY`, `TASK_URL`, `REPOSITORY`, `RECOVERY_CONTEXT`, and
`POLICY_CONCURRENCY`, `POLICY_OUTPUT`, and `POLICY_MERGE`; ordinary skill
rendering is unchanged.

A GitHub issue source has five distinct lifecycle labels. Defaults are shown
below; every override must be a non-empty string and all five must be distinct:

```yaml
source:
  type: github-issues
  repository: example/widgets
  repository-path: /srv/widgets
  trusted-authors: [maintainer]
  labels:
    ready: factory:ready
    running: factory:running
    deferred: factory:deferred
    review: factory:review
    blocked: factory:blocked
```

Pi runners accept optional positive `timeout-seconds` (default: `7200`) for
long-running development work. GitHub CLI operations use a fixed 30-second
timeout. After any worker return, Groundskeeper first reconciles the accepted
open-draft closing PR. Explicit Codex usage-limit, rate-limit/HTTP 429, and
temporary provider-capacity failures then move running work to deferred for a
deterministic-session resume on the next tick. Authentication, missing or
misconfigured models, policy failures, ordinary worker failures, and Pi timeouts
remain blocked. For a no-PR outcome, the skill may end with one strict
`FACTORY_RESULT_JSON` line containing `status: blocked`, a public-safe summary,
and a next action. Valid bounded content becomes the blocked issue explanation;
malformed, oversized, or absent markers use the generic fallback, and arbitrary
worker stdout remains local. The host lock is released when the tick exits.
Automation entries are strict:
unknown keys are rejected at the entry, source, runner, policy, and labels
levels. For example, use `timeout-seconds`, not `timeout_seconds`, and
`concurrency`, not `concurency`. A misspelled top-level `automation:` key is
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
