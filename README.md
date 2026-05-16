# Groundskeeper

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
