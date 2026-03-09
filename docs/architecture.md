---
id: architecture
title: Architecture Deep Reference
description: >
  Ports-and-adapters design, domain models, execution flow,
  and CI generation pipeline for the Groundskeeper system.
index:
  - id: ports-and-adapters
  - id: domain-models-groundskeeperdomainmodelspy
  - id: workflow-models-groundskeeperdomainconfigpy
  - id: execution-flow
  - id: ci-generation-flow
---

# Architecture Deep Reference

## Ports and Adapters

The domain layer has zero external dependencies. All I/O goes through protocol interfaces in `groundskeeper/protocols.py`:

| Protocol | Purpose | Implementations |
|---|---|---|
| `SkillStore` | Load/list skills | `LocalSkillStore` (`.groundskeeper/skills/` + external paths), `BuiltinSkillStore` (shipped) |
| `AgentRunner` | Execute a skill | `ClaudeCodeRunner` (shells out to `claude` CLI), `DryRunRunner` (prints prompt) |
| `CIProvider` | Generate CI YAML | `GitHubActionsProvider` (Jinja2 templates) |

## Domain Models (`groundskeeper/domain/models.py`)

- **`Skill`** — parsed SKILL.md: name, description, body, source, allowed_tools, triggers, metadata
- **`SkillSource`** — where it came from: `kind` (local/builtin/external) + `path`
- **`RunContext`** — execution context: skill + arguments + working_directory + skip_permissions + allowed_tools_override
- **`RunResult`** — output: success, output text, error text, exit_code, metadata

## Trigger Types (`groundskeeper/domain/triggers.py`)

Tagged union for workflow trigger configuration:

- **`EventTrigger`** — GitHub webhook event (e.g., `pull_request`) with activity type filters. Uses `GitHubEvent` enum for event names, `tuple[str, ...]` for activity types.
- **`ScheduleTrigger`** — Cron-based schedule (e.g., `"0 8 * * 1"` for Monday 8am UTC)
- **`ManualTrigger`** — `workflow_dispatch` for GitHub Actions UI runs

`TriggerSpec = EventTrigger | ScheduleTrigger | ManualTrigger`

Schedule triggers auto-inject `ManualTrigger` during parsing so scheduled workflows can always be triggered manually.

## Workflow Models (`groundskeeper/domain/config.py`)

- **`SkillRef`** — reference to a skill with optional per-step `allowed_tools`
- **`ParallelGroup`** — group of `SkillRef`s that can run concurrently
- **`Step`** = `SkillRef | ParallelGroup` — a workflow step
- **`Workflow`** — named sequence of steps with typed `triggers: tuple[TriggerSpec, ...]`, optional workflow-level `allowed_tools`, and `report_mode` (pr/issue)

Key methods:
- `Workflow.effective_tools(ref)` resolves tool precedence (per-step > workflow > skill frontmatter)
- `Workflow.has_pr_trigger` / `Workflow.has_schedule` — trigger type queries used by CI generation

## Execution Flow

```
CLI (main.py)
  → _get_stores() builds [LocalSkillStore, ..., BuiltinSkillStore]
  → resolve_skill() iterates stores (first match wins)
  → RunContext constructed (with effective_tools from workflow if applicable)
  → runner.run(context) → ClaudeCodeRunner shells out:
      claude -p <rendered_prompt> --output-format json [--allowedTools ...] [--dangerously-skip-permissions]
  → JSON response parsed into RunResult
```

For workflows: steps execute sequentially. `ParallelGroup` steps use `ThreadPoolExecutor` when all skills are read-only (no Write/Edit/Bash/NotebookEdit tools) or when `--parallel` is forced.

## CI Generation Flow

```
config.yml → load_config() → get_workflows()
  → _parse_triggers() converts raw YAML into typed TriggerSpec tuple
  → _triggers_to_actions_yaml() converts back to GHA on: syntax
  → Templates receive is_pr_trigger flag for conditional rendering:
      PR triggers: draft check + PR-number concurrency group
      Schedule/manual: simple concurrency group, no draft check
  → Single-skill workflow: generate_caller() from caller.yml.j2
  → Multi-skill workflow: generate_chain_workflow() from chain.yml.j2
      Skills within a stage run in parallel (separate GHA jobs)
      Stages are sequential (each waits for the previous)
```

Templates live in `groundskeeper/builtins/templates/github_actions/`.
