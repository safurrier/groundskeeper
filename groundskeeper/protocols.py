"""Protocols defining the ports for Groundskeeper adapters."""

from __future__ import annotations

from typing import Protocol

from groundskeeper.domain.models import RunContext, RunResult, Skill


class SkillStore(Protocol):
    """Loads skills from a source."""

    def list_skills(self) -> list[Skill]: ...

    def get_skill(self, name: str) -> Skill | None: ...


class AgentRunner(Protocol):
    """Executes a skill via an AI agent."""

    def run(self, context: RunContext) -> RunResult: ...

    def is_available(self) -> bool: ...


class CIProvider(Protocol):
    """Generates CI workflow files."""

    def generate_reusable_workflow(self) -> str: ...

    def generate_caller(
        self,
        skill_name: str,
        triggers: dict[str, list[str]],
        depends_on: list[str] | None = None,
    ) -> str: ...

    @property
    def workflow_directory(self) -> str: ...
