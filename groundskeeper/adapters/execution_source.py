"""Immutable configuration snapshots for host-local scheduled automation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from groundskeeper.adapters.process import ProcessClient

GIT_OPERATION_TIMEOUT_SECONDS = 30


class ExecutionSourceError(RuntimeError):
    """The configured scheduler execution source cannot be used safely."""


@dataclass(frozen=True)
class ExecutionSource:
    """One immutable repository snapshot used for a scheduled run."""

    path: Path
    commit: str


def resolve_execution_path(snapshot: Path, configured: Path) -> Path:
    """Resolve a repository-relative path without allowing snapshot escape."""
    if configured.is_absolute():
        raise ExecutionSourceError(
            "scheduled config must be a relative path inside its execution snapshot"
        )
    resolved_snapshot = snapshot.resolve()
    resolved = (resolved_snapshot / configured).resolve()
    if not resolved.is_relative_to(resolved_snapshot):
        raise ExecutionSourceError(
            "scheduled config must be a relative path inside its execution snapshot"
        )
    return resolved


def validate_execution_tree(snapshot: Path, tree: Path) -> None:
    """Reject symlinks that let scheduled inputs escape their Git snapshot."""
    if not tree.exists():
        if tree.is_symlink():
            raise ExecutionSourceError(
                "scheduled input cannot be resolved inside its execution snapshot: "
                f"{tree}"
            )
        return
    resolved_snapshot = snapshot.resolve()
    pending = [tree]
    visited_directories: set[Path] = set()
    while pending:
        candidate = pending.pop()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise ExecutionSourceError(
                "scheduled input cannot be resolved inside its execution snapshot: "
                f"{candidate}"
            ) from error
        if not resolved.is_relative_to(resolved_snapshot):
            raise ExecutionSourceError(
                "scheduled inputs must remain inside their execution snapshot: "
                f"{candidate}"
            )
        if not resolved.is_dir() or resolved in visited_directories:
            continue
        visited_directories.add(resolved)
        try:
            pending.extend(resolved.iterdir())
        except OSError as error:
            raise ExecutionSourceError(
                "scheduled input cannot be traversed inside its execution snapshot: "
                f"{candidate}"
            ) from error


class ExecutionSourceManager:
    """Refresh and materialize one bounded scheduler source worktree."""

    def __init__(
        self,
        process: ProcessClient,
        repository_path: Path,
        ref: str,
        refresh: str,
        state_root: Path,
    ) -> None:
        self._process = process
        self._repository_path = repository_path.resolve()
        self._ref = ref
        self._refresh = refresh
        self._state_root = state_root

    def prepare(self) -> ExecutionSource:
        """Resolve the configured ref and return its clean detached snapshot."""
        self._validate_donor()
        if self._refresh == "fetch":
            self._fetch_origin_ref()
        elif self._refresh != "none":
            raise ExecutionSourceError(
                f"unsupported execution source refresh policy: {self._refresh}"
            )
        commit = self._resolve_commit()
        common_dir = self.common_dir()
        source_key = hashlib.sha256(f"{common_dir}\0{self._ref}".encode()).hexdigest()[
            :16
        ]
        snapshot = (
            self._state_root / "execution-sources" / source_key / "worktree"
        ).resolve()
        if snapshot.exists():
            current = self._validate_snapshot(snapshot, common_dir)
            if current != commit:
                self._run_or_raise(
                    ("git", "checkout", "--detach", commit),
                    snapshot,
                    "could not advance managed execution source",
                )
        else:
            try:
                snapshot.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ExecutionSourceError(
                    f"could not create execution source directory: {error}"
                ) from error
            self._run_or_raise(
                ("git", "worktree", "add", "--detach", str(snapshot), commit),
                self._repository_path,
                "could not materialize execution source",
            )
        pinned = self._validate_snapshot(snapshot, common_dir)
        if pinned != commit:
            raise ExecutionSourceError(
                f"managed execution source is not at its pinned commit: {snapshot}"
            )
        return ExecutionSource(snapshot, commit)

    def common_dir(self) -> Path:
        """Return the Git metadata directory shared by the source worktrees."""
        result = self._process.run(
            ("git", "rev-parse", "--git-common-dir"),
            self._repository_path,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not result.success or not result.stdout.strip():
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ExecutionSourceError(
                f"could not identify execution source Git directory: {detail}"
            )
        value = Path(result.stdout.strip())
        return (
            value.resolve()
            if value.is_absolute()
            else (self._repository_path / value).resolve()
        )

    def _validate_donor(self) -> None:
        result = self._process.run(
            ("git", "rev-parse", "--is-inside-work-tree"),
            self._repository_path,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not result.success or result.stdout.strip() != "true":
            raise ExecutionSourceError(
                "execution source repository path is not a Git worktree: "
                f"{self._repository_path}"
            )

    def _fetch_origin_ref(self) -> None:
        if not self._ref.startswith("origin/"):
            raise ExecutionSourceError(
                "execution source refresh 'fetch' requires an origin/* ref"
            )
        branch = self._ref.removeprefix("origin/")
        valid = self._process.run(
            ("git", "check-ref-format", "--branch", branch),
            self._repository_path,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not valid.success:
            raise ExecutionSourceError(
                f"execution source ref is not a valid origin branch: {self._ref}"
            )
        self._run_or_raise(
            (
                "git",
                "fetch",
                "--no-tags",
                "origin",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            ),
            self._repository_path,
            "execution source refresh failed",
        )

    def _resolve_commit(self) -> str:
        result = self._process.run(
            (
                "git",
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{self._ref}^{{commit}}",
            ),
            self._repository_path,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not result.success or not result.stdout.strip():
            raise ExecutionSourceError(
                f"execution source ref does not resolve to a commit: {self._ref}"
            )
        return result.stdout.strip()

    def _validate_snapshot(self, snapshot: Path, expected_common_dir: Path) -> str:
        if not snapshot.is_dir():
            raise ExecutionSourceError(
                f"execution source path is not a directory: {snapshot}"
            )
        snapshot_common = self._git_value(
            ("git", "rev-parse", "--git-common-dir"),
            snapshot,
            "could not identify managed execution source repository",
        )
        common_path = Path(snapshot_common)
        normalized_common = (
            common_path.resolve()
            if common_path.is_absolute()
            else (snapshot / common_path).resolve()
        )
        if normalized_common != expected_common_dir:
            raise ExecutionSourceError(
                f"managed execution source belongs to a different repository: {snapshot}"
            )
        head = self._git_value(
            ("git", "rev-parse", "HEAD"),
            snapshot,
            "could not resolve managed execution source HEAD",
        )
        branch = self._process.run(
            ("git", "symbolic-ref", "--quiet", "HEAD"),
            snapshot,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if branch.success or branch.exit_code != 1:
            raise ExecutionSourceError(
                f"managed execution source is not detached: {snapshot}"
            )
        status = self._process.run(
            ("git", "status", "--porcelain"),
            snapshot,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not status.success or status.stdout.strip():
            raise ExecutionSourceError(
                f"managed execution source is not clean: {snapshot}"
            )
        return head

    def _git_value(self, argv: tuple[str, ...], cwd: Path, action: str) -> str:
        result = self._process.run(
            argv,
            cwd,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not result.success or not result.stdout.strip():
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ExecutionSourceError(f"{action}: {detail}")
        return result.stdout.strip()

    def _run_or_raise(self, argv: tuple[str, ...], cwd: Path, action: str) -> None:
        result = self._process.run(
            argv,
            cwd,
            timeout=GIT_OPERATION_TIMEOUT_SECONDS,
        )
        if not result.success:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ExecutionSourceError(f"{action}: {detail}")
