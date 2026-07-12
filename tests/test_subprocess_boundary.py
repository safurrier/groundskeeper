import ast
from pathlib import Path


def test_subprocess_is_confined_to_process_adapter() -> None:
    package = Path(__file__).parents[1] / "groundskeeper"
    offenders: list[str] = []
    allowed = package / "adapters" / "process.py"
    for path in package.rglob("*.py"):
        if path == allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "subprocess" for alias in node.names
            ):
                offenders.append(str(path.relative_to(package)))
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                offenders.append(str(path.relative_to(package)))
    assert offenders == []
