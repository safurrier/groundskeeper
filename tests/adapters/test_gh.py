import json
from pathlib import Path

import pytest

from groundskeeper.adapters.gh import (
    GH_BRANCH_PULL_REQUEST_LIMIT,
    GH_QUERY_TIMEOUT_SECONDS,
    GhClient,
    GhError,
)
from groundskeeper.adapters.process import CommandResult
from groundskeeper.domain.automation import (
    GitHubIssueIdentity,
    ReviewPullRequestState,
)
from groundskeeper.domain.task_contract import DependencyState, GitHubDependency


class FakeProcess:
    def __init__(
        self,
        stdout: str | list[str],
        success: bool | list[bool] = True,
    ) -> None:
        self.stdout = [stdout] if isinstance(stdout, str) else stdout
        self.success = [success] if isinstance(success, bool) else success
        self.argv: tuple[str, ...] = ()
        self.argv_history: list[tuple[str, ...]] = []
        self.timeout: int | None = None

    def run(
        self, argv: tuple[str, ...], cwd: Path, timeout: int | None = None
    ) -> CommandResult:
        self.argv = argv
        self.argv_history.append(argv)
        self.timeout = timeout
        call_index = len(self.argv_history) - 1
        stdout = self.stdout[min(call_index, len(self.stdout) - 1)]
        success = self.success[min(call_index, len(self.success) - 1)]
        return CommandResult(argv, cwd, 0 if success else 1, stdout, "")


def _graphql_page(
    pull_requests: list[dict[str, object]], *, end_cursor: str | None = None
) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "issue": {
                        "closedByPullRequestsReferences": {
                            "nodes": pull_requests,
                            "pageInfo": {
                                "hasNextPage": end_cursor is not None,
                                "endCursor": end_cursor,
                            },
                        }
                    }
                }
            }
        }
    )


def _pr(
    url: str,
    repository: str,
    *,
    draft: bool = True,
    state: str = "OPEN",
) -> dict[str, object]:
    return {
        "url": url,
        "isDraft": draft,
        "state": state,
        "repository": {"nameWithOwner": repository},
    }


def _branch_pr(
    url: str,
    repository: str,
    branch: str,
    *,
    draft: bool = True,
    state: str = "OPEN",
) -> dict[str, object]:
    return {
        "url": url,
        "isDraft": draft,
        "state": state,
        "headRefName": branch,
        "headRepository": {"nameWithOwner": repository},
    }


def test_invalid_json_is_actionable() -> None:
    with pytest.raises(GhError, match="invalid JSON"):
        GhClient(FakeProcess("not json"), Path(".")).list_issues("me/repo", ())


def test_authenticated_login_is_typed_and_cached() -> None:
    process = FakeProcess('{"login":"factory-bot"}')
    client = GhClient(process, Path("."))

    assert client.authenticated_login() == "factory-bot"
    assert client.authenticated_login() == "factory-bot"
    assert process.argv_history == [("gh", "api", "user")]


def test_authenticated_login_command_failure_is_not_cached() -> None:
    process = FakeProcess(
        ["", '{"login":"factory-bot"}'],
        success=[False, True],
    )
    client = GhClient(process, Path("."))

    with pytest.raises(GhError, match="failed to resolve authenticated GitHub actor"):
        client.authenticated_login()

    assert client.authenticated_login() == "factory-bot"
    assert process.argv_history == [
        ("gh", "api", "user"),
        ("gh", "api", "user"),
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "invalid JSON"),
        ("[]", "unexpected JSON shape"),
        ("null", "unexpected JSON shape"),
        ("{}", "incomplete user data"),
        ('{"login":""}', "incomplete user data"),
        ('{"login":42}', "incomplete user data"),
    ],
)
def test_authenticated_login_rejects_invalid_payload(
    payload: str, message: str
) -> None:
    with pytest.raises(GhError, match=message):
        GhClient(FakeProcess(payload), Path(".")).authenticated_login()


def test_issue_discovery_is_server_filtered_and_bounded() -> None:
    process = FakeProcess("[]")
    GhClient(process, Path(".")).list_issues("me/repo", ("factory:ready",))
    assert process.argv[process.argv.index("--label") + 1] == "factory:ready"
    assert process.argv[process.argv.index("--limit") + 1] == "1000"
    assert process.timeout == GH_QUERY_TIMEOUT_SECONDS


def test_issue_discovery_can_include_closed_review_issues() -> None:
    process = FakeProcess("[]")

    GhClient(process, Path(".")).list_issues(
        "me/repo", ("factory:review",), state="all"
    )

    assert process.argv[process.argv.index("--state") + 1] == "all"


