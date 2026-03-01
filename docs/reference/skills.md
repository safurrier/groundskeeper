# Skills Reference

## Skill Format

Skills follow an open standard: YAML frontmatter + markdown body.

### File Structure

```
<skill-name>/
├── SKILL.md          # required
├── scripts/          # optional — executable code
├── references/       # optional — docs loaded into context
└── assets/           # optional — files used in output
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Kebab-case identifier |
| `description` | Yes | What the skill does |
| `argument-hint` | No | Documents `$ARGUMENTS` usage |
| `allowed-tools` | No | Tools the agent gets |
| `tags` | No | Categorization tags |
| `triggers` | No | CI event triggers |

### Example

```yaml
---
name: codex-code-review
description: Reviews PR diffs for bugs, security, style.
argument-hint: "[mode] [scope]"
allowed-tools: [Read, Grep, Glob, Bash]
tags: [review, quality]
triggers:
  pull_request: [ready_for_review, synchronize, reopened]
---

Review the code changes with $ARGUMENTS.
```

## Resolution Order

Skills are resolved in this order (first match wins):

1. **Local**: `.groundskeeper/skills/<name>/SKILL.md`
2. **Builtin**: Bundled with the groundskeeper package

Local skills always shadow builtins with the same name.

## Built-in Skills

### `codex-code-review`

Reviews PR diffs for bugs, security issues, and style violations. Provides a structured verdict with confidence level.

### `context-files`

Generates or updates AGENTS.md and CLAUDE.md context files for the repository.
