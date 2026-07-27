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
| `Tracker` | Select, claim, transition, and reconcile durable tasks | `GitHubIssuesTracker` |
| `AutomationRunner` | Execute one normalized tracker task | `PiAutomationRunner` with a configured skill |

`ProcessClient` is the only operating-system process boundary. Pi and GitHub
adapters provide typed semantic operations and bounded timeouts above it.

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

### Skills and workflows

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

### Local tracker automations

```text
automation config
  → strict source queue, target repository/path/checkout, runner, labels, timeout, and policy parsing
  → resolve configured skill with provenance
  → validation/dry-run: prove target identity and resolve the optional base without donor mutation
  → live tick: acquire stable source-admission and target-workspace host locks
  → live tick: prove target identity, optionally fetch the exact origin branch, and freeze its commit SHA
  → reconcile terminal review PRs before dispatch quota
      exact merged target PR → retain closed label + close source as completed
      exact closed-unmerged target PR → retain closed label + close source as not planned
      open or missing evidence → preserve review state
      open closed-labeled issue → resume an interrupted terminal mutation
  → reconcile running issues before deferred work before ready work
  → parse one exact Factory Task + Dependencies contract and resolve dependency state
  → block malformed, tracking, inaccessible, unresolved, or closed-unmerged dependency work before Pi
  → atomically claim at most one admitted trusted deferred or ready task
  → create or reuse the deterministic task branch/worktree from its pinned original base
  → normalize the repository-qualified source issue and explicit target identity
  → render configured skill + typed closing/task-contract/checkout/recovery/policy/session context
  → require POSIX process-group isolation
  → run Pi in the task worktree with a deterministic session keyed by source + issue + target
      stdout + stderr → separately preserved and tee'd to live scheduler logs
      stdout → PR URL parsing + strict public blocker marker extraction
      stderr → transient/durable provider failure classification
  → reconcile GitHub as the durable result authority
      exact source issue's closedByPullRequestsReferences filtered to target repository
      accepted open target draft PR → review (wins if a violating PR also exists)
      otherwise non-draft, closed, or merged target closing PR → blocked policy violation
      explicit closing-PR page exhaustion → indeterminate error without lifecycle mutation
      no accepted PR + explicit transient provider exhaustion → deferred
      no accepted PR + durable/ordinary worker failure → blocked worker error
      valid public blocker marker → bounded summary + next action in issue comment
      arbitrary worker stdout → local logs only
  → expose session id, stable name, and resume command in JSON + issue comment
```

```text
ready ──claim──> running ──accepted draft PR──> review
                   │
                   ├──Codex usage/rate/capacity exhaustion──> deferred
                   └──auth/model/policy/worker/timeout────────> blocked

deferred ──next tick claim + recovery──> running
review ──merged target PR──────────────> closed + issue completed
       └─closed target PR without merge──> closed + issue not planned
```

Deferred claims return the issue to `running` before Pi resumes the same UUIDv5
session. A repeat transient failure returns it to `deferred`; a later accepted
draft PR reaches `review`.

The retained `closed` label is both the terminal ledger state and the recovery
checkpoint for a partial label/issue-close mutation. Scheduled review
reconciliation is quota-free and runs even when no worker attempt remains.
The trusted factory review comment records the exact accepted target PR URL;
terminal queries match that URL rather than selecting another PR associated
with the same source issue or deterministic branch.
Retry uses a new source issue because one source identity deterministically owns
its branch, target pull request, and Pi session. Query errors fail without a
lifecycle mutation.

The source repository owns discovery, admission, dependencies, labels, comments,
and lifecycle. The target checkout module owns donor validation and refresh,
immutable task-base pinning, deterministic branch/worktree recovery, and Pi cwd.
The target repository also scopes rendered `REPOSITORY` and accepted draft PR
discovery. Workflow instructions belong to the configured skill; they do not
own Git checkout mechanics. The Pi adapter knows only how to execute a rendered
prompt. `merge: never` is an accepted-result contract, not a credential sandbox.
Host-state locking coordinates source admission and target workspaces on one
machine and does not provide distributed locking.

### Scheduled local automation

```text
host adapter (launchd, systemd, cron)
  → installed `gk automation ... run-scheduled`
  → acquire the named schedule lock
  → acquire the execution-source repository lock
  → optionally fetch the configured origin branch without touching donor files
  → resolve and pin one commit
  → create, advance, or validate one clean detached pinned worktree
  → resolve the repository-relative config and local skills inside that snapshot
  → validate every selected automation before quota state is opened
  → rotate selected automations and run bounded ticks under one daily ledger
  → emit one versioned JSON schedule result
```

The execution-source adapter owns Git refresh, commit resolution, and the
bounded reusable configuration worktree. The scheduler application module owns
lock ordering, repository-wide source-lease duration, preflight ordering, result
aggregation, and daily reservation policy; its file-backed ledger adapter owns
strict, locked persistence. Schedules using different refs from the same Git
common directory serialize because fetch and worktree administration mutate
shared repository state. The existing automation lifecycle continues to own
tracker reconciliation and target task worktrees. A host adapter owns only
calendar timing, environment/secrets, and runtime-specific executable wiring.
The developer checkout is a donor for Git object access, not an execution
directory or cleanliness authority.

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
