"""Typed semantic client for GitHub CLI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from groundskeeper.adapters.process import ProcessClient
from groundskeeper.domain.task_contract import DependencyState, GitHubDependency

GH_QUERY_TIMEOUT_SECONDS = 30
GH_MUTATION_TIMEOUT_SECONDS = 30


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
            "number,title,body,url,author,labels",
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
                "number,title,body,url,author,labels",
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
            if not isinstance(login, str) or not isinstance(number, (int, str)):
                raise TypeError
            return GhIssue(
                number=int(number),
                title=str(item["title"]),
                body=str(item.get("body") or ""),
                url=str(item["url"]),
                author=login,
                labels=tuple(labels),
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

    def linked_pull_request(self, repository: str, issue: int) -> str | None:
        result = self._process.run(
            (
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--json",
                "url,isDraft,closingIssuesReferences",
                "--limit",
                "1000",
            ),
            self._cwd,
            timeout=GH_QUERY_TIMEOUT_SECONDS,
        )
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to query pull requests")
        values = self._parse_json(result.stdout, "pull request list")
        if not isinstance(values, list):
            raise GhError("gh pr list returned an unexpected JSON shape")
        try:
            for pull_request in cast(list[object], values):
                if not isinstance(pull_request, dict):
                    raise TypeError
                pr_data = cast(dict[str, object], pull_request)
                if pr_data.get("isDraft") is not True:
                    continue
                references = pr_data.get("closingIssuesReferences")
                if not isinstance(references, list):
                    raise TypeError
                closes_issue = False
                for reference in cast(list[object], references):
                    if not isinstance(reference, dict):
                        raise TypeError
                    reference_data = cast(dict[str, object], reference)
                    number = reference_data.get("number")
                    if not isinstance(number, (int, str)):
                        raise TypeError
                    if int(number) == issue:
                        closes_issue = True
                if closes_issue:
                    return str(pr_data["url"])
            return None
        except (KeyError, TypeError, ValueError) as error:
            raise GhError("gh pr list returned incomplete pull request data") from error

    def closing_pr_policy_violation(self, repository: str, issue: int) -> str | None:
        """Report a closing PR that fails the accepted draft-result postcondition."""
        result = self._process.run(
            (
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "all",
                "--json",
                "url,isDraft,state,closingIssuesReferences",
                "--limit",
                "1000",
            ),
            self._cwd,
            timeout=GH_QUERY_TIMEOUT_SECONDS,
        )
        if not result.success:
            raise GhError(
                result.stderr.strip() or "failed to query pull request policy"
            )
        values = self._parse_json(result.stdout, "pull request policy query")
        if not isinstance(values, list):
            raise GhError("gh pr list returned an unexpected JSON shape")
        try:
            for pull_request in cast(list[object], values):
                if not isinstance(pull_request, dict):
                    raise TypeError
                pr_data = cast(dict[str, object], pull_request)
                if not self._closes_issue(pr_data, issue):
                    continue
                url = pr_data.get("url")
                if not isinstance(url, str):
                    raise TypeError
                if pr_data.get("isDraft") is not True:
                    return f"Closing pull request is not a draft: {url}"
                state = pr_data.get("state")
                if state != "OPEN":
                    return f"Closing pull request is not open: {url}"
            return None
        except (TypeError, ValueError) as error:
            raise GhError("gh pr list returned incomplete pull request data") from error

    @staticmethod
    def _closes_issue(pr_data: dict[str, object], issue: int) -> bool:
        """Return whether a pull request has the exact closing reference."""
        references = pr_data.get("closingIssuesReferences")
        if not isinstance(references, list):
            raise TypeError
        for reference in cast(list[object], references):
            if not isinstance(reference, dict):
                raise TypeError
            number = cast(dict[str, object], reference).get("number")
            if not isinstance(number, (int, str)):
                raise TypeError
            if int(number) == issue:
                return True
        return False

    @staticmethod
    def _parse_json(value: str, operation: str) -> object:
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise GhError(f"gh {operation} returned invalid JSON") from error
