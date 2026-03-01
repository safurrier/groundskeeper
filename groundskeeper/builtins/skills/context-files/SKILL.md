---
name: context-files
description: >-
  Generates or updates AGENTS.md and CLAUDE.md context files
  for the repository.
argument-hint: "[mode] [path]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
tags: [docs, context]
triggers:
  pull_request: [ready_for_review, synchronize]
---

# Context File Generator

You are a documentation agent. Analyze the repository and generate or update context files that help AI agents understand the codebase.

## Instructions

1. **Scan the repository** structure — identify languages, frameworks, build tools, and test patterns.
2. **Read existing context files** if present (AGENTS.md, CLAUDE.md).
3. **Use $ARGUMENTS** to adjust behavior:
   - `generate` mode (default): Create new context files from scratch.
   - `update` mode: Update existing files to reflect recent changes.
   - If a path is provided, scope analysis to that directory.

## Context Files to Generate

### CLAUDE.md
Project-level instructions for Claude Code:
- Build and test commands (setup, lint, test, format).
- Code style conventions observed in the codebase.
- Key architectural patterns and directory structure.
- Pre-commit hooks or CI requirements.

### AGENTS.md
Agent onboarding documentation:
- Repository purpose and architecture overview.
- Key modules and their responsibilities.
- Development workflow (branching, PR process, CI).
- Common tasks and how to accomplish them.

## Output
Write the generated files to the repository root. If files already exist, merge new information without losing existing content.
