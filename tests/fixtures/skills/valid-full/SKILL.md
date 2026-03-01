---
name: valid-full
description: A fully-featured skill for testing
argument-hint: "[mode] [scope]"
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
tags: [review, quality]
triggers:
  pull_request: [ready_for_review, synchronize, reopened]
---

Review the code changes with $ARGUMENTS.

Check for bugs, security issues, and style violations.
