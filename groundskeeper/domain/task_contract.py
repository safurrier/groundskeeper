"""Strict, provider-neutral Factory Task and dependency contract parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FactoryTaskKind(str, Enum):
    """Whether an issue is runnable work or tracking-only coordination."""

    TASK = "task"
    TRACKING = "tracking"


class ExecutionMode(str, Enum):
    """User-approved execution depth for one runnable task."""

    COMPACT = "compact"
    FULL = "full"


@dataclass(frozen=True)
class GitHubDependency:
    """One canonical GitHub issue or pull request dependency."""

    repository: str
    number: int
    kind: str
    url: str


@dataclass(frozen=True)
class FactoryTaskContract:
    """Typed contract extracted from one issue body."""

    schema: int
    kind: FactoryTaskKind
    mode: ExecutionMode | None
    dependencies: tuple[GitHubDependency, ...]


class DependencyState(str, Enum):
    """Terminal-state result for one GitHub dependency."""

    COMPLETE = "complete"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"
    INACCESSIBLE = "inaccessible"


class TaskContractError(ValueError):
    """Raised when an issue does not satisfy the clean-break task contract."""


_FACTORY_HEADING = "## Factory Task"
_DEPENDENCIES_HEADING = "## Dependencies"
_SECTION_RE = re.compile(r"(?m)^## ([^\n]+)\n")
_DEPENDENCY_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(issues|pull)/(\d+)$"
)


def parse_factory_task(body: str) -> FactoryTaskContract:
    """Parse the strict contract from the first two H2 sections."""
    if not body.lstrip("\r\n").startswith(f"{_FACTORY_HEADING}\n"):
        raise TaskContractError("Factory Task must be the first content in the issue")
    headings = list(_SECTION_RE.finditer(body))
    if len(headings) < 2 or headings[0].group(0).strip() != _FACTORY_HEADING:
        raise TaskContractError("Factory Task must be the first H2 section")
    if headings[1].group(0).strip() != _DEPENDENCIES_HEADING:
        raise TaskContractError("Dependencies must be the second H2 section")
    task = _section(body, _FACTORY_HEADING)
    dependencies = _section(body, _DEPENDENCIES_HEADING)
    if any(
        marker in task or marker in dependencies for marker in ("<!--", "```", "~~~")
    ):
        raise TaskContractError(
            "Factory Task contract must not contain comments or fences"
        )
    values = _key_values(task, _FACTORY_HEADING)
    required = {"Schema", "Kind"}
    unknown = set(values) - {"Schema", "Kind", "Mode"}
    missing = required - set(values)
    if unknown:
        raise TaskContractError(
            f"{_FACTORY_HEADING} has unknown key '{sorted(unknown)[0]}'"
        )
    if missing:
        raise TaskContractError(
            f"{_FACTORY_HEADING} is missing key '{sorted(missing)[0]}'"
        )
    if values["Schema"] != "1":
        raise TaskContractError("Factory Task Schema must be 1")
    try:
        kind = FactoryTaskKind(values["Kind"])
    except ValueError as error:
        raise TaskContractError("Factory Task Kind must be task or tracking") from error
    mode_value = values.get("Mode")
    if kind is FactoryTaskKind.TASK and mode_value is None:
        raise TaskContractError("Factory Task task requires Mode")
    if kind is FactoryTaskKind.TRACKING and mode_value is not None:
        raise TaskContractError("Factory Task tracking forbids Mode")
    try:
        mode = ExecutionMode(mode_value) if mode_value is not None else None
    except ValueError as error:
        raise TaskContractError("Factory Task Mode must be compact or full") from error
    return FactoryTaskContract(1, kind, mode, _dependencies(dependencies))


def _section(body: str, heading: str) -> str:
    matches = [
        match
        for match in _SECTION_RE.finditer(body)
        if match.group(0).strip() == heading
    ]
    if len(matches) != 1:
        count = "missing" if not matches else "duplicate"
        raise TaskContractError(f"{count} exact {heading} section")
    start = matches[0].end()
    following = _SECTION_RE.search(body, start)
    return body[start : following.start() if following else len(body)].strip()


def _key_values(value: str, heading: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise TaskContractError(f"{heading} has malformed line '{line}'")
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if not key or not raw or key in values:
            raise TaskContractError(f"{heading} has malformed or duplicate key '{key}'")
        values[key] = raw
    return values


def _dependencies(value: str) -> tuple[GitHubDependency, ...]:
    if value == "None":
        return ()
    if not value or value == "None" or "None" in value.splitlines():
        raise TaskContractError("Dependencies must be exactly None or URL bullets")
    dependencies: list[GitHubDependency] = []
    for line in value.splitlines():
        if not line.startswith("- "):
            raise TaskContractError(
                "Dependencies must use one full GitHub URL per bullet"
            )
        url = line[2:].strip()
        match = _DEPENDENCY_RE.fullmatch(url)
        if match is None:
            raise TaskContractError(
                "Dependencies contain malformed GitHub issue or pull URL"
            )
        owner, repository, path_kind, number = match.groups()
        dependencies.append(
            GitHubDependency(
                repository=f"{owner}/{repository}",
                number=int(number),
                kind="pull" if path_kind == "pull" else "issue",
                url=url,
            )
        )
    if len({item.url for item in dependencies}) != len(dependencies):
        raise TaskContractError("Dependencies must not contain duplicate URLs")
    return tuple(dependencies)
