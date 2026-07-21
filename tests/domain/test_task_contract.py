import pytest

from groundskeeper.domain.task_contract import (
    ExecutionMode,
    FactoryTaskKind,
    TaskContractError,
    parse_factory_task,
)


def body(task: str, dependencies: str = "None") -> str:
    return f"## Factory Task\n\n{task}\n\n## Dependencies\n\n{dependencies}\n"


@pytest.mark.parametrize(
    ("task", "kind", "mode"),
    [
        (
            "Schema: 1\nKind: runnable\nMode: focused",
            FactoryTaskKind.RUNNABLE,
            ExecutionMode.FOCUSED,
        ),
        (
            "Schema: 1\nKind: runnable\nMode: full",
            FactoryTaskKind.RUNNABLE,
            ExecutionMode.FULL,
        ),
        ("Schema: 1\nKind: tracking", FactoryTaskKind.TRACKING, None),
    ],
)
def test_parses_valid_contracts(
    task: str, kind: FactoryTaskKind, mode: ExecutionMode | None
) -> None:
    contract = parse_factory_task(body(task))
    assert contract.kind is kind
    assert contract.mode is mode


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Schema: 2\nKind: runnable\nMode: full",
        "Schema: 1\nKind: runnable",
        "Schema: 1\nKind: tracking\nMode: full",
        "Schema: 1\nKind: runnable\nMode: noisy",
        "Schema: 1\nKind: runnable\nMode: full\nExtra: nope",
        "Schema: 1\nSchema: 1\nKind: runnable\nMode: full",
    ],
)
def test_rejects_invalid_contracts(value: str) -> None:
    with pytest.raises(TaskContractError):
        parse_factory_task(body(value))


@pytest.mark.parametrize(
    "value",
    [
        "## Dependencies\n\nNone\n",
        "## Factory task\n\nSchema: 1\nKind: runnable\nMode: full\n\n## Dependencies\n\nNone\n",
        body("Schema: 1\nKind: runnable\nMode: full")
        + body("Schema: 1\nKind: runnable\nMode: full"),
        "## Factory Task\n\nSchema: 1\nKind: runnable\nMode: full\n\n"
        "## Dependencies\n\nNone\n\n## Dependencies\n\nNone\n",
    ],
)
def test_requires_exact_unique_sections(value: str) -> None:
    with pytest.raises(TaskContractError):
        parse_factory_task(value)


@pytest.mark.parametrize("prefix", ("```markdown\n", "<!--\n", "Intro\n"))
def test_contract_must_be_first_content(prefix: str) -> None:
    with pytest.raises(TaskContractError, match="first content"):
        parse_factory_task(prefix + body("Schema: 1\nKind: runnable\nMode: full"))


def test_dependencies_must_be_second_h2_section() -> None:
    value = (
        "## Factory Task\n\nSchema: 1\nKind: runnable\nMode: full\n\n"
        "## Context\n\nDetails\n\n## Dependencies\n\nNone\n"
    )

    with pytest.raises(TaskContractError, match="second H2"):
        parse_factory_task(value)


def test_contract_sections_reject_comments_and_fences() -> None:
    for marker in ("<!-- hidden -->", "```text", "~~~text"):
        value = body(f"Schema: 1\nKind: runnable\nMode: full\n{marker}")
        with pytest.raises(TaskContractError, match="comments or fences"):
            parse_factory_task(value)


@pytest.mark.parametrize(
    "dependencies",
    [
        "- https://github.com/owner/repo/issues/2",
        "- https://github.com/owner/repo/pull/3",
    ],
)
def test_parses_cross_repository_dependencies(dependencies: str) -> None:
    contract = parse_factory_task(
        body("Schema: 1\nKind: runnable\nMode: full", dependencies)
    )
    assert contract.dependencies[0].repository == "owner/repo"


@pytest.mark.parametrize(
    "dependencies",
    [
        "None\n- https://github.com/owner/repo/issues/2",
        "- bad",
        "https://github.com/owner/repo/issues/2",
        "- https://github.com/owner/repo/issues/2\n- https://github.com/owner/repo/issues/2",
        "- https://github.com/owner/repo/pulls/2",
        "- https://github.com/owner/repo/issues/not-a-number",
    ],
)
def test_rejects_malformed_dependency_sections(dependencies: str) -> None:
    with pytest.raises(TaskContractError):
        parse_factory_task(body("Schema: 1\nKind: runnable\nMode: full", dependencies))
