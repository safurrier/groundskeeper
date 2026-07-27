"""Typed semantic client for GitHub CLI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.automation import (
    GitHubIssueIdentity,
    PullRequestReconciliation,
    canonical_github_repository,
)
from groundskeeper.domain.task_contract import DependencyState, GitHubDependency

GH_QUERY_TIMEOUT_SECONDS = 30
GH_MUTATION_TIMEOUT_SECONDS = 30
GH_CLOSING_PULL_REQUEST_PAGE_LIMIT = 10
GH_BRANCH_PULL_REQUEST_LIMIT = 100

_ISSUE_CLOSING_PULL_REQUESTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      closedByPullRequestsReferences(
        first: 100
        after: $endCursor
        includeClosedPrs: true
      ) {
        nodes {
          url
          isDraft
          state
          repository { nameWithOwner }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()


class GhError(RuntimeError):
    """A GitHub CLI operation failed."""


@dataclass(frozen=True)
class GhIssue:
    number: int
    title: str
    body: str
    url: str
    author: str
    labels: tuple[str, ...]
    state: str


class GhClient:
    """GitHub issue operations expressed without subprocess details."""

    def __init__(self, process: ProcessClient, cwd: Path) -> None:
        self._process = process
        self._cwd = cwd

    def list_issues(self, repository: str, labels: tuple[str, ...]) -> list[GhIssue]:
        argv = (
            "gh",
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "open",
            "--limit",
            "1000",
            "--label",
            ",".join(labels),
            "--json",
            "number,title,body,url,author,labels,state",
        )
        result = self._process.run(argv, self._cwd, timeout=GH_QUERY_TIMEOUT_SECONDS)
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to list GitHub issues")
        raw = self._parse_json(result.stdout, "issue list")
        if not isinstance(raw, list):
            raise GhError("gh issue list returned an unexpected JSON shape")
        issues = [
            self._parse_issue(item, "issue list") for item in cast(list[object], raw)
        ]
        return [
            issue for issue in issues if all(label in issue.labels for label in labels)
        ]

    def get_issue(self, repository: str, issue: int) -> GhIssue:
        """Read one current issue snapshot after a lifecycle mutation."""
        result = self._process.run(
            (
                "gh",
                "issue",
                "view",
                str(issue),
                "--repo",
                repository,
                "--json",
                "number,title,body,url,author,labels,state",
            ),
            self._cwd,
            timeout=GH_QUERY_TIMEOUT_SECONDS,
        )
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to read GitHub issue")
        return self._parse_issue(
            self._parse_json(result.stdout, "issue view"), "issue view"
        )

    @staticmethod
    def _parse_issue(value: object, operation: str) -> GhIssue:
        try:
            if not isinstance(value, dict):
                raise TypeError
            item = cast(dict[str, object], value)
            label_values = item.get("labels", [])
            if not isinstance(label_values, list):
                raise TypeError
            labels: list[str] = []
            for label_value in cast(list[object], label_values):
                if not isinstance(label_value, dict):
                    raise TypeError
                name = cast(dict[str, object], label_value).get("name")
                if not isinstance(name, str):
                    raise TypeError
                labels.append(name)
            author = item.get("author")
            if not isinstance(author, dict):
                raise TypeError
            login = cast(dict[str, object], author).get("login")
            number = item.get("number")
            state = item.get("state")
            if (
                not isinstance(login, str)
                or not isinstance(number, (int, str))
                or not isinstance(state, str)
            ):
                raise TypeError
            return GhIssue(
                number=int(number),
                title=str(item["title"]),
                body=str(item.get("body") or ""),
                url=str(item["url"]),
                author=login,
                labels=tuple(labels),
                state=state.lower(),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GhError(f"gh {operation} returned incomplete issue data") from error

    def replace_label(self, repository: str, issue: int, old: str, new: str) -> None:
        result = self._process.run(
            (
                "gh",
                "issue",
                "edit",
                str(issue),
                "--repo",
                repository,
                "--remove-label",
                old,
                "--add-label",
                new,
            ),
            self._cwd,
            timeout=GH_MUTATION_TIMEOUT_SECONDS,
        )
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to update issue labels")

    def comment(self, repository: str, issue: int, body: str) -> None:
        result = self._process.run(
            (
                "gh",
                "issue",
                "comment",
                str(issue),
                "--repo",
                repository,
                "--body",
                body,
            ),
            self._cwd,
            timeout=GH_MUTATION_TIMEOUT_SECONDS,
        )
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to comment on issue")

    def dependency_state(
        self, dependency: GitHubDependency
    ) -> tuple[DependencyState, str]:
        """Resolve one cross-repository issue or pull request dependency."""
        endpoint = (
            f"repos/{dependency.repository}/pulls/{dependency.number}"
            if dependency.kind == "pull"
            else f"repos/{dependency.repository}/issues/{dependency.number}"
        )
        result = self._process.run(
            ("gh", "api", endpoint), self._cwd, timeout=GH_QUERY_TIMEOUT_SECONDS
        )
        if not result.success:
            return (
                DependencyState.INACCESSIBLE,
                f"Cannot resolve dependency {dependency.url}",
            )
        raw = self._parse_json(result.stdout, "dependency query")
        if not isinstance(raw, dict):
            raise GhError("gh dependency query returned an unexpected JSON shape")
        dependency_data = cast(dict[str, object], raw)
        if dependency.kind == "pull":
            state = dependency_data.get("state")
            merged_at = dependency_data.get("merged_at")
            if state == "open":
                return (
                    DependencyState.UNRESOLVED,
                    f"Dependency pull request is open: {dependency.url}",
                )
            if state == "closed" and merged_at:
                return DependencyState.COMPLETE, ""
            return (
                DependencyState.INVALID,
                f"Dependency pull request closed without merge: {dependency.url}",
            )
        if "pull_request" in dependency_data:
            return (
                DependencyState.INVALID,
                f"Pull request dependency must use its canonical /pull/ URL: {dependency.url}",
            )
        state = dependency_data.get("state")
        if state == "open":
            return (
                DependencyState.UNRESOLVED,
                f"Dependency issue is open: {dependency.url}",
            )
        if state == "closed":
            return DependencyState.COMPLETE, ""
        return (
            DependencyState.INACCESSIBLE,
            f"Dependency issue has unknown state: {dependency.url}",
        )

    def reconcile_pull_requests(
        self, target_repository: str, source_issue: GitHubIssueIdentity
    ) -> PullRequestReconciliation:
        """Read PRs that GitHub records as closing the exact source issue.

        The documented issue-rooted connection avoids scanning unrelated target
        pull requests. An accepted open draft wins if accepted and violating
        target PRs coexist; otherwise the first target policy violation blocks.
        """
        canonical_github_repository(target_repository)
        owner, name = source_issue.repository.split("/")
        pull_requests: list[dict[str, object]] = []
        end_cursor: str | None = None
        for page_number in range(GH_CLOSING_PULL_REQUEST_PAGE_LIMIT):
            argv = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_ISSUE_CLOSING_PULL_REQUESTS_QUERY}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={source_issue.number}",
            ]
            if end_cursor is not None:
                argv.extend(("-F", f"endCursor={end_cursor}"))
            result = self._process.run(
                tuple(argv), self._cwd, timeout=GH_QUERY_TIMEOUT_SECONDS
            )
            if not result.success:
                raise GhError(
                    result.stderr.strip() or "failed to query closing pull requests"
                )
            page = self._parse_json(result.stdout, "closing pull request GraphQL query")
            nodes, end_cursor = self._parse_closing_pull_request_page(page)
            try:
                pull_requests.extend(
                    self._parse_closing_pull_request(node) for node in nodes
                )
            except (TypeError, ValueError) as error:
                raise GhError(
                    "gh GraphQL returned incomplete closing pull request data"
                ) from error
            if end_cursor is None:
                break
            if page_number == GH_CLOSING_PULL_REQUEST_PAGE_LIMIT - 1:
                raise GhError(
                    "closing pull request query exceeded the supported page limit"
                )

        accepted_url: str | None = None
        policy_violation: str | None = None
        for pr_data in pull_requests:
            repository = cast(str, pr_data["repository"])
            if repository.casefold() != target_repository.casefold():
                continue
            url = cast(str, pr_data["url"])
            if pr_data["isDraft"] is True and pr_data["state"] == "OPEN":
                accepted_url = accepted_url or url
                continue
            if policy_violation is None:
                if pr_data["isDraft"] is not True:
                    policy_violation = f"Closing pull request is not a draft: {url}"
                else:
                    policy_violation = f"Closing pull request is not open: {url}"
        return PullRequestReconciliation(accepted_url, policy_violation)

    def reconcile_branch_pull_requests(
        self, target_repository: str, branch: str
    ) -> PullRequestReconciliation:
        """Read target pull requests for one deterministic automation branch."""
        canonical_github_repository(target_repository)
        result = self._process.run(
            (
                "gh",
                "pr",
                "list",
                "--repo",
                target_repository,
                "--head",
                branch,
                "--state",
                "all",
                "--limit",
                str(GH_BRANCH_PULL_REQUEST_LIMIT),
                "--json",
                "url,isDraft,state,headRefName,headRepository",
            ),
            self._cwd,
            timeout=GH_QUERY_TIMEOUT_SECONDS,
        )
        if not result.success:
            raise GhError(
                result.stderr.strip() or "failed to query branch pull requests"
            )
        raw = self._parse_json(result.stdout, "branch pull request query")
        if not isinstance(raw, list):
            raise GhError("gh pr list returned an unexpected JSON shape")
        if len(raw) >= GH_BRANCH_PULL_REQUEST_LIMIT:
            raise GhError(
                "branch pull request query reached the supported result limit"
            )

        accepted_url: str | None = None
        policy_violation: str | None = None
        try:
            pull_requests = [
                self._parse_branch_pull_request(item)
                for item in cast(list[object], raw)
            ]
        except (TypeError, ValueError) as error:
            raise GhError("gh pr list returned incomplete pull request data") from error
        for repository, url, is_draft, state, head_branch in pull_requests:
            if repository.casefold() != target_repository.casefold():
                continue
            if head_branch != branch:
                continue
            if is_draft and state == "OPEN":
                accepted_url = accepted_url or url
                continue
            if policy_violation is None:
                if not is_draft:
                    policy_violation = f"Branch pull request is not a draft: {url}"
                else:
                    policy_violation = f"Branch pull request is not open: {url}"
        return PullRequestReconciliation(accepted_url, policy_violation)

    @staticmethod
    def _parse_branch_pull_request(
        value: object,
    ) -> tuple[str, str, bool, str, str]:
        if not isinstance(value, dict):
            raise TypeError
        pr_data = cast(dict[str, object], value)
        repository = pr_data.get("headRepository")
        if not isinstance(repository, dict):
            raise TypeError
        name_with_owner = cast(dict[str, object], repository).get("nameWithOwner")
        url = pr_data.get("url")
        is_draft = pr_data.get("isDraft")
        state = pr_data.get("state")
        branch = pr_data.get("headRefName")
        if (
            not isinstance(name_with_owner, str)
            or not isinstance(url, str)
            or not isinstance(is_draft, bool)
            or state not in {"OPEN", "CLOSED", "MERGED"}
            or not isinstance(branch, str)
        ):
            raise TypeError
        return name_with_owner, url, is_draft, cast(str, state), branch

    @staticmethod
    def _parse_closing_pull_request_page(
        value: object,
    ) -> tuple[list[object], str | None]:
        try:
            if not isinstance(value, dict):
                raise TypeError
            data = cast(dict[str, object], value).get("data")
            if not isinstance(data, dict):
                raise TypeError
            repository_data = cast(dict[str, object], data).get("repository")
            if not isinstance(repository_data, dict):
                raise TypeError
            issue = cast(dict[str, object], repository_data).get("issue")
            if not isinstance(issue, dict):
                raise TypeError
            connection = cast(dict[str, object], issue).get(
                "closedByPullRequestsReferences"
            )
            if not isinstance(connection, dict):
                raise TypeError
            connection_data = cast(dict[str, object], connection)
            nodes = connection_data.get("nodes")
            page_info = connection_data.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(page_info, dict):
                raise TypeError
            page_info_data = cast(dict[str, object], page_info)
            has_next_page = page_info_data.get("hasNextPage")
            cursor = page_info_data.get("endCursor")
            if not isinstance(has_next_page, bool):
                raise TypeError
            if has_next_page:
                if not isinstance(cursor, str) or not cursor:
                    raise TypeError
                return cast(list[object], nodes), cursor
            if cursor is not None and not isinstance(cursor, str):
                raise TypeError
            return cast(list[object], nodes), None
        except (KeyError, TypeError, ValueError) as error:
            raise GhError(
                "gh GraphQL returned incomplete closing pull request data"
            ) from error

    @staticmethod
    def _parse_closing_pull_request(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise TypeError
        pr_data = cast(dict[str, object], value)
        url = pr_data.get("url")
        is_draft = pr_data.get("isDraft")
        state = pr_data.get("state")
        repository = pr_data.get("repository")
        if (
            not isinstance(url, str)
            or not isinstance(is_draft, bool)
            or state not in {"OPEN", "CLOSED", "MERGED"}
            or not isinstance(repository, dict)
        ):
            raise TypeError
        name_with_owner = cast(dict[str, object], repository).get("nameWithOwner")
        if not isinstance(name_with_owner, str):
            raise TypeError
        canonical_github_repository(name_with_owner)
        return {
            "url": url,
            "isDraft": is_draft,
            "state": state,
            "repository": name_with_owner,
        }

    @staticmethod
    def _parse_json(value: str, operation: str) -> object:
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise GhError(f"gh {operation} returned invalid JSON") from error
