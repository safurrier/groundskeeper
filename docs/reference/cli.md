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
