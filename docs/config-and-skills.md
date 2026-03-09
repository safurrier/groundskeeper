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

### Triggers

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
