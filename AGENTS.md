# Groundskeeper

CLI tool that scripts AI agents to run on PRs and schedules. Users define "skills" (YAML-frontmatter + markdown prompt in `SKILL.md` files), and Groundskeeper generates CI workflows (GitHub Actions) to execute them via Claude Code on PR events, cron schedules, or manual triggers.

## Repo Map

```
groundskeeper/           # Python package (the library + CLI)
  cli/main.py            # Click CLI entry point (`gk` command)
  domain/                # Core models, parser, triggers, errors (no external deps)
  adapters/              # Ports: skill stores, runners, CI providers
  builtins/              # Shipped skills + templates (config, GHA Jinja2)
  protocols.py           # Protocol interfaces (SkillStore, AgentRunner, CIProvider)
tests/                   # Mirrors source layout; e2e/ has installed-CLI tests
.groundskeeper/          # User config: config.yml + skills/
docs/AGENTS.md           # Agent routing index for docs/
docs/                    # MkDocs Material site
docker/                  # Dev container setup
mise.toml                # Task runner config (all commands below)
pyproject.toml           # Package metadata, deps, tool config (ruff, ty, pytest)
```

## Architecture (one paragraph)

Hexagonal/ports-and-adapters. Domain layer (`domain/`) defines `Skill`, `Workflow`, `RunContext`, `RunResult` models, a typed trigger system (`domain/triggers.py`), a frontmatter parser, and a config loader. `protocols.py` defines the port interfaces: `SkillStore` (loads skills), `AgentRunner` (executes via Claude Code or dry-run), `CIProvider` (generates CI YAML). Adapters implement these: `LocalSkillStore` reads from `.groundskeeper/skills/` or external paths, `BuiltinSkillStore` reads shipped skills, `ClaudeCodeRunner` shells out to `claude -p` with `--allowedTools` and JSON output parsing, `GitHubActionsProvider` renders Jinja2 templates (including chained and scheduled workflows). The CLI (`cli/main.py`) wires adapters together. Skill resolution: local → external → builtin (first-match).

## Common Commands (mise task runner)

| Command | What it does |
|---|---|
| `mise run setup` | Install deps via uv |
| `mise run check` | Run all checks (lint + format + ty + test) |
| `mise run test` | Unit tests with coverage (`-m 'not e2e'` by default) |
| `mise run lint` | Ruff linter with auto-fix |
| `mise run format` | Ruff formatter |
| `mise run ty` | Type check with ty (strict, error-on-warning) |
| `mise run test:e2e` | E2E tests (requires `mise run install` first) |
| `mise run test:all` | All tests including e2e |
| `mise run install` | Install `gk` CLI via `uv tool install` |
| `mise run docs:serve` | Local docs server (port 8000) |
| `mise run docs:build` | Build docs site (strict mode) |

Single test: `uv run -m pytest tests/path_to_test.py::test_function_name`

## Code Style

Enforced by tools — run `mise run check` before pushing:
- **Formatter/Linter**: ruff (config in `pyproject.toml`)
- **Type checker**: ty with `error-on-warning = true` (see `[tool.ty]` in `pyproject.toml`)
- **Python**: 3.10+, strict typing, snake_case / PascalCase conventions
- **Imports**: stdlib → third-party → `groundskeeper` (enforced by ruff isort)

## CI

GitHub Actions on PR (`.github/workflows/tests.yml`): lint → format check → pytest → ty → codecov.

## Skill Anatomy

A skill lives in a directory with a `SKILL.md` file:
```
.groundskeeper/skills/<name>/SKILL.md   # local (user-defined)
groundskeeper/builtins/skills/<name>/SKILL.md  # shipped
<any-dir>/<name>/SKILL.md               # external (via --skill-path)
```
Format: YAML frontmatter (`name`, `description`, `triggers`, `allowed-tools`, `tags`, `argument-hint`) + markdown body (the prompt). `$ARGUMENTS` in the body is substituted at runtime. See `groundskeeper/domain/parser.py` for parsing rules.

## gk CLI Commands

| Command | Purpose |
|---|---|
| `gk init` | Bootstrap `.groundskeeper/` config and skills dir |
| `gk list` | Show available skills (local + external + builtin) |
| `gk run <skill> [--dry-run] [--yolo] [--args ...]` | Execute a single skill |
| `gk run-workflow <name> [--dry-run] [--yolo] [--parallel] [--args ...]` | Execute a workflow chain from config |
| `gk check [skill]` | Validate skill frontmatter |
| `gk show <skill>` | Display skill metadata + body |
| `gk render <skill>` | Output rendered prompt (used by CI) |
| `gk generate` | Regenerate CI workflow files |
| `gk --skill-path <dir> ...` | Add external skill directories (top-level option) |

## Key Invariants

- Skill names must be kebab-case (validated by parser regex)
- Skill resolution order: local → external (--skill-path) → builtin (first-match wins)
- `ClaudeCodeRunner` passes `--allowedTools` from skill frontmatter (or workflow override) and parses JSON output
- Workflows in `.groundskeeper/config.yml` support per-step and workflow-level `allowed-tools` overrides (see `docs/config-and-skills.md`)
- Multi-skill CI workflows generate a single GitHub Actions file with chained jobs (stages run in parallel within, sequential across)
- Parallel groups in workflows auto-parallelize locally only when all skills are read-only; use `--parallel` to force
- `--yolo` flag skips all permission checks (`--dangerously-skip-permissions`)
- `pytest` excludes `e2e` marker by default (`addopts = "-m 'not e2e'"`)
- ty is strict: warnings are errors
- Triggers are typed: `EventTrigger`, `ScheduleTrigger`, `ManualTrigger` (see `domain/triggers.py`). Schedule triggers auto-inject `workflow_dispatch` for manual runs.
- Templates conditionally emit draft-PR checks and PR-specific concurrency groups (only for workflows with `pull_request` triggers)

## Task-Specific Docs

### Cross-cutting docs in `docs/`

| Doc | Topic |
|-----|-------|
| `docs/AGENTS.md` | Agent routing index for all docs |
| `docs/architecture.md` | Ports-and-adapters flow, domain model relationships, execution pipeline |
| `docs/config-and-skills.md` | config.yml format, skill frontmatter spec, allowed-tools precedence |
| `docs/future-work.md` | Planned features: worktree isolation, additional agent runners |

### Reference

| Doc | Topic |
|-----|-------|
| `docs/reference/skills.md` | User-facing skill authoring guide |
| `docs/reference/cli.md` | CLI reference |
| `docs/reference/api.md` | Auto-generated API docs |

## Key References

- `groundskeeper/domain/parser.py` -- authoritative parsing logic for SKILL.md
- `groundskeeper/domain/config.py` -- config loading, workflow/step model, trigger parsing, tool precedence logic
- `groundskeeper/domain/triggers.py` -- typed trigger system (EventTrigger, ScheduleTrigger, ManualTrigger)
