---
id: future-work
title: Future Work
description: >
  Planned features: worktree-isolated parallel execution and
  additional agent runner implementations (Codex, Agent SDK, Jules).
index:
  - id: additional-agent-runners
---

# Future Work

## Worktree-isolated parallel execution (`--parallel --isolate`)

Currently, parallel stages run skills in the same working directory via ThreadPoolExecutor. This is safe for read-only skills but risky for writers. Full isolation would give each skill in a parallel group its own git worktree:

- `git worktree add .claude/worktrees/<skill>-<timestamp>` per skill
- Run Claude in each worktree simultaneously
- After completion, apply each worktree's diff back to the main tree
- Detect and report merge conflicts

Claude Code's agent teams feature (Feb 2026) uses this pattern via `isolation: worktree` on subagents. The complexity is in merging — multiple agents editing the same file produces conflicts that need resolution (auto-merge, retry with rebase, or escalation).

See: https://docs.anthropic.com/en/docs/claude-code/agent-teams

## Additional agent runners

The `AgentRunner` protocol (`groundskeeper/protocols.py`) is runner-agnostic. Currently only `ClaudeCodeRunner` (shells out to `claude -p`) and `DryRunRunner` exist. Future runners:

- **OpenAI Codex** — `codex` CLI with similar subprocess invocation
- **Anthropic Claude Agent SDK** — Python-native async runner using `claude-agent-sdk` package instead of subprocess. Enables streaming, structured outputs, turn limits, budget caps, and permission callbacks.
- **Google Jules / other agents** — same pattern, different CLI

The `runner` field in `config.yml` (`runner: claude-code`) already exists but is currently ignored. Wiring it up to dispatch to different `AgentRunner` implementations is straightforward.