def test_issue_discovery_parses_comment_author_and_body() -> None:
    payload = json.dumps(
        [
            {
                "number": 7,
                "title": "Review",
                "body": "",
                "url": "https://github.com/source/queue/issues/7",
                "author": {"login": "alex"},
                "labels": [{"name": "factory:review"}],
                "state": "OPEN",
                "comments": [
                    {
                        "author": {"login": "alex"},
                        "body": "AI-authored factory update: https://pr/9",
                    }
                ],
            }
        ]
    )

    issue = GhClient(FakeProcess(payload), Path(".")).list_issues(
        "source/queue", ("factory:review",), state="all"
    )[0]

    assert issue.comments[0].author == "alex"
    assert issue.comments[0].body.endswith("https://pr/9")


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


def test_reconciliation_is_rooted_at_exact_source_issue_and_filters_target() -> None:
    process = FakeProcess(
        _graphql_page(
            [
                _pr("https://pr/other", "other/repo"),
                _pr("https://pr/exact", "target/repo"),
            ]
        )
    )

    result = GhClient(process, Path(".")).reconcile_pull_requests(
        "target/repo", GitHubIssueIdentity("source/queue", 12)
    )

    assert result.accepted_url == "https://pr/exact"
    assert result.policy_violation is None
    assert process.argv[:3] == ("gh", "api", "graphql")
    assert "owner=source" in process.argv
    assert "name=queue" in process.argv
    assert "number=12" in process.argv
    assert "includeClosedPrs: true" in process.argv[process.argv.index("-f") + 1]


def test_reconciliation_reads_all_issue_closing_pr_pages() -> None:
    process = FakeProcess(
        [
            _graphql_page([], end_cursor="next-page"),
            _graphql_page([_pr("https://pr/exact", "target/repo")]),
        ]
    )

    result = GhClient(process, Path(".")).reconcile_pull_requests(
        "target/repo", GitHubIssueIdentity("source/queue", 12)
    )

    assert result.accepted_url == "https://pr/exact"
    assert len(process.argv_history) == 2
    assert "endCursor=next-page" in process.argv_history[1]


def test_reconciliation_fails_closed_when_page_limit_is_exhausted() -> None:
    pages = [_graphql_page([], end_cursor=f"page-{number}") for number in range(10)]
    client = GhClient(FakeProcess(pages), Path("."))

    with pytest.raises(GhError):
        client.reconcile_pull_requests(
            "target/repo", GitHubIssueIdentity("source/queue", 12)
        )


@pytest.mark.parametrize(
    ("draft", "state", "expected"),
    [
        (False, "OPEN", "not a draft"),
        (True, "CLOSED", "not open"),
        (True, "MERGED", "not open"),
    ],
)
def test_exact_target_non_draft_or_closed_pr_is_policy_violation(
    draft: bool, state: str, expected: str
) -> None:
    process = FakeProcess(
        _graphql_page(
            [_pr("https://pr/invalid", "target/repo", draft=draft, state=state)]
        )
    )

    result = GhClient(process, Path(".")).reconcile_pull_requests(
        "target/repo", GitHubIssueIdentity("source/queue", 12)
    )

    assert result.policy_violation is not None
    assert expected in result.policy_violation


def test_reconciliation_reports_accepted_and_violating_coexistence() -> None:
    process = FakeProcess(
        _graphql_page(
            [
                _pr("https://pr/violation", "target/repo", draft=False),
                _pr("https://pr/accepted", "target/repo"),
            ]
        )
    )

    result = GhClient(process, Path(".")).reconcile_pull_requests(
        "target/repo", GitHubIssueIdentity("source/queue", 12)
    )

    assert result.accepted_url == "https://pr/accepted"
    assert result.policy_violation is not None


@pytest.mark.parametrize(
    ("states", "expected_state", "expected_url"),
    [
        (["OPEN"], ReviewPullRequestState.OPEN, "https://pr/OPEN"),
        (["CLOSED"], ReviewPullRequestState.CLOSED, "https://pr/CLOSED"),
        (
            ["CLOSED", "OPEN", "MERGED"],
            ReviewPullRequestState.MERGED,
            "https://pr/MERGED",
        ),
    ],
)
def test_review_pull_request_resolves_terminal_state_with_safe_precedence(
    states: list[str],
    expected_state: ReviewPullRequestState,
    expected_url: str,
) -> None:
    process = FakeProcess(
        _graphql_page(
            [_pr(f"https://pr/{state}", "target/repo", state=state) for state in states]
        )
    )

    result = GhClient(process, Path(".")).review_pull_request(
        "target/repo",
        GitHubIssueIdentity("source/queue", 12),
        expected_url,
    )

    assert result is not None
    assert result.state is expected_state
    assert result.url == expected_url


def test_review_pull_request_returns_none_without_exact_target() -> None:
    process = FakeProcess(_graphql_page([_pr("https://pr/other", "other/repo")]))

    result = GhClient(process, Path(".")).review_pull_request(
        "target/repo",
        GitHubIssueIdentity("source/queue", 12),
        "https://pr/missing",
    )

    assert result is None


