# Groundskeeper

Groundskeeper is a Python CLI for reusable agent skills, local workflows, generated GitHub Actions, and host-local tracker automations. Start at `groundskeeper/cli/main.py`; strict models live in `groundskeeper/domain/`, side effects in `groundskeeper/adapters/`, and application lifecycle in `groundskeeper/automation.py`.

## How to Work Here

1. Read `docs/architecture.md` for ownership and `docs/config-and-skills.md` for schemas.
2. Add behavior at the narrowest domain, application, or adapter seam.
3. Run focused pytest while editing, then `mise run check` once near handoff.
4. Install before testing the packaged command: `mise run install && mise run test:e2e`.

## Commands

| Command | Purpose |
|---|---|
| `mise run check` | Ruff, formatting, strict `ty`, and non-E2E pytest |
| `mise run test:e2e` | Installed-CLI process and lifecycle tests |
| `mise run docs:build` | Strict MkDocs build |
| `uv run -m pytest tests/path.py::test_name` | Focused test |

## Architecture

- `SkillStore`, `AgentRunner`, and `CIProvider` support the original skill/workflow path.
- `Tracker` and `AutomationRunner` support the local factory path.
- `AutomationService` owns ready/running/review/blocked reconciliation; adapters own GitHub, Pi, locking, and processes.
- `ProcessClient` is the only operating-system subprocess boundary.
- Skill resolution is local → external `--skill-path` → builtin.

## Gotchas

- **DO** keep automation workflow prose in the configured `SKILL.md`. **NOT** in `PiClient` or `AutomationService`. **BECAUSE** Groundskeeper owns execution and lifecycle; the consuming repo owns its workflow.
- **DO** validate strict automation config before claiming work. **NOT** silently coerce unknown keys, paths, labels, or runner settings. **BECAUSE** reviewed configuration must match scheduled behavior.
- **DO** treat GitHub as the durable result authority after a worker returns. **NOT** trust worker stdout or exit status alone. **BECAUSE** a valid draft PR may exist after a late timeout, while unrelated or non-draft PRs must block.
- **DO** keep task admission single-host and repository-scoped. **NOT** claim distributed locking. **BECAUSE** the stable host-state lock coordinates config worktrees but not multiple machines.
- **DO** terminate the complete process group on timeout. **NOT** only the direct Pi process. **BECAUSE** descendants can retain pipes and continue mutating after lock release.
- **DO** keep `merge: never` language precise. **NOT** describe it as a credential sandbox. **BECAUSE** Groundskeeper enforces the accepted open-draft postcondition but cannot prevent every command available to user-authorized Pi credentials.
- **DO** preserve exact closing-reference and draft-status verification in `GhClient`. **NOT** accept a printed URL as completion. **BECAUSE** tracker transition depends on independently verified GitHub state.
- **DO** add installed E2E for process, recovery, or CLI contract changes. **NOT** rely only on mocked Click tests. **BECAUSE** packaging, argv, crash recovery, and state transitions cross real process boundaries.

## Related Context

| Path | What is there |
|---|---|
| `docs/architecture.md` | Both execution paths and ownership seams |
| `docs/config-and-skills.md` | Workflow and automation configuration contracts |
| `docs/reference/cli.md` | Human and agent-facing command surface |
| `tests/e2e/test_e2e_commands.py` | Installed CLI and automation lifecycle proof |
| `docs/AGENTS.md` | Documentation routing index |

<!-- generated-by: context-engineering@2.2.0 | last-updated: 2026-07-12 -->
