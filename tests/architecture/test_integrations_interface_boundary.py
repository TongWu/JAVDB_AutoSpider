from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INTEGRATIONS_ROOT = ROOT / "javdb" / "integrations"
APPS_CLI_ROOT = ROOT / "apps" / "cli"

INTEGRATION_CLI_SURFACE_ALLOWLIST = {
    "javdb/integrations/rclone/manager.py": {
        "argparse_import",
        "parse_arguments",
        "main",
        "dunder_main",
        "argparse_namespace_annotation",
        "sys_exit",
    },
    "javdb/integrations/notify/email.py": {
        "argparse_import",
        "parse_arguments",
        "main",
        "dunder_main",
        "sys_exit",
    },
}

APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST = {
    "apps/cli/rclone/manager.py",
    "apps/cli/notify/email.py",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_sys_exit_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
    )


def _is_dunder_main_check(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(_string_value(comparator) == "__main__" for comparator in test.comparators)
    )


def _integration_cli_surface(tree: ast.AST) -> set[str]:
    surface: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "argparse" for alias in node.names):
                surface.add("argparse_import")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "argparse":
                surface.add("argparse_import")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "parse_arguments":
                surface.add("parse_arguments")
            elif node.name == "main":
                surface.add("main")
        elif _is_sys_exit_call(node):
            surface.add("sys_exit")
        elif _is_dunder_main_check(node):
            surface.add("dunder_main")
        elif isinstance(node, ast.Attribute) and node.attr == "Namespace":
            value = node.value
            if isinstance(value, ast.Name) and value.id == "argparse":
                surface.add("argparse_namespace_annotation")
    return surface


def _imports_integration_module(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("javdb.integrations.")
            ):
                return True
    return False


def _assigns_to_sys_modules_dunder_name(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            if not isinstance(target.value, ast.Attribute):
                continue
            if target.value.attr != "modules":
                continue
            if not isinstance(target.value.value, ast.Name):
                continue
            if target.value.value.id != "sys":
                continue
            key = target.slice
            if isinstance(key, ast.Name) and key.id == "__name__":
                return True
            if isinstance(key, ast.Constant) and key.value == "__name__":
                return True
    return False


def test_integrations_do_not_add_untracked_cli_surface():
    offenders: list[str] = []

    for path in _python_files(INTEGRATIONS_ROOT):
        relpath = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        actual = _integration_cli_surface(tree)
        allowed = INTEGRATION_CLI_SURFACE_ALLOWLIST.get(relpath, set())
        unexpected = sorted(actual - allowed)
        if unexpected:
            offenders.append(f"{relpath}: {', '.join(unexpected)}")

    assert offenders == []


def test_apps_cli_does_not_add_untracked_integration_aliases():
    offenders: list[str] = []

    for path in _python_files(APPS_CLI_ROOT):
        relpath = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        is_integration_alias = (
            _imports_integration_module(tree)
            and _assigns_to_sys_modules_dunder_name(tree)
        )
        if is_integration_alias and relpath not in APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST:
            offenders.append(relpath)

    assert offenders == []
