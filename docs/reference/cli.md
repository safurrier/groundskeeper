---
id: cli-reference
title: CLI Reference
description: >
  Complete reference for all gk CLI commands: init, list, show, run,
  check, generate, and render with options and usage examples.
index:
  - id: gk-init
  - id: gk-list
  - id: gk-show-name
  - id: gk-run-name
  - id: gk-check-name
  - id: gk-generate
  - id: gk-render-name
---

# CLI Reference

## `gk automation`

Automation commands use `.groundskeeper/config.yml` by default. Use
`gk automation --config PATH ...` to select a different file.

```bash
gk automation list [--json]
gk automation show NAME [--json]
gk automation validate [NAME] [--json]
gk automation inspect NAME [--json]
gk automation tick NAME [--dry-run] [--json]
gk automation --config RELATIVE_PATH run-scheduled [NAME ...] \
  --schedule-id ID \
  --source-repository-path PATH \
  --daily-attempt-limit N \
  [--source-ref origin/main] \
  [--source-refresh fetch|none] \
  [--rotation daily|fixed] \
  [--json]
```

`list` and `show` inspect configured local automations. `inspect` reads tracker
items and reports Factory Task parsing, dependency resolution, and deterministic
admission without labels, comments, or worker launch. `show --json` includes
distinct source queue and target repository/path objects, lifecycle labels,
runner settings, policy, and selected-skill provenance (name, source kind, and
path, never the prompt body). `inspect --json` and `tick --json` also expose
source and target separately while dry-run continues to omit issue bodies.
`validate --json` returns the same resolved skill provenance after checking it.
`validate` checks strict config, named-skill resolution, Pi availability, the
fixed draft-only/never-merge policy, and that the target path is a Git worktree
whose `origin` identifies `target.repository`, without contacting GitHub or
starting work. For `isolated-worktree` targets it also resolves the configured
base ref. Dry-run performs the same read-only checkout preflight. A live tick
with `refresh: fetch` fetches the configured `origin/*` branch under the host
lock and freezes its resolved commit SHA before tracker access. After claim it
creates or reuses the deterministic task worktree and starts Pi there. Recovery
retains the original task base even when the configured ref advances. Neither
path checks or modifies donor working-tree files. `tick` runs one bounded reconciliation pass. Dry-run
selects and reports eligible work without labels, comments, or worker launch.
Invalid, tracking, missing-contract, or unresolved-dependency work reports
`would-block` in dry-run and transitions to blocked only in a live tick.
Its compact output omits issue bodies. Pi child output is streamed to stderr so
scheduler logs show progress without corrupting JSON stdout. Review, deferred,
and blocked results expose `session_id`, `session_name`, and `resume_command` in
the JSON `data` object. A blocked result may also contain a strict, bounded
public summary/next-action marker emitted by the automation skill; unmarked Pi
stdout remains local and the generic blocker text is the safe fallback. A
deferred result means Pi reported an explicit transient
provider exhaustion signal and the deterministic session will be reclaimed on a
later tick. The generic resume command uses `pi`; callers with auth
profiles can substitute their profile wrapper, such as `pih`. Pi automation
requires POSIX process-group isolation; validation and live ticks reject
unsupported hosts before tracker access, issue claim, or worker startup rather
than risk descendants surviving after lock release.

`run-scheduled` is the stable installed-CLI entry point for launchd, systemd,
cron, or another host scheduler. Its `--config` path must be relative to the
execution-source repository. Groundskeeper locks the source, optionally fetches
the exact configured `origin/*` branch, resolves one commit, and materializes a
clean detached snapshot under its host state directory. Configuration and local
skills are loaded only from that pinned snapshot. The donor checkout
may be on another branch or contain tracked and untracked changes; scheduled
execution never checks, stashes, resets, checks out, cleans, or executes files
from that working tree. External `--skill-path` directories and snapshot
symlinks that resolve outside the snapshot are rejected.

When no names are supplied, `run-scheduled` uses all automations in declaration
order. `daily` rotation changes the first automation by local calendar day;
`fixed` preserves declaration order. Groundskeeper reserves quota before every
mutating tick and refunds only a proven `no-work` or `not-claimed` result, so a
crash cannot fail open. The schedule ID owns a non-overlap lock and strict daily
ledger under the Groundskeeper state root. Preflight validates the pinned
config, skills, Pi, and target checkouts before opening that ledger. The JSON
result includes the execution ref, pinned commit, managed workspace, quota
state, and each tick result.

The host adapter remains outside Groundskeeper: it chooses when to invoke the
command and supplies secrets or a private Pi launcher. It should execute the
installed `gk`, not a script from the donor repository.

Pi runner configuration accepts optional `timeout-seconds` (default: 7,200) for
long-running tasks. GitHub CLI calls use a fixed 30-second timeout. Groundskeeper
does not sandbox a user-authorized Pi process; it enforces its accepted-result
postcondition by transitioning to review only for an open draft pull request in
the target repository with the exact repository-qualified source issue closing
reference. It queries the exact source issue's documented GraphQL
`closedByPullRequestsReferences` connection, reads each pull request's repository
identity, and filters to the configured target. It paginates that relevant
connection and fails closed if its explicit page budget is exhausted. Before
worker dispatch, an accepted exact open draft reaches review; otherwise an exact
non-draft, closed, or merged target PR blocks. If accepted and violating target
PRs coexist, the accepted open draft wins.

All automation JSON responses use the breaking v2 envelope:
`{"version":2,"status":"...","data":{...},"exit_code":N}`. `list` and `show`
place complete definitions under `data.automations[]` and `data.automation`;
`inspect` returns `data.source`, `data.target`, and `data.tasks[]`; and `tick`
returns `data.source`, `data.target`, its optional repository-qualified
`data.task`, result detail, PR URL, and session handoff fields. Exit `0` means
no work, review-ready work, deferred work, or a successful dry-run. In
particular, `status: "deferred"` is a nonfatal scheduler result. Exit `2` is a
configuration, command, tracker, or lock error; `4` is claim contention; and `5`
is a blocked worker. Commands are noninteractive and ticks use a single-host
advisory lock. Tick selection order is running, deferred, then ready; deferred
work is claimed back to running before a recovery invocation.

## `gk init`

Initialize Groundskeeper in the current project.

```bash
gk init [--non-interactive]
```

Creates `.groundskeeper/` directory structure and default config. Run `gk generate` separately to create CI workflow files.

**Options:**

- `--non-interactive` — Skip interactive prompts, use defaults

## `gk list`

Show available skills with source provenance.

```bash
gk list
```

Displays all skills from local and builtin stores, showing name, source (`local` or `builtin`), and description.

## `gk show <name>`

Display a skill's metadata and body.

```bash
gk show codex-code-review
```

## `gk run <name>`

Execute a skill locally.

```bash
gk run <name> [--args "..."] [--dry-run]
```

**Options:**

- `--args` — Arguments to substitute for `$ARGUMENTS` in the skill body
- `--dry-run` — Show the rendered prompt without executing

## `gk check [name]`

Validate skill frontmatter and structure.

```bash
gk check              # Check all skills
gk check my-skill     # Check a specific skill
```

## `gk generate`

Regenerate CI workflow files from `.groundskeeper/config.yml`.

```bash
gk generate
```

## `gk render <name>`

Output the rendered prompt to stdout. Used internally by CI.

```bash
gk render <name> [--args "..."]
```
