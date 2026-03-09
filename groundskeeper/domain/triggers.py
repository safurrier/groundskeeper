"""Typed trigger definitions for workflow scheduling and CI events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GitHubEvent(Enum):
    """GitHub webhook events supported as workflow triggers."""

    PULL_REQUEST = "pull_request"
    PUSH = "push"
    ISSUES = "issues"


@dataclass(frozen=True)
class EventTrigger:
    """A GitHub event trigger with optional activity type filters."""

    event: GitHubEvent
    types: tuple[str, ...]


@dataclass(frozen=True)
class ScheduleTrigger:
    """A cron-based schedule trigger."""

    cron: str


@dataclass(frozen=True)
class ManualTrigger:
    """A workflow_dispatch trigger for manual runs."""

    pass


TriggerSpec = EventTrigger | ScheduleTrigger | ManualTrigger