def test_branch_reconciliation_filters_exact_target_and_head_branch() -> None:
    branch = "groundskeeper/task-abc123"
    process = FakeProcess(
        json.dumps(
            [
                _branch_pr("https://pr/other-repo", "other/repo", branch),
                _branch_pr("https://pr/other-branch", "target/repo", "feature"),
                _branch_pr("https://pr/exact", "target/repo", branch),
            ]
        )
    )

    result = GhClient(process, Path(".")).reconcile_branch_pull_requests(
        "target/repo", branch
    )

    assert result.accepted_url == "https://pr/exact"
    assert result.policy_violation is None
    assert process.argv[:3] == ("gh", "pr", "list")
    assert process.argv[process.argv.index("--repo") + 1] == "target/repo"
    assert process.argv[process.argv.index("--head") + 1] == branch
    assert process.argv[process.argv.index("--state") + 1] == "all"


def test_review_branch_pull_request_resolves_merged_state() -> None:
    branch = "groundskeeper/task-abc123"
    process = FakeProcess(
        json.dumps(
            [_branch_pr("https://pr/exact", "target/repo", branch, state="MERGED")]
        )
    )

    result = GhClient(process, Path(".")).review_branch_pull_request(
        "target/repo", branch, "https://pr/exact"
    )

    assert result is not None
    assert result.state is ReviewPullRequestState.MERGED


def test_review_reconciliation_ignores_other_merged_pull_request() -> None:
    process = FakeProcess(
        _graphql_page(
            [
                _pr("https://pr/stale", "target/repo", state="MERGED"),
                _pr("https://pr/accepted", "target/repo", state="OPEN"),
            ]
        )
    )

    result = GhClient(process, Path(".")).review_pull_request(
        "target/repo",
        GitHubIssueIdentity("source/queue", 12),
        "https://pr/accepted",
    )

    assert result is not None
    assert result.url == "https://pr/accepted"
    assert result.state is ReviewPullRequestState.OPEN


@pytest.mark.parametrize(
    ("merged", "reason"), [(True, "completed"), (False, "not planned")]
)
def test_close_issue_preserves_terminal_disposition(merged: bool, reason: str) -> None:
    process = FakeProcess("")

    GhClient(process, Path(".")).close_issue("source/queue", 7, merged=merged)

    assert process.argv[:3] == ("gh", "issue", "close")
    assert process.argv[process.argv.index("--reason") + 1] == reason


def test_branch_reconciliation_fails_closed_when_result_limit_is_reached() -> None:
    branch = "groundskeeper/task-abc123"
    payload = json.dumps(
        [
            _branch_pr(f"https://pr/{number}", "target/repo", branch)
            for number in range(GH_BRANCH_PULL_REQUEST_LIMIT)
        ]
    )

    with pytest.raises(GhError, match="reached the supported result limit"):
        GhClient(FakeProcess(payload), Path(".")).reconcile_branch_pull_requests(
            "target/repo", branch
        )


@pytest.mark.parametrize(
    ("draft", "state", "expected"),
    [
        (False, "OPEN", "not a draft"),
        (True, "CLOSED", "not open"),
        (True, "MERGED", "not open"),
    ],
)
def test_branch_reconciliation_rejects_non_draft_or_closed_pr(
    draft: bool, state: str, expected: str
) -> None:
    branch = "groundskeeper/task-abc123"
    process = FakeProcess(
        json.dumps(
            [
                _branch_pr(
                    "https://pr/invalid",
                    "target/repo",
                    branch,
                    draft=draft,
                    state=state,
                )
            ]
        )
    )

    result = GhClient(process, Path(".")).reconcile_branch_pull_requests(
        "target/repo", branch
    )

    assert result.policy_violation is not None
    assert expected in result.policy_violation


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        json.dumps(
            [
                {
                    "url": "https://pr/incomplete",
                    "isDraft": True,
                    "state": "OPEN",
                    "headRefName": "groundskeeper/task-abc123",
                }
            ]
        ),
    ],
)
def test_incomplete_branch_pull_request_payload_is_rejected(payload: str) -> None:
    with pytest.raises(GhError):
        GhClient(FakeProcess(payload), Path(".")).reconcile_branch_pull_requests(
            "target/repo", "groundskeeper/task-abc123"
        )


@pytest.mark.parametrize(
    "payload",
    [
        _graphql_page(
            [{"url": "https://pr/incomplete", "isDraft": True, "state": "OPEN"}]
        ),
        json.dumps({"data": {"repository": {"issue": None}}}),
    ],
)
def test_incomplete_closing_pull_request_payload_is_rejected(payload: str) -> None:
    with pytest.raises(GhError):
        GhClient(FakeProcess(payload), Path(".")).reconcile_pull_requests(
            "target/repo", GitHubIssueIdentity("source/queue", 12)
        )
