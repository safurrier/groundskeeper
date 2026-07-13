# Groundskeeper

Groundskeeper has two execution paths. Local automations dispatch trusted
tracker tasks through a configured Groundskeeper skill. GitHub Actions generation
runs skills on repository events.

## Local task automations

The first automation source is GitHub Issues and the first runner is Pi. A tick
claims at most one trusted, explicitly-ready issue, renders its configured skill
with a normalized task context, and runs it in Pi. Groundskeeper validates its
fixed policy and enforces the accepted-result postcondition: only an open draft
pull request with an exact GitHub closing reference reaches review. It does not
sandbox Pi or prevent a user-authorized process from merging a pull request.

```yaml
automations:
  daily-maintenance:
    source:
      type: github-issues
      repository: example/widgets
      repository-path: /Users/you/src/widgets
      trusted-authors: [maintainer]
      labels:
        ready: factory:ready
        running: factory:running
        review: factory:review
        blocked: factory:blocked
    runner:
      type: pi
      skill: issue-implementation
      approval: allow
      session: deterministic
      timeout-seconds: 7200  # optional; default is two hours
    policy:
      concurrency: 1
      output: draft-pr
      merge: never
```

Create `issue-implementation` as an ordinary skill under
`.groundskeeper/skills/`. It receives its usual prompt plus `TASK_ID`,
`TASK_TITLE`, `TASK_BODY`, `TASK_URL`, `REPOSITORY`, `RECOVERY_CONTEXT`, and
the fixed `POLICY_CONCURRENCY`, `POLICY_OUTPUT`, and `POLICY_MERGE` fields.
Normal `gk run` and `gk render` behavior for that skill is unchanged.

```bash
gk automation list
gk automation validate daily-maintenance --json
gk automation tick daily-maintenance --dry-run --json
gk automation tick daily-maintenance --json
```

`validate` checks configuration, skill resolution, the Pi executable, repository
path, and fixed policy without contacting GitHub or claiming work. `tick` is
noninteractive and uses a host-local advisory lock keyed by normalized GitHub
repository identity. Locks live under `$XDG_STATE_HOME/groundskeeper/locks`
(or `~/.local/state/groundskeeper/locks`), so separate config worktrees for the
same repository share one host lock. `GROUNDSKEEPER_STATE_HOME` is a narrow
host/test override. Run exactly one scheduler host for each automation;
multi-host scheduling is not supported. A successful no-work tick is safe. After
any worker return, Groundskeeper first reconciles the accepted GitHub result: an
open draft PR with the exact closing reference moves to review even if the worker
reported a late failure. Otherwise, a failed worker moves the issue to
`factory:blocked` with an actionable comment. The JSON contract is versioned,
and dry-run output deliberately omits
issue bodies. Set a scheduler's working directory to the repository containing
`.groundskeeper/config.yml`, or pass `gk automation --config PATH ...`.

Each issue maps to a deterministic UUIDv5 Pi session and stable run name. Pi
output streams to Groundskeeper's stderr for live scheduler logs while the final
versioned JSON result remains on stdout. Review and blocked results include the
session ID, name, and generic `pi --session ID` resume command; GitHub transition
comments preserve the same handoff metadata. Streaming Pi automations require a
POSIX host so Groundskeeper can terminate the complete process group before
releasing repository locks; unsupported hosts fail before starting Pi.

Pi runs have a configurable positive timeout (`timeout-seconds`, default 7,200
seconds); GitHub CLI operations have fixed 30-second timeouts. After a timeout,
Groundskeeper reconciles the same accepted GitHub result first; without one, it
blocks the claimed issue with the command error and releases the host lock for retry.
If a process exits after claiming an issue, the next tick resumes that session
and reconciles GitHub state. Issue discovery requests ready/running labels
server-side and is bounded at 1,000 open issues per state.

Define AI agent skills as markdown prompt templates. Chain them into workflows. Run them locally or generate GitHub Actions workflows that run them on PRs or schedules.

## Why this exists

You want an AI agent to review a PR, update docs, check for anti-patterns, or run a custom analysis. The agent needs a prompt, tool permissions, and a trigger. Without Groundskeeper, you wire that together by hand with GitHub Actions, `claude-code-action`, and YAML boilerplate.

Groundskeeper keeps the authoring model small: a skill is a directory containing `SKILL.md` with YAML frontmatter and a markdown body. No SDK, no plugin API, no build step. If you can write a prompt template, you can write a skill.

## Install or run it

### From a source checkout

Use this path when you are developing Groundskeeper or when package-registry availability is uncertain.

```bash
git clone https://github.com/safurrier/groundskeeper.git
cd groundskeeper
uv run gk --help      # run the CLI from this checkout
```

To install the checkout as a global `gk` command, run `uv tool install .` from the repo root. For development tasks, use `mise run setup` and `mise run check` after trusting the repo's `mise.toml` if your mise configuration requires it.

### From a package registry

```bash
uv tool install groundskeeper
gk --help
```

Registry installation was not verified for this README update. Use the source-checkout path above if `uv` cannot find a published `groundskeeper` package in your configured Python package index.

