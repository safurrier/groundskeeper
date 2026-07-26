"""Preflight validation for a configured automation target checkout."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import canonical_github_repository
from groundskeeper.domain.config import AutomationCheckout

GIT_PREFLIGHT_TIMEOUT_SECONDS = 30
_SCP_GITHUB_REMOTE_RE = re.compile(r"^[^@/:]+@github\.com:(?P<repository>[^/]+/[^/]+)$")


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
    process: ProcessClient,
    repository_path: Path,
    expected_repository: str,
    checkout: AutomationCheckout | None = None,
) -> str | None:
    """Prove the donor path, origin, and configured base ref without mutation."""
    checkout = checkout or AutomationCheckout()
    _validate_target_repository(process, repository_path, expected_repository)
    return _validate_base_ref(process, repository_path, checkout)


def prepare_target_checkout(
    process: ProcessClient,
    repository_path: Path,
    expected_repository: str,
    checkout: AutomationCheckout,
) -> str | None:
    """Refresh and prove a live isolated-worktree donor before tracker access."""
    _validate_target_repository(process, repository_path, expected_repository)
    if checkout.mode == "isolated-worktree" and checkout.refresh == "fetch":
        if (
            checkout.base_ref is None
        ):  # Defensive: config parsing already requires this.
            raise TargetCheckoutError("isolated-worktree checkout requires a base ref")
        branch = checkout.base_ref.removeprefix("origin/")
        refresh = process.run(
            (
                "git",
                "fetch",
                "--no-tags",
                "origin",
                f"refs/heads/{branch}:refs/remotes/origin/{branch}",
            ),
            repository_path,
            timeout=GIT_PREFLIGHT_TIMEOUT_SECONDS,
        )
        if not refresh.success:
            detail = refresh.stderr.strip() or refresh.stdout.strip() or "unknown error"
            raise TargetCheckoutError(f"target checkout refresh failed: {detail}")
    return _validate_base_ref(process, repository_path, checkout)


def _validate_target_repository(
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


def _validate_base_ref(
    process: ProcessClient, repository_path: Path, checkout: AutomationCheckout
) -> str | None:
    """Require the typed isolated-worktree base to resolve to one commit."""
    if checkout.mode == "existing":
        return None
    if checkout.base_ref is None:  # Defensive: config parsing already requires this.
        raise TargetCheckoutError("isolated-worktree checkout requires a base ref")
    resolved = process.run(
        (
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{checkout.base_ref}^{{commit}}",
        ),
        repository_path,
        timeout=GIT_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if not resolved.success or not resolved.stdout.strip():
        raise TargetCheckoutError(
            f"target checkout base ref does not resolve to a commit: {checkout.base_ref}"
        )
    return resolved.stdout.strip()
