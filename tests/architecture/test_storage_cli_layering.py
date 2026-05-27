from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = ROOT / "javdb" / "storage"
DELETED_HELPER_MODULES = {
    "apps.cli.db._session_helpers",
    "javdb.storage.rollback.session_helpers",
}
SCAN_ROOTS = (
    ROOT / "apps",
    ROOT / "javdb",
    ROOT / "tests",
)


def _storage_python_files() -> list[Path]:
    return sorted(
        path
        for path in STORAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _import_targets(tree: ast.AST) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((alias.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append((node.lineno, node.module))
            targets.extend(
                (node.lineno, f"{node.module}.{alias.name}")
                for alias in node.names
                if alias.name != "*"
            )
    return targets


def test_storage_modules_do_not_import_cli_modules():
    offenders: list[str] = []

    for path in _storage_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line_no, target in _import_targets(tree):
            if target == "apps.cli" or target.startswith("apps.cli."):
                relpath = path.relative_to(ROOT)
                offenders.append(f"{relpath}:{line_no}: imports {target}")

    assert offenders == []


def test_deleted_session_helper_modules_are_not_imported():
    offenders: list[str] = []

    for scan_root in SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line_no, target in _import_targets(tree):
                if target in DELETED_HELPER_MODULES:
                    relpath = path.relative_to(ROOT)
                    offenders.append(f"{relpath}:{line_no}: imports {target}")

    assert offenders == []