## Quick start: local skill first, CI second

### 1. Initialize project config

```bash
gk init
```

`gk init` creates local Groundskeeper state only:

- `.groundskeeper/config.yml`
- `.groundskeeper/skills/`

It does **not** create GitHub Actions files. Run `gk generate` later after you validate skills and opt into a CI provider.

### 2. See available skills

```bash
gk list
```

You should see builtin skills such as `codex-code-review` and `context-files`, plus any local or external skill paths.

### 3. Preview a skill prompt locally

```bash
gk run codex-code-review --dry-run
```

`--dry-run` renders the prompt without calling Claude Code. To execute for real, install the Claude Code CLI and run without `--dry-run`; `--yolo` passes `--dangerously-skip-permissions` to Claude Code.

### 4. Write or edit a workflow

Workflows live in `.groundskeeper/config.yml` and chain skills in order:

```yaml
version: 1
runner: claude-code
ci: github-actions

workflows:
  pr-check:
    triggers:
      pull_request: [ready_for_review, synchronize]
    skills:
      - name: context-files
        allowed-tools: [Read, Write, Edit, Grep, Glob, Bash]
      - name: codex-code-review
        allowed-tools: [Read, Grep, Glob]
```

Tool permissions cascade: per-step `allowed-tools` > workflow-level `allowed-tools` > skill frontmatter.

### 5. Validate before generating CI

```bash
gk check
```

This re-parses all visible `SKILL.md` files and catches invalid frontmatter or skill structure before you create workflow YAML.

### 6. Generate GitHub Actions workflows

```bash
gk generate
```

`gk generate` reads `.groundskeeper/config.yml` and writes `.github/workflows/gk_*.yml`. It requires `ci: github-actions` in the config. Generated workflows use `ANTHROPIC_API_KEY` from repository secrets and call `anthropics/claude-code-action@v1`.

## Skills are prompt templates

A skill is a directory with a `SKILL.md` file:

```text
.groundskeeper/skills/my-skill/
└── SKILL.md
```

```markdown
---
name: my-skill
description: Does a useful thing
allowed-tools: [Read, Grep, Glob]
triggers:
  pull_request: [synchronize]
---

You are an agent. Do the useful thing.

Use $ARGUMENTS to adjust behavior.
```

Put the directory under `.groundskeeper/skills/` and `gk list` picks it up. Skill names must be kebab-case.

### External skill libraries

Load skills from another directory with `--skill-path`:

```bash
gk --skill-path ~/my-skills list
gk --skill-path ~/my-skills run my-skill --dry-run
```

Resolution order is local, then external paths, then builtin skills. First match wins, so a local skill can shadow a shared or builtin skill.

## Workflows and parallel groups

Workflows run steps sequentially. A nested list marks a parallel stage:

```yaml
workflows:
  full-check:
    triggers:
      pull_request: [ready_for_review]
    allowed-tools: [Read, Grep, Glob]
    skills:
      - [lint-check, type-check]
      - name: docs-updater
        allowed-tools: [Read, Write, Edit, Grep, Glob, Bash]
```

In CI, skills in the same stage run in parallel. Locally, parallel groups auto-parallelize only when all skills are read-only; pass `--parallel` to force concurrency.

## Command reference

| Command | What it does |
|---|---|
| `gk init [--non-interactive]` | Create `.groundskeeper/config.yml` and `.groundskeeper/skills/`; does not generate CI files. |
| `gk list` | Show local, external, and builtin skills with source labels. |
| `gk show <skill>` | Display skill metadata and prompt body. |
| `gk check [skill]` | Validate one skill or all visible skills. |
| `gk render <skill> [--args "..."]` | Print the rendered prompt; generated CI uses this. |
| `gk run <skill> [--dry-run] [--yolo] [--args "..."]` | Run or preview one skill locally. |
| `gk run-workflow <name> [--dry-run] [--yolo] [--parallel] [--args "..."]` | Run a configured workflow locally. |
| `gk generate` | Write `.github/workflows/gk_*.yml` from config; requires `ci: github-actions`. |

Global flag: `--skill-path <dir>` adds an external skill directory.

## How generated CI works

Generated workflows:

1. Check out the repository.
2. Install `uv`.
3. Install `groundskeeper` in the runner (`uv tool install groundskeeper`, so generated CI assumes the package is available from the configured registry).
4. Render the selected skill prompt with `gk render`.
5. Pass the prompt to `anthropics/claude-code-action@v1`.

Multi-skill workflows become one GitHub Actions file with staged jobs: skills in the same stage run in parallel, and later stages wait for earlier stages.

## Development

```bash
mise run setup       # Install dependencies with uv
mise run check       # Lint + format check + type check + tests
mise run test        # Unit tests only (excludes e2e by default)
mise run test:e2e    # E2E tests; requires mise run install first
```

## More docs

- [Getting started](docs/getting-started.md)
- [Config and skills deep reference](docs/config-and-skills.md)
- [CLI reference](docs/reference/cli.md)
- [Architecture](docs/architecture.md)

## License

MIT
