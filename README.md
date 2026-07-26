# Groundskeeper

Groundskeeper has two execution paths. Local automations dispatch trusted
tracker tasks through a configured Groundskeeper skill. GitHub Actions generation
runs skills on repository events.

## Local task automations

The first automation source is GitHub Issues and the first runner is Pi. A tick
claims at most one trusted, explicitly-ready issue, renders its configured skill
with a normalized task context, and runs it in Pi. Groundskeeper validates its
fixed policy and enforces the accepted-result postcondition: only a verified
open draft pull request reaches review. It does not
sandbox Pi or prevent a user-authorized process from merging a pull request.

```yaml
automations:
  daily-maintenance:
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
      repository-path: /Users/you/src/widgets
      checkout:
        mode: isolated-worktree
        base-ref: origin/main
        refresh: fetch
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
      link-source-issue: true
      include-factory-session: true
      include-pi-resume: true
```

Create `issue-implementation` as an ordinary skill under
`.groundskeeper/skills/`. It receives its usual prompt plus `TASK_ID`,
`TASK_TITLE`, `TASK_BODY`, `TASK_URL`, `REPOSITORY`,
`RECOVERY_CONTEXT`, typed `TARGET_CHECKOUT_MODE`,
`TARGET_BASE_REF`, `TARGET_BASE_SHA`, `TARGET_REFRESH`,
`TARGET_WORKSPACE_PATH`, and the fixed
`POLICY_CONCURRENCY`, `POLICY_OUTPUT`, `POLICY_MERGE`,
`POLICY_LINK_SOURCE_ISSUE`, `POLICY_INCLUDE_FACTORY_SESSION`, and
`POLICY_INCLUDE_PI_RESUME` fields. `REPOSITORY` is the target repository.
When `link-source-issue` is true, the prompt also includes
`FACTORY_CLOSING_REFERENCE`: `#123` for a same-repository queue or
`example/work-factory#123` for a cross-repository queue. All three public
handoff controls default to true. Disabling them tells the worker to omit that
source or session metadata from the target pull request; Groundskeeper retains
the recovery fields in its private execution context. Disabling source linking
requires an `isolated-worktree` or `managed-worktree` target so Groundskeeper
can verify the deterministic task branch.
Normal `gk run` and `gk render` behavior for that skill is unchanged.

```bash
gk automation list
gk automation validate daily-maintenance --json
gk automation tick daily-maintenance --dry-run --json
gk automation tick daily-maintenance --json
```

`validate` checks configuration, skill resolution, the Pi executable, fixed
policy, and that the target path is a Git worktree whose GitHub `origin` matches
the configured target, without contacting GitHub or claiming work. For an
`isolated-worktree` or `managed-worktree` target it also resolves the configured
base ref. A managed target must be a linked disposable worktree; it is reused
directly instead of materializing another task checkout. It starts clean, but a
dirty Groundskeeper task branch remains resumable. A live tick with
`refresh: fetch` refreshes the exact `origin/*` branch under the host lock, then
freezes its commit SHA before tracker access.
Validation and dry-run never fetch. The target path is a donor checkout; its
dirty working tree is neither inspected nor modified by Groundskeeper in
`isolated-worktree` mode. After claim, Groundskeeper creates a deterministic
task branch from the frozen SHA, runs Pi there, and reuses that same workspace
and original base when recovering the deterministic session. Source issue
discovery, admission, labels, dependencies, and comments stay in the source
repository. Pull-request reconciliation uses either the exact source issue's
closing-PR connection or the deterministic task branch, according to policy,
and filters results to the target repository. `tick` is noninteractive and uses a host-local
source-queue lock plus a target donor/worktree lock. Locks and deterministic
task worktrees live under `$XDG_STATE_HOME/groundskeeper`
(or `~/.local/state/groundskeeper`), so separate config worktrees share the
same host coordination and recovery state. `GROUNDSKEEPER_STATE_HOME` is a
narrow host/test override. Run exactly one scheduler host for each automation;
multi-host scheduling is not supported. A tick reconciles running work first,
then atomically reclaims deferred work, then claims new ready work. A successful
no-work tick is safe. Before dispatch and after any worker return, Groundskeeper
reconciles the accepted GitHub result: a policy-verified open draft PR in the
target repository moves to review even if the worker reported a late failure. If accepted and violating
exact target PRs coexist, the accepted draft wins; otherwise a non-draft, closed,
or merged exact target PR blocks without dispatch. Explicit Codex usage,
provider rate-limit/HTTP 429, and temporary provider-capacity failures move the
issue to `factory:deferred` with an actionable comment and are resumed on the
next tick. Authentication, model configuration, policy, timeout, and ordinary
worker failures move it to `factory:blocked`. The breaking source/target JSON
shape uses envelope version `2`, and dry-run output deliberately omits
issue bodies.

For unattended execution, point launchd, systemd, or cron at the installed
Groundskeeper command rather than a script inside the configuration checkout:

```bash
gk automation \
  --config .groundskeeper/config.yml \
  run-scheduled \
  --schedule-id personal-factory \
  --source-repository-path /Users/you/src/factory-config \
  --source-ref origin/main \
  --source-refresh fetch \
  --daily-attempt-limit 5 \
  --rotation daily \
  --json
```

`run-scheduled` fetches and pins the configured ref, then loads configuration
and local skills from a clean detached worktree under Groundskeeper's host
state. The donor checkout may be dirty or on another branch and is never
cleaned, reset, stashed, or used as the execution directory. Groundskeeper owns
the bounded source worktree, crash-safe daily reservations, ordering, and tick
lifecycle; the host adapter owns only calendar timing, secret injection, and
runtime-specific launchers.

The lifecycle is deliberately small and label-backed:

```text
factory:ready ──claim──> factory:running ──accepted draft PR──> factory:review
                              │
                              ├──transient provider exhaustion──> factory:deferred
                              └──durable failure/policy violation──> factory:blocked

factory:deferred ──next tick claim/resume──> factory:running
```

Each source repository, issue number, and target repository tuple maps to a
deterministic UUIDv5 Pi session and stable run name. Pi
output streams to Groundskeeper's stderr for live scheduler logs while the final
versioned JSON result remains on stdout. Review, deferred, and blocked results
include the session ID, name, and generic `pi --session ID` resume command;
GitHub transition comments preserve the same handoff metadata. When no PR is
created, an automation skill may expose a bounded public-safe explanation with
one final `FACTORY_RESULT_JSON={"status":"blocked","summary":"...","next_action":"..."}`
line. Groundskeeper validates that exact shape, caps it at 8,000 characters,
neutralizes GitHub mentions, and publishes only the summary and next action.
Arbitrary Pi stdout is never copied into an issue comment. Streaming Pi
automations require a
POSIX host so Groundskeeper can terminate the complete process group before
releasing repository locks; unsupported hosts fail before starting Pi.

Pi runs have a configurable positive timeout (`timeout-seconds`, default 7,200
seconds); GitHub CLI operations have fixed 30-second timeouts. After a timeout,
Groundskeeper reconciles the same accepted GitHub result first; without one, it
blocks the claimed issue with the command error and releases the host lock for retry.
If a process exits after claiming an issue, the next tick resumes that session
and reconciles GitHub state. Issue discovery requests ready, running, and
deferred labels server-side and is bounded at 1,000 open issues per state.
Pull request reconciliation inspects either the exact source issue's
repository-qualified closing pull request references or the deterministic task
branch and filters them to the configured target repository.

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
