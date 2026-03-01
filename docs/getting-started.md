# Getting Started

## Installation

```bash
uv tool install groundskeeper
```

This makes the `gk` command available globally.

## Initialize a Project

```bash
cd your-repo
gk init
```

This creates:

- `.groundskeeper/config.yml` — workflow configuration
- `.groundskeeper/skills/` — directory for local skills

To also generate CI workflow files, add `ci: github-actions` to your config and run:

```bash
gk generate
```

## Configuration

The config file at `.groundskeeper/config.yml` defines your workflows:

```yaml
version: 1
runner: claude-code
ci: github-actions

workflows:
  pr-review:
    triggers:
      pull_request: [ready_for_review, synchronize, reopened]
    skills:
      - codex-code-review
```

## Creating a Skill

Skills follow an open standard. Create a directory with a `SKILL.md` file:

```
.groundskeeper/skills/my-skill/
└── SKILL.md
```

The SKILL.md file has YAML frontmatter and a markdown body:

```markdown
---
name: my-skill
description: Does something useful
argument-hint: "[mode]"
allowed-tools:
  - Read
  - Grep
tags: [quality]
triggers:
  pull_request: [ready_for_review]
---

You are an AI assistant. Do the thing with $ARGUMENTS.
```

## Workflow Commands

```bash
gk list              # Show available skills
gk show <name>       # Display skill details
gk check [name]      # Validate skill(s)
gk run <name> --dry-run  # Preview rendered prompt
gk generate          # Regenerate CI workflows
gk render <name>     # Output raw prompt (used by CI)
```
