from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_GLOBS = [
    "javdb/spider/**/*.py",
]

ALLOWED_FILES = {
    "javdb/spider/runtime/state.py",
    "javdb/spider/runtime/context.py",
}

FORBIDDEN_DIRECT_STATE_FIELDS = {
    "parsed_links",
    "proxy_ban_html_files",
    "global_proxy_pool",
    "global_request_handler",
    "global_proxy_coordinator",
    "global_login_state_client",
    "global_movie_claim_client",
    "global_runner_registry_client",
    "global_recommend_proxy_policy",
    "global_work_distributor_client",
    "runtime_holder_id",
    "login_attempted",
    "refreshed_session_cookie",
    "logged_in_proxy_name",
    "current_login_state_version",
    "login_attempts_per_proxy",
    "login_failures_per_proxy",
    "login_total_attempts",
    "login_total_budget",
    "always_bypass_time",
    "proxies_requiring_cf_bypass",
}


def _production_files():
    for pattern in PRODUCTION_GLOBS:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ALLOWED_FILES:
                continue
            if "__pycache__" in rel:
                continue
            yield path


def _legacy_state_field_offenders(text: str, source: str) -> list[str]:
    tree = ast.parse(text)
    lines = text.splitlines()

    def line_for(node: ast.AST) -> str:
        return lines[node.lineno - 1].strip()

    def import_state_aliases(root: ast.AST) -> set[str]:
        aliases = {"state", "_state"}
        for node in ast.walk(root):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "javdb.spider.runtime.state":
                        aliases.add(alias.asname or "javdb")
                        aliases.add(alias.asname or alias.name)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "javdb.spider.runtime"
            ):
                for alias in node.names:
                    if alias.name == "state":
                        aliases.add(alias.asname or alias.name)
        return aliases

    imported_state_aliases = import_state_aliases(tree)

    def dotted_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = dotted_name(node.value)
            if prefix is None:
                return None
            return f"{prefix}.{node.attr}"
        return None

    def module_level_state_aliases(root: ast.Module) -> set[str]:
        aliases = set(imported_state_aliases)
        for node in root.body:
            if isinstance(node, ast.Assign) and state_alias_value(node.value, aliases):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases.add(target.id)
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and state_alias_value(node.value, aliases)
                and isinstance(node.target, ast.Name)
            ):
                aliases.add(node.target.id)
        return aliases

    def state_alias_value(value: ast.AST, aliases: set[str]) -> bool:
        name = dotted_name(value)
        if name is not None:
            return name in aliases
        if isinstance(value, ast.IfExp):
            return state_alias_value(value.body, aliases) or state_alias_value(
                value.orelse,
                aliases,
            )
        return False

    def returns_state_module(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for node in ast.walk(function):
            if isinstance(node, ast.Return) and node.value is not None:
                if state_alias_value(node.value, imported_state_aliases):
                    return True
        return False

    state_returning_functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and returns_state_module(node)
    }
    module_state_aliases = module_level_state_aliases(tree)

    offenders: list[str] = []
    seen_offenders: set[tuple[int, str]] = set()

    class StateBoundaryVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.alias_stack: list[set[str]] = [set(module_state_aliases)]

        @property
        def aliases(self) -> set[str]:
            return self.alias_stack[-1]

        def add_offender(self, node: ast.AST) -> None:
            line = line_for(node)
            key = (node.lineno, line)
            if key in seen_offenders:
                return
            seen_offenders.add(key)
            offenders.append(f"{source}:{node.lineno}: {line}")

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name == "javdb.spider.runtime.state":
                    self.aliases.add(alias.asname or "javdb")

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module == "javdb.spider.runtime":
                for alias in node.names:
                    if alias.name == "state":
                        self.aliases.add(alias.asname or alias.name)
            if node.module == "javdb.spider.runtime.state":
                for alias in node.names:
                    if alias.name in FORBIDDEN_DIRECT_STATE_FIELDS:
                        self.add_offender(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.alias_stack.append(set(module_state_aliases))
            self.generic_visit(node)
            self.alias_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node: ast.Assign) -> None:
            if self._state_alias_value(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.aliases.add(target.id)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None and self._state_alias_value(node.value):
                if isinstance(node.target, ast.Name):
                    self.aliases.add(node.target.id)
            self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> None:
            if node.value is not None and state_alias_value(node.value, self.aliases):
                self.add_offender(node)
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if (
                self._state_alias_value(node.value)
                and node.attr in FORBIDDEN_DIRECT_STATE_FIELDS
            ):
                self.add_offender(node)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in self.aliases
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in FORBIDDEN_DIRECT_STATE_FIELDS
            ):
                self.add_offender(node)
            self.generic_visit(node)

        def _state_alias_value(self, value: ast.AST) -> bool:
            if state_alias_value(value, self.aliases):
                return True
            return (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in state_returning_functions
            )

    StateBoundaryVisitor().visit(tree)
    return offenders


def test_architecture_guard_catches_direct_state_alias_assignments():
    offenders = _legacy_state_field_offenders(
        "\n".join(
            [
                "services = state",
                "pool = services.global_proxy_pool",
                "legacy = _state",
                "holder = legacy.runtime_holder_id",
            ]
        ),
        "example.py",
    )

    assert offenders == [
        "example.py:2: pool = services.global_proxy_pool",
        "example.py:4: holder = legacy.runtime_holder_id",
    ]


def test_architecture_guard_catches_import_aliases_from_imports_and_returned_state():
    offenders = _legacy_state_field_offenders(
        "\n".join(
            [
                "import javdb.spider.runtime.state as runtime_state",
                "from javdb.spider.runtime import state as spider_state",
                "from javdb.spider.runtime.state import global_proxy_pool",
                "",
                "def helper():",
                "    return spider_state",
                "",
                "def conditional_helper(runtime):",
                "    return runtime.services if runtime is not None else runtime_state",
                "",
                "services = helper()",
                "conditional_services = conditional_helper(None)",
                "pool = services.global_request_handler",
                "holder = runtime_state.runtime_holder_id",
                "coordinator = conditional_services.global_proxy_coordinator",
            ]
        ),
        "example.py",
    )

    assert offenders == [
        "example.py:3: from javdb.spider.runtime.state import global_proxy_pool",
        "example.py:6: return spider_state",
        "example.py:9: return runtime.services if runtime is not None else runtime_state",
        "example.py:13: pool = services.global_request_handler",
        "example.py:14: holder = runtime_state.runtime_holder_id",
        "example.py:15: coordinator = conditional_services.global_proxy_coordinator",
    ]


def test_architecture_guard_keeps_module_aliases_inside_functions():
    offenders = _legacy_state_field_offenders(
        "\n".join(
            [
                "import javdb.spider.runtime.state as runtime_state",
                "services = runtime_state",
                "",
                "def f():",
                "    return services.global_proxy_pool",
            ]
        ),
        "example.py",
    )

    assert offenders == [
        "example.py:5: return services.global_proxy_pool",
    ]


def test_architecture_guard_catches_full_import_chain_field_access():
    offenders = _legacy_state_field_offenders(
        "\n".join(
            [
                "import javdb.spider.runtime.state",
                "pool = javdb.spider.runtime.state.global_proxy_pool",
            ]
        ),
        "example.py",
    )

    assert offenders == [
        "example.py:2: pool = javdb.spider.runtime.state.global_proxy_pool",
    ]


def test_production_code_does_not_directly_use_legacy_state_fields():
    offenders: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            _legacy_state_field_offenders(
                text,
                path.relative_to(ROOT).as_posix(),
            )
        )

    assert offenders == []
