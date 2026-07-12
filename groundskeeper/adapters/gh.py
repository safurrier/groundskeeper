"""Typed semantic client for GitHub CLI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from groundskeeper.adapters.process import ProcessClient


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
        result = self._process.run(argv, self._cwd)
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to list GitHub issues")
        raw = self._parse_json(result.stdout, "issue list")
        if not isinstance(raw, list):
            raise GhError("gh issue list returned an unexpected JSON shape")
        issues: list[GhIssue] = []
        for item in cast(list[object], raw):
            try:
                if not isinstance(item, dict):
                    raise TypeError
                item_data = cast(dict[str, object], item)
                label_values = item_data.get("labels", [])
                if not isinstance(label_values, list):
                    raise TypeError
                item_labels_list: list[str] = []
                for label_value in cast(list[object], label_values):
                    if not isinstance(label_value, dict):
                        raise TypeError
                    label_data = cast(dict[str, object], label_value)
                    name = label_data.get("name")
                    if not isinstance(name, str):
                        raise TypeError
                    item_labels_list.append(name)
                item_labels = tuple(item_labels_list)
                author = item_data.get("author")
                if not isinstance(author, dict) or not isinstance(
                    cast(dict[str, object], author).get("login"), str
                ):
                    raise TypeError
                author_data = cast(dict[str, object], author)
                number = item_data.get("number")
                if not isinstance(number, (int, str)):
                    raise TypeError
                if all(label in item_labels for label in labels):
                    issues.append(
                        GhIssue(
                            number=int(number),
                            title=str(item_data["title"]),
                            body=str(item_data.get("body") or ""),
                            url=str(item_data["url"]),
                            author=str(author_data["login"]),
                            labels=item_labels,
                        )
                    )
            except (KeyError, TypeError, ValueError) as error:
                raise GhError("gh issue list returned incomplete issue data") from error
        return issues

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
        )
        if not result.success:
            raise GhError(result.stderr.strip() or "failed to comment on issue")

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

    @staticmethod
    def _parse_json(value: str, operation: str) -> object:
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise GhError(f"gh {operation} returned invalid JSON") from error
