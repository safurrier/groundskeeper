import subprocess
from pathlib import Path

import pytest

from groundskeeper.adapters.execution_source import (
    ExecutionSourceError,
    ExecutionSourceManager,
    resolve_execution_path,
    validate_execution_tree,
)
from groundskeeper.adapters.process import ProcessClient


def _commit(repo: Path, message: str, content: str) -> str:
    (repo / "config.txt").write_text(content)
    subprocess.run(["git", "add", "config.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Groundskeeper Test",
            "-c",
            "user.email=groundskeeper@example.com",
            "commit",
            "-qm",
            message,
        ],
        cwd=repo,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_refresh_materializes_remote_commit_without_touching_dirty_donor(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    donor = tmp_path / "donor"
    state = tmp_path / "state"
    subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True
    )
    first_sha = _commit(seed, "first", "first\n")
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=seed, check=True)
    subprocess.run(["git", "clone", "-q", "-b", "main", remote, donor], check=True)
    (donor / "config.txt").write_text("dirty local value\n")
    (donor / "untracked.txt").write_text("preserve me\n")
    second_sha = _commit(seed, "second", "remote value\n")
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)

    manager = ExecutionSourceManager(
        ProcessClient(),
        donor,
        "origin/main",
        "fetch",
        state,
    )
    source = manager.prepare()

    assert first_sha != second_sha
    assert source.commit == second_sha
    assert source.path != donor
    assert source.path.is_relative_to(state / "execution-sources")
    assert (source.path / "config.txt").read_text() == "remote value\n"
    assert (donor / "config.txt").read_text() == "dirty local value\n"
    assert (donor / "untracked.txt").read_text() == "preserve me\n"

    third_sha = _commit(seed, "third", "new remote value\n")
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)
    advanced = manager.prepare()

    assert advanced.path == source.path
    assert advanced.commit == third_sha
    assert (advanced.path / "config.txt").read_text() == "new remote value\n"
    assert (donor / "config.txt").read_text() == "dirty local value\n"


def test_reused_execution_source_must_remain_clean(tmp_path: Path) -> None:
    donor = tmp_path / "donor"
    subprocess.run(["git", "init", "-q", "-b", "main", donor], check=True)
    _commit(donor, "base", "base\n")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=donor,
        check=True,
    )
    manager = ExecutionSourceManager(
        ProcessClient(),
        donor,
        "origin/main",
        "none",
        tmp_path / "state",
    )
    source = manager.prepare()
    (source.path / "unexpected.txt").write_text("dirty\n")

    with pytest.raises(ExecutionSourceError, match="not clean"):
        manager.prepare()


@pytest.mark.parametrize(
    "configured",
    [
        Path("/absolute/config.yml"),
        Path("../outside.yml"),
        Path("nested/../../outside.yml"),
    ],
)
def test_execution_config_path_cannot_escape_snapshot(
    tmp_path: Path, configured: Path
) -> None:
    with pytest.raises(ExecutionSourceError, match="relative path inside"):
        resolve_execution_path(tmp_path / "snapshot", configured)


def test_execution_config_path_resolves_inside_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"

    resolved = resolve_execution_path(snapshot, Path(".groundskeeper/config.yml"))

    assert resolved == snapshot / ".groundskeeper/config.yml"


def test_execution_tree_rejects_symlink_outside_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    skills = snapshot / ".groundskeeper/skills"
    external = tmp_path / "mutable-skill"
    skills.mkdir(parents=True)
    external.mkdir()
    (skills / "escaped").symlink_to(external, target_is_directory=True)

    with pytest.raises(ExecutionSourceError, match="must remain inside"):
        validate_execution_tree(snapshot, skills)
