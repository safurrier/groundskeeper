"""Adapters for Groundskeeper — stores, runners, and CI providers."""

from __future__ import annotations

from groundskeeper.domain.models import Skill
from groundskeeper.protocols import SkillStore


def resolve_skill(name: str, stores: list[SkillStore]) -> Skill | None:
    """First match wins across stores."""
    for store in stores:
        skill = store.get_skill(name)
        if skill is not None:
            return skill
    return None


def list_all_skills(stores: list[SkillStore]) -> list[Skill]:
    """Deduplicated, first-store-wins ordering."""
    seen: set[str] = set()
    skills: list[Skill] = []
    for store in stores:
        for skill in store.list_skills():
            if skill.name not in seen:
                seen.add(skill.name)
                skills.append(skill)
    return skills
