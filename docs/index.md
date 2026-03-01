# Groundskeeper

Script AI agents to run on your PRs.

## What is Groundskeeper?

Groundskeeper is a CLI tool that lets you define **skills** (markdown instructions with YAML frontmatter) and **workflows** (chains of skills), then automatically runs them in CI via AI agents.

You give the Groundskeeper skills and tools, and it enforces them on every pull request.

## Quick Start

```bash
# Install
uv tool install groundskeeper

# Initialize in your project
cd your-repo
gk init

# See available skills
gk list

# Test a skill locally
gk run codex-code-review --dry-run

# Generate CI workflows
gk generate
```

## Concepts

| Term | What it is |
|------|-----------|
| **Skill** | A SKILL.md file: YAML frontmatter + markdown body |
| **Workflow** | A chain of skills that run as sequential CI jobs |
| **Runner** | The agent backend that executes skills (Claude Code) |

## How It Works

1. **Define skills** as markdown files in `.groundskeeper/skills/`
2. **Configure workflows** in `.groundskeeper/config.yml`
3. **Generate CI** with `gk generate` — produces GitHub Actions workflow files
4. **PRs trigger skills** automatically via the generated workflows
