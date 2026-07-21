from pathlib import Path

import pytest

from groundskeeper.adapters.gh import GH_QUERY_TIMEOUT_SECONDS, GhClient, GhError
from groundskeeper.adapters.process import CommandResult
from groundskeeper.domain.task_contract import DependencyState, GitHubDependency


class FakeProcess:
    def __init__(self, stdout: str, success: bool = True) -> None:
        self.stdout = stdout
        self.success = success
        self.argv: tuple[str, ...] = ()
        self.timeout: int | None = None

    def run(
        self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
    ) -> CommandResult:
        self.argv = argv
        self.timeout = timeout
        return CommandResult(argv, cwd, 0 if self.success else 1, self.stdout, "")


def test_invalid_json_is_actionable() -> None:
    with pytest.raises(GhError, match="invalid JSON"):
        GhClient(FakeProcess("not json"), Path(".")).list_issues("me/repo", ())


def test_issue_discovery_is_server_filtered_and_bounded() -> None:
    process = FakeProcess("[]")
    GhClient(process, Path(".")).list_issues("me/repo", ("factory:ready",))
    assert process.argv[process.argv.index("--label") + 1] == "factory:ready"
    assert process.argv[process.argv.index("--limit") + 1] == "1000"
    assert process.timeout == GH_QUERY_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("dependency", "payload", "expected"),
    [
        (
            GitHubDependency(
                "other/repo", 7, "issue", "https://github.com/other/repo/issues/7"
            ),
            '{"state":"closed"}',
            DependencyState.COMPLETE,
        ),
        (
            GitHubDependency(
                "other/repo", 8, "issue", "https://github.com/other/repo/issues/8"
            ),
            '{"state":"open"}',
            DependencyState.UNRESOLVED,
        ),
        (
            GitHubDependency(
                "other/repo", 9, "pull", "https://github.com/other/repo/pull/9"
            ),
            '{"state":"closed","merged_at":"2026-01-01T00:00:00Z"}',
            DependencyState.COMPLETE,
        ),
        (
            GitHubDependency(
                "other/repo", 10, "pull", "https://github.com/other/repo/pull/10"
            ),
            '{"state":"closed","merged_at":null}',
            DependencyState.INVALID,
        ),
    ],
)
def test_dependency_state_resolves_cross_repository_terminal_status(
    dependency: GitHubDependency, payload: str, expected: DependencyState
) -> None:
    process = FakeProcess(payload)
    state, _reason = GhClient(process, Path(".")).dependency_state(dependency)
    assert state is expected
    assert any(
        argument.startswith(f"repos/{dependency.repository}/")
        for argument in process.argv
    )


def test_issue_form_pull_request_dependency_is_invalid() -> None:
    dependency = GitHubDependency(
        "other/repo", 12, "issue", "https://github.com/other/repo/issues/12"
    )
    process = FakeProcess(
        '{"state":"closed","pull_request":{"url":"https://api.github.com/pulls/12"}}'
    )

    state, reason = GhClient(process, Path(".")).dependency_state(dependency)

    assert state is DependencyState.INVALID
    assert "canonical /pull/ URL" in reason


def test_dependency_state_reports_inaccessible_without_parsing_error() -> None:
    dependency = GitHubDependency(
        "other/repo", 11, "issue", "https://github.com/other/repo/issues/11"
    )
    process = FakeProcess("not-found", success=False)

    state, reason = GhClient(process, Path(".")).dependency_state(dependency)

    assert state is DependencyState.INACCESSIBLE
    assert dependency.url in reason


def test_linked_pr_matches_exact_closing_issue_reference() -> None:
    process = FakeProcess(
        '[{"url":"https://pr/incidental","isDraft":true,"closingIssuesReferences":[{"number":112}]},'
        '{"url":"https://pr/exact","isDraft":true,"closingIssuesReferences":[{"number":12}]}]'
    )
    assert (
        GhClient(process, Path(".")).linked_pull_request("me/repo", 12)
        == "https://pr/exact"
    )
    assert any("closingIssuesReferences" in argument for argument in process.argv)
    assert "--search" not in process.argv
    assert process.argv[process.argv.index("--limit") + 1] == "1000"


def test_linked_pr_finds_exact_reference_after_first_hundred() -> None:
    unrelated = ",".join(
        f'{{"url":"https://pr/{number}","isDraft":true,"closingIssuesReferences":[{{"number":{number + 1000}}}]}}'
        for number in range(150)
    )
    process = FakeProcess(
        f'[{unrelated},{{"url":"https://pr/exact","isDraft":true,'
        '"closingIssuesReferences":[{"number":12}]}]'
    )
    assert (
        GhClient(process, Path(".")).linked_pull_request("me/repo", 12)
        == "https://pr/exact"
    )


def test_closing_non_draft_or_merged_pr_is_a_policy_violation() -> None:
    process = FakeProcess(
        '[{"url":"https://pr/ready","isDraft":false,"state":"OPEN",'
        '"closingIssuesReferences":[{"number":12}]},'
        '{"url":"https://pr/merged","isDraft":false,"state":"MERGED",'
        '"closingIssuesReferences":[{"number":12}]}]'
    )
    violation = GhClient(process, Path(".")).closing_pr_policy_violation("me/repo", 12)
    assert violation == "Closing pull request is not a draft: https://pr/ready"
    assert "--state" in process.argv
    assert process.argv[process.argv.index("--state") + 1] == "all"


def test_linked_pr_ignores_non_draft_closing_reference() -> None:
    process = FakeProcess(
        '[{"url":"https://pr/ready","isDraft":false,'
        '"closingIssuesReferences":[{"number":12}]}]'
    )
    assert GhClient(process, Path(".")).linked_pull_request("me/repo", 12) is None
