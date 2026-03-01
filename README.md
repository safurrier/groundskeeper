# Groundskeeper

Define AI agent skills as markdown files. Chain them into workflows. Run them locally or in CI on every PR.

## Why this exists

You want an AI agent to review your PRs, update your docs, check for anti-patterns, or run a custom analysis. The agent needs a prompt, some tool permissions, and a trigger. Today you'd wire this up manually with GitHub Actions, `claude-code-action`, and a bunch of YAML boilerplate.

Groundskeeper wraps all of that. You write a skill (a markdown file with YAML frontmatter), declare when it should run, and `gk generate` produces the CI workflows. You can also run skills locally with `gk run` or chain multiple skills together with `gk run-workflow`.

Skills are just prompt templates in directories. No SDK, no plugin API, no build step. If you can write a prompt, you can write a skill.

## Installation

```bash
uv tool install groundskeeper
```

This installs `gk` globally. Run `gk --help` to verify.

### From source

```bash
git clone https://github.com/safurrier/groundskeeper.git
cd groundskeeper
mise run setup    # Install dependencies
mise run check    # Run lint, type check, tests
```

## Quick Start

**1. Initialize**

```bash
gk init
```

Creates `.groundskeeper/config.yml` and a `skills/` directory. Ships with two builtin skills (`codex-code-review` and `context-files`).

**2. List available skills**

```bash
gk list
```

Shows builtins, local skills (`.groundskeeper/skills/`), and any external skill paths.

**3. Run a skill locally**

```bash
gk run codex-code-review --dry-run          # Preview the prompt
gk run codex-code-review --yolo             # Run with full tool access
gk run codex-code-review --args "strict"    # Pass arguments
```

**4. Run a workflow (chain of skills)**

Define a workflow in `.groundskeeper/config.yml`:

```yaml
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

Run it:

```bash
gk run-workflow pr-check --dry-run     # Preview
gk run-workflow pr-check --yolo        # Run for real
```

**5. Generate CI workflows**

```bash
gk generate
```

Produces `.github/workflows/gk_*.yml` files that run your skills on PR events via `claude-code-action`.

## Skills

A skill is a directory with a `SKILL.md` file:

```
my-skill/
  SKILL.md
```

The file has YAML frontmatter and a markdown body (the prompt):

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

That's it. Put the directory in `.groundskeeper/skills/` and `gk list` picks it up.

## External skills

Load skills from anywhere with `--skill-path`:

```bash
gk --skill-path ~/my-skills list
gk --skill-path ~/my-skills run my-skill --dry-run
```

Useful for sharing skills across repos or using a personal skill library.

## Workflows

Workflows chain skills sequentially. Define them in `.groundskeeper/config.yml`:

```yaml
workflows:
  full-check:
    triggers:
      pull_request: [ready_for_review]
    allowed-tools: [Read, Grep, Glob]    # default for all steps
    skills:
      - name: docs-updater
        allowed-tools: [Read, Write, Edit, Grep, Glob, Bash]  # override
      - code-reviewer                     # inherits workflow-level tools
```

Tool permissions cascade: per-step > workflow-level > skill frontmatter.

### Parallel groups

Skills in a nested list run concurrently (in CI always, locally when safe):

```yaml
skills:
  - [lint-check, type-check]    # these two run in parallel
  - test-runner                  # this waits for both to finish
```

Locally, parallel groups auto-run concurrently only when all skills are read-only (no Write/Edit/Bash). Use `--parallel` to force it.

## Commands

| Command | What it does |
|---------|-------------|
| `gk init` | Create `.groundskeeper/` config and CI workflows |
| `gk list` | Show available skills |
| `gk run <skill>` | Run a single skill locally |
| `gk run-workflow <name>` | Run a workflow chain |
| `gk check [skill]` | Validate skill definitions |
| `gk show <skill>` | Display skill metadata and prompt |
| `gk render <skill>` | Output rendered prompt (used by CI) |
| `gk generate` | Regenerate CI workflow files |

Common flags: `--dry-run` (preview), `--yolo` (skip permission checks), `--args "..."` (pass arguments), `--skill-path <dir>` (external skills).

## How it works in CI

`gk generate` produces GitHub Actions workflows that:

1. Check out the repo
2. Install groundskeeper
3. Render the skill prompt (`gk render <skill>`)
4. Pass it to `anthropics/claude-code-action@v1`

Multi-skill workflows generate a single workflow file with chained jobs. You just need `ANTHROPIC_API_KEY` in your repo secrets.

## Development

```bash
mise run setup       # Install dependencies
mise run check       # Lint + format + typecheck + tests
mise run test        # Unit tests only
mise run test:e2e    # E2E tests (requires: mise run install)
```

## License

MIT
