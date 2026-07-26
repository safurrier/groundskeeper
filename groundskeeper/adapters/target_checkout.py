"""Preflight validation for a configured automation target checkout."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import (
    AutomationTask,
    automation_task_identity,
    canonical_github_repository,
)
from groundskeeper.domain.config import AutomationCheckout

GIT_QUERY_TIMEOUT_SECONDS = 30
GIT_MUTATION_TIMEOUT_SECONDS = 300
_SCP_GITHUB_REMOTE_RE = re.compile(r"^[^@/:]+@github\.com:(?P<repository>[^/]+/[^/]+)$")


class TargetCheckoutError(RuntimeError):
    """The configured target path does not identify the declared repository."""


def target_checkout_common_dir(process: ProcessClient, path: Path) -> Path:
    """Return the normalized Git metadata directory shared by linked worktrees."""
    result = process.run(
        ("git", "rev-parse", "--git-common-dir"),
        path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    if not result.success or not result.stdout.strip():
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise TargetCheckoutError(
            f"could not identify target workspace Git directory: {detail}"
        )
    common_dir = Path(result.stdout.strip())
    return (
        common_dir.resolve()
        if common_dir.is_absolute()
        else (path / common_dir).resolve()
    )


@dataclass(frozen=True)
class TargetWorkspace:
    """Execution checkout and its immutable task base."""

    path: Path
    base_sha: str | None


class TargetCheckoutManager:
    """Own deterministic task worktrees above one validated donor checkout."""

    def __init__(
        self,
        process: ProcessClient,
        repository_path: Path,
        expected_repository: str,
        checkout: AutomationCheckout,
        candidate_base_sha: str | None,
        state_root: Path,
    ) -> None:
        self._process = process
        self._repository_path = repository_path
        self._expected_repository = expected_repository
        self._checkout = checkout
        self._candidate_base_sha = candidate_base_sha
        self._state_root = state_root

    def readiness_for(self, task: AutomationTask) -> str | None:
        """Return a nonmutating reason this task cannot use the managed target."""
        if self._checkout.mode != "managed-worktree":
            return None
        branch_ref = f"refs/heads/{_task_branch(task)}"
        current_branch = _managed_worktree_branch(self._process, self._repository_path)
        status = self._process.run(
            ("git", "status", "--porcelain"),
            self._repository_path,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
        )
        if not status.success:
            raise TargetCheckoutError("could not inspect managed target worktree")
        if status.stdout and current_branch != branch_ref:
            occupied = current_branch or "detached HEAD"
            return (
                "managed target has uncommitted work for another task "
                f"({occupied}); preserve or reconcile it before dispatch"
            )
        return None

    def workspace_for(self, task: AutomationTask) -> TargetWorkspace:
        """Create or reuse the task checkout selected by the typed policy."""
        if task.target_repository.casefold() != self._expected_repository.casefold():
            raise TargetCheckoutError(
                "task target repository does not match the prepared checkout: "
                f"expected {self._expected_repository}, found {task.target_repository}"
            )
        if self._checkout.mode == "existing":
            return TargetWorkspace(self._repository_path, None)
        if self._candidate_base_sha is None:
            raise TargetCheckoutError(
                "isolated-worktree checkout has no resolved candidate base"
            )

        branch = _task_branch(task)
        task_key = branch.removeprefix("groundskeeper/task-")
        target_key = hashlib.sha256(
            (
                f"{self._expected_repository.casefold()}\0"
                f"{target_checkout_common_dir(self._process, self._repository_path)}"
            ).encode()
        ).hexdigest()[:16]
        branch_ref = f"refs/heads/{branch}"
        base_ref = f"refs/groundskeeper/bases/{task_key}"
        if self._checkout.mode == "managed-worktree":
            return self._managed_workspace_for(branch, branch_ref, base_ref)
        workspace_path = self._state_root / "worktrees" / target_key / task_key
        workspace_exists = workspace_path.exists()
        branch_exists = self._resolve_optional_ref(branch_ref) is not None
        pinned_base = self._resolve_optional_ref(base_ref)
        if pinned_base is None:
            if workspace_exists or branch_exists:
                raise TargetCheckoutError(
                    "target workspace state exists without its immutable base pin; "
                    f"manual recovery is required for {workspace_path}"
                )
            self._run_or_raise(
                ("git", "update-ref", base_ref, self._candidate_base_sha, ""),
                "could not pin the immutable task base",
            )
            pinned_base = self._candidate_base_sha

        if workspace_exists:
            if not workspace_path.is_dir():
                raise TargetCheckoutError(
                    f"target workspace path is not a directory: {workspace_path}"
                )
            if not branch_exists:
                raise TargetCheckoutError(
                    "target workspace exists without its deterministic branch: "
                    f"{workspace_path}"
                )
            try:
                self._validate_reused_workspace(workspace_path, branch_ref)
            except TargetCheckoutError:
                if not self._recover_interrupted_workspace(
                    workspace_path, branch_ref, pinned_base
                ):
                    raise
                self._run_or_raise(
                    ("git", "worktree", "add", str(workspace_path), branch),
                    "could not recover the interrupted target workspace",
                )
        else:
            try:
                workspace_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise TargetCheckoutError(
                    f"could not create target workspace directory: {error}"
                ) from error
            if branch_exists:
                argv = ("git", "worktree", "add", str(workspace_path), branch)
            else:
                argv = (
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    branch,
                    str(workspace_path),
                    pinned_base,
                )
            self._run_or_raise(argv, "could not create or recover the target workspace")
        return TargetWorkspace(workspace_path, pinned_base)

    def _managed_workspace_for(
        self, branch: str, branch_ref: str, base_ref: str
    ) -> TargetWorkspace:
        """Create or resume a task branch in one explicit disposable worktree."""
        _validate_managed_worktree(self._process, self._repository_path)
        candidate_base = self._candidate_base_sha
        if candidate_base is None:
            raise TargetCheckoutError("managed target has no resolved candidate base")
        branch_exists = self._resolve_optional_ref(branch_ref) is not None
        pinned_base = self._resolve_optional_ref(base_ref)
        if pinned_base is None:
            if branch_exists:
                raise TargetCheckoutError(
                    "managed target branch exists without its immutable base pin"
                )
            self._run_or_raise(
                ("git", "update-ref", base_ref, candidate_base, ""),
                "could not pin the immutable task base",
            )
            pinned_base = candidate_base
        current_branch = _managed_worktree_branch(self._process, self._repository_path)
        if current_branch == branch_ref:
            return TargetWorkspace(self._repository_path, pinned_base)
        status = self._process.run(
            ("git", "status", "--porcelain"),
            self._repository_path,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
        )
        if not status.success or status.stdout:
            raise TargetCheckoutError(
                "managed target must be clean before switching task branches"
            )
        argv = (
            ("git", "switch", "--no-guess", branch)
            if branch_exists
            else ("git", "switch", "--create", branch, pinned_base)
        )
        self._run_or_raise(argv, "could not prepare the managed target workspace")
        _validate_managed_worktree(
            self._process, self._repository_path, expected_branch_ref=branch_ref
        )
        return TargetWorkspace(self._repository_path, pinned_base)

    def _recover_interrupted_workspace(
        self, workspace_path: Path, branch_ref: str, pinned_base: str
    ) -> bool:
        """Quarantine a narrowly identified, unregistered partial worktree."""
        if workspace_path.is_symlink():
            return False
        git_file = workspace_path / ".git"
        if not git_file.is_file() or git_file.is_symlink():
            return False
        donor_common_dir = target_checkout_common_dir(
            self._process, self._repository_path
        )
        expected_admin_dir = donor_common_dir / "worktrees" / workspace_path.name
        try:
            pointer = git_file.read_text().strip()
        except OSError:
            return False
        if pointer != f"gitdir: {expected_admin_dir}" or expected_admin_dir.exists():
            return False
        branch_sha = self._resolve_optional_ref(branch_ref)
        if branch_sha != pinned_base:
            return False
        worktrees = self._process.run(
            ("git", "worktree", "list", "--porcelain"),
            self._repository_path,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
        )
        if not worktrees.success:
            return False
        registered_paths = {
            line.removeprefix("worktree ")
            for line in worktrees.stdout.splitlines()
            if line.startswith("worktree ")
        }
        if str(workspace_path) in registered_paths:
            return False
        quarantine = workspace_path.with_name(f"{workspace_path.name}.interrupted")
        if quarantine.exists():
            return False
        try:
            workspace_path.rename(quarantine)
        except OSError as error:
            raise TargetCheckoutError(
                f"could not quarantine interrupted target workspace: {error}"
            ) from error
        return True

    def _resolve_optional_ref(self, ref: str) -> str | None:
        result = self._process.run(
            (
                "git",
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{ref}^{{commit}}",
            ),
            self._repository_path,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
        )
        if result.success and result.stdout.strip():
            return result.stdout.strip()
        if result.exit_code == 1:
            return None
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise TargetCheckoutError(
            f"could not resolve target workspace ref {ref}: {detail}"
        )

    def _validate_reused_workspace(
        self, workspace_path: Path, expected_branch_ref: str
    ) -> None:
        donor_common_dir = target_checkout_common_dir(
            self._process, self._repository_path
        )
        workspace_common_dir = target_checkout_common_dir(self._process, workspace_path)
        if workspace_common_dir != donor_common_dir:
            raise TargetCheckoutError(
                "target workspace belongs to a different Git repository: "
                f"{workspace_path}"
            )
        branch = self._process.run(
            ("git", "symbolic-ref", "--quiet", "HEAD"),
            workspace_path,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
        )
        if not branch.success or branch.stdout.strip() != expected_branch_ref:
            raise TargetCheckoutError(
                f"target workspace is not on its deterministic branch: {workspace_path}"
            )

    def _run_or_raise(self, argv: tuple[str, ...], action: str) -> None:
        result = self._process.run(
            argv,
            self._repository_path,
            timeout=GIT_MUTATION_TIMEOUT_SECONDS,
        )
        if not result.success:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise TargetCheckoutError(f"{action}: {detail}")


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


def _task_branch(task: AutomationTask) -> str:
    """Return the deterministic local branch for one source task."""
    task_key = hashlib.sha256(
        automation_task_identity(task).encode("utf-8")
    ).hexdigest()[:16]
    return f"groundskeeper/task-{task_key}"


def validate_target_checkout(
    process: ProcessClient,
    repository_path: Path,
    expected_repository: str,
    checkout: AutomationCheckout | None = None,
) -> str | None:
    """Prove the donor path, origin, and configured base ref without mutation."""
    checkout = checkout or AutomationCheckout()
    _validate_target_repository(process, repository_path, expected_repository)
    if checkout.mode == "managed-worktree":
        _validate_managed_worktree(process, repository_path)
    return _validate_base_ref(process, repository_path, checkout)


def prepare_target_checkout(
    process: ProcessClient,
    repository_path: Path,
    expected_repository: str,
    checkout: AutomationCheckout,
) -> str | None:
    """Refresh and prove a live isolated-worktree donor before tracker access."""
    _validate_target_repository(process, repository_path, expected_repository)
    if checkout.mode == "managed-worktree":
        _validate_managed_worktree(process, repository_path)
    if (
        checkout.mode in {"isolated-worktree", "managed-worktree"}
        and checkout.refresh == "fetch"
    ):
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
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            ),
            repository_path,
            timeout=GIT_MUTATION_TIMEOUT_SECONDS,
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
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    if not worktree.success or worktree.stdout.strip() != "true":
        raise TargetCheckoutError(
            f"target.repository-path is not a Git worktree: {repository_path}"
        )
    origin = process.run(
        ("git", "remote", "get-url", "origin"),
        repository_path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
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
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    if not resolved.success or not resolved.stdout.strip():
        raise TargetCheckoutError(
            f"target checkout base ref does not resolve to a commit: {checkout.base_ref}"
        )
    return resolved.stdout.strip()


def _validate_managed_worktree(
    process: ProcessClient,
    repository_path: Path,
    *,
    expected_branch_ref: str | None = None,
) -> None:
    """Require a linked disposable worktree with a safe current branch."""
    git_dir = process.run(
        ("git", "rev-parse", "--git-dir"),
        repository_path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    common_dir = process.run(
        ("git", "rev-parse", "--git-common-dir"),
        repository_path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    if not git_dir.success or not common_dir.success:
        raise TargetCheckoutError("managed target is not a valid linked worktree")
    resolved_git_dir = (
        Path(git_dir.stdout.strip()).resolve()
        if Path(git_dir.stdout.strip()).is_absolute()
        else (repository_path / git_dir.stdout.strip()).resolve()
    )
    resolved_common_dir = (
        Path(common_dir.stdout.strip()).resolve()
        if Path(common_dir.stdout.strip()).is_absolute()
        else (repository_path / common_dir.stdout.strip()).resolve()
    )
    if resolved_git_dir == resolved_common_dir:
        raise TargetCheckoutError(
            "managed target must be a linked disposable worktree, not the primary checkout"
        )
    branch_ref = _managed_worktree_branch(process, repository_path)
    if expected_branch_ref is not None:
        if branch_ref != expected_branch_ref:
            raise TargetCheckoutError(
                "managed target is not on its deterministic task branch"
            )
    elif branch_ref and not branch_ref.startswith("refs/heads/groundskeeper/task-"):
        raise TargetCheckoutError(
            "managed target must be detached or on a Groundskeeper task branch"
        )
    status = process.run(
        ("git", "status", "--porcelain"),
        repository_path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    if not status.success:
        raise TargetCheckoutError("could not inspect managed target worktree")
    if status.stdout and not branch_ref.startswith("refs/heads/groundskeeper/task-"):
        raise TargetCheckoutError("managed target worktree must be clean")


def _managed_worktree_branch(process: ProcessClient, repository_path: Path) -> str:
    """Return the current branch ref, or empty text when detached."""
    branch = process.run(
        ("git", "symbolic-ref", "--quiet", "HEAD"),
        repository_path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
    )
    if branch.exit_code not in {0, 1}:
        raise TargetCheckoutError("could not identify managed target branch")
    return branch.stdout.strip() if branch.success else ""
