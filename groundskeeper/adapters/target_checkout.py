"""Preflight validation for a configured automation target checkout."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import canonical_github_repository

GIT_PREFLIGHT_TIMEOUT_SECONDS = 30
_SCP_GITHUB_REMOTE_RE = re.compile(
    r"^[^@/:]+@github\.com:(?P<repository>[^/]+/[^/]+)$"
)


class TargetCheckoutError(RuntimeError):
    """The configured target path does not identify the declared repository."""


def _repository_from_remote(remote: str) -> str:
    """Return canonical owner/repo identity from a normal GitHub remote URL."""
    value = remote.strip()
    if value.endswith(".git"):
        value = value[:-4]
    scp_match = _SCP_GITHUB_REMOTE_RE.fullmatch(value)
    if scp_match:
        candidate = scp_match.group("repository")
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            raise TargetCheckoutError(
                "target checkout origin must be a GitHub HTTPS or SSH remote"
            )
        candidate = parsed.path.removeprefix("/")
    try:
        return canonical_github_repository(candidate)
    except ValueError as error:
        raise TargetCheckoutError(
            "target checkout origin does not contain a canonical owner/repo identity"
        ) from error


def validate_target_checkout(
    process: ProcessClient, repository_path: Path, expected_repository: str
) -> None:
    """Prove the target path is a Git worktree for the configured origin."""
    worktree = process.run(
        ("git", "rev-parse", "--is-inside-work-tree"),
        repository_path,
        timeout=GIT_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if not worktree.success or worktree.stdout.strip() != "true":
        raise TargetCheckoutError(
            f"target.repository-path is not a Git worktree: {repository_path}"
        )
    origin = process.run(
        ("git", "remote", "get-url", "origin"),
        repository_path,
        timeout=GIT_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if not origin.success or not origin.stdout.strip():
        raise TargetCheckoutError(
            f"target repository has no configured origin remote: {repository_path}"
        )
    actual_repository = _repository_from_remote(origin.stdout)
    if actual_repository.casefold() != expected_repository.casefold():
        raise TargetCheckoutError(
            "target checkout origin repository does not match target.repository: "
            f"expected {expected_repository}, found {actual_repository}"
        )
