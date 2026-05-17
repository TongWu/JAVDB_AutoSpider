#!/usr/bin/env python3
"""Select impacted tests for GitHub Actions.

The selector is intentionally dependency-free and static: it parses local Python
imports with ``ast`` instead of importing project modules, then supplements that
graph with conservative domain rules for cross-language and runtime behavior.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]

PYTHON_SOURCE_ROOTS = (
    "packages",
    "apps",
    "api",
    "scripts",
    "utils",
    "migration",
    "legacy",
)
TOP_LEVEL_PYTHON_FILES = ("pipeline.py", "compat.py")
TEST_ROOTS = ("tests",)
IGNORED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "target",
}
RUST_ROOT = "javdb/rust_core"
RUST_SRC_ROOT = f"{RUST_ROOT}/src"

SOURCE_CHANGE_LIMIT = 20
SELECTED_TEST_RATIO_LIMIT = 0.50

FORCE_FULL_GLOBS = (
    ".github/actions/install-rust-wheel/**",
    ".github/actions/setup-python-env/**",
    ".github/workflows/unit-tests.yml",
    "compat.py",
    "html/**",
    "pytest.ini",
    "requirements.txt",
    "scripts/ci/select_tests.py",
    "tests/conftest.py",
    "tests/**/conftest.py",
)

RUST_ADAPTER_GLOBS = (
    "packages/python/javdb_platform/bridges/rust_adapters/**",
    "utils/bridges/rust_adapters/**",
)
FALLBACK_TESTS = (
    "tests/unit/test_rust_adapters_fallback.py",
    "tests/unit/test_dedup_checker_rust_adapter.py",
)


@dataclass(frozen=True)
class ImpactRule:
    name: str
    changed: tuple[str, ...]
    tests: tuple[str, ...]


IMPACT_RULES = (
    ImpactRule(
        "api",
        (
            "apps/api/**",
            "api/**",
        ),
        (
            "tests/unit/test_api_*.py",
            "tests/unit/test_config_service.py",
            "tests/unit/test_video_code_search.py",
            "tests/integration/test_spider_gateway.py",
        ),
    ),
    ImpactRule(
        "parser-domain",
        (
            "api/parsers/**",
            "apps/api/parsers/**",
            "packages/python/javdb_core/contracts.py",
            "packages/python/javdb_core/filename_helper.py",
            "packages/python/javdb_core/magnet_extractor.py",
            "packages/python/javdb_core/parser.py",
            "packages/python/javdb_core/url_helper.py",
            "utils/domain/**",
            "utils/parser.py",
            f"{RUST_SRC_ROOT}/magnet_extractor.rs",
            f"{RUST_SRC_ROOT}/models.rs",
            f"{RUST_SRC_ROOT}/scraper/**",
            f"{RUST_SRC_ROOT}/url_helper.rs",
        ),
        (
            "tests/unit/test_api_parsers.py",
            "tests/unit/test_api_tag_parser.py",
            "tests/unit/test_api_tag_parser_security.py",
            "tests/unit/test_magnet_extractor.py",
            "tests/unit/test_parser.py",
            "tests/unit/test_video_code_search.py",
            "tests/integration/test_spider_gateway.py",
        ),
    ),
    ImpactRule(
        "db-d1-rollback",
        (
            "apps/cli/cleanup_stale_in_progress.py",
            "apps/cli/commit_session.py",
            "apps/cli/rollback.py",
            "migration/d1/**",
            "packages/python/javdb_platform/d1_client.py",
            "packages/python/javdb_platform/db.py",
            "packages/python/javdb_platform/db_layer/**",
            "packages/python/javdb_platform/dual_connection.py",
            "scripts/cleanup_stale_session_audits.py",
            "scripts/sync_d1_to_sqlite.py",
            "utils/infra/db.py",
        ),
        (
            "tests/unit/test_cleanup_stale_in_progress.py",
            "tests/unit/test_cleanup_stale_session_audits.py",
            "tests/unit/test_d1_dual.py",
            "tests/unit/test_db.py",
            "tests/unit/test_db_write_kill_switch.py",
            "tests/unit/test_reconcile_d1_drift.py",
            "tests/unit/test_rollback*.py",
            "tests/unit/test_sync_d1_to_sqlite.py",
            "tests/integration/test_align_inventory_with_moviehistory.py",
            "tests/integration/test_pipeline.py",
        ),
    ),
    ImpactRule(
        "rclone-qb-pikpak-integrations",
        (
            "apps/cli/pikpak_bridge.py",
            "apps/cli/qb_file_filter.py",
            "apps/cli/qb_uploader.py",
            "apps/cli/rclone_manager.py",
            "packages/python/javdb_integrations/pikpak_bridge.py",
            "packages/python/javdb_integrations/qb_client.py",
            "packages/python/javdb_integrations/qb_file_filter.py",
            "packages/python/javdb_integrations/qb_uploader.py",
            "packages/python/javdb_integrations/rclone_helper.py",
            "packages/python/javdb_integrations/rclone_manager.py",
            "scripts/pikpak_bridge.py",
            "scripts/qb_file_filter.py",
            "scripts/qb_uploader.py",
            "scripts/rclone_*.py",
            "scripts/rclone_manager.py",
            "utils/rclone_helper.py",
            f"{RUST_SRC_ROOT}/dedup_ops.rs",
            f"{RUST_SRC_ROOT}/rclone_ops.rs",
        ),
        (
            "tests/unit/test_dedup_checker.py",
            "tests/unit/test_dedup_checker_rust_adapter.py",
            "tests/unit/test_pikpak_bridge.py",
            "tests/unit/test_qb_*.py",
            "tests/unit/test_rclone_*.py",
            "tests/unit/test_rclone_manager.py",
            "tests/integration/test_align_inventory_with_moviehistory.py",
        ),
    ),
    ImpactRule(
        "spider-runtime",
        (
            "apps/cli/spider.py",
            "packages/python/javdb_spider/**",
            "scripts/spider/**",
        ),
        (
            "tests/unit/test_detail_runner_movie_claim.py",
            "tests/unit/test_index_parallel.py",
            "tests/unit/test_legacy_spider_wrapper.py",
            "tests/unit/test_login.py",
            "tests/unit/test_login_coordinator_park.py",
            "tests/unit/test_movie_claim_auto_toggle.py",
            "tests/unit/test_parallel_login.py",
            "tests/unit/test_runner_heartbeat_dynamic_interval.py",
            "tests/unit/test_setup_movie_claim_client.py",
            "tests/unit/test_sleep*.py",
            "tests/unit/test_spider_self_check.py",
            "tests/smoke/test_spider*.py",
            "tests/integration/test_spider_integration.py",
        ),
    ),
    ImpactRule(
        "ingestion",
        (
            "javdb/pipeline/**",
            "packages/python/javdb_ingestion/**",
            "scripts/ingestion/**",
        ),
        (
            "tests/unit/test_ingestion_engine.py",
            "tests/smoke/test_spider_detail_runner.py",
            "tests/integration/test_align_inventory_with_moviehistory.py",
        ),
    ),
    ImpactRule(
        "migration",
        (
            "apps/cli/migration.py",
            "migration/**",
            "packages/python/javdb_migrations/**",
        ),
        (
            "tests/unit/test_migrate_*.py",
            "tests/unit/test_reconcile_d1_drift.py",
            "tests/smoke/test_migrate_to_current.py",
            "tests/integration/test_align_inventory_with_moviehistory.py",
        ),
    ),
    ImpactRule(
        "platform-config-and-clients",
        (
            "config.py.example",
            "packages/python/javdb_platform/config_generator.py",
            "packages/python/javdb_platform/config_helper.py",
            "packages/python/javdb_platform/git_helper.py",
            "packages/python/javdb_platform/logging_config.py",
            "packages/python/javdb_platform/login_state_client.py",
            "packages/python/javdb_platform/movie_claim_client.py",
            "packages/python/javdb_platform/path_helper.py",
            "packages/python/javdb_platform/pipeline_service.py",
            "packages/python/javdb_platform/proxy_*.py",
            "packages/python/javdb_platform/qb_config.py",
            "packages/python/javdb_platform/request_handler.py",
            "packages/python/javdb_platform/runner_registry_client.py",
            "packages/python/javdb_platform/spider_gateway.py",
            "utils/infra/**",
            "utils/proxy_ban_manager.py",
            "utils/spider_gateway.py",
            f"{RUST_SRC_ROOT}/proxy/**",
            f"{RUST_SRC_ROOT}/requester/**",
        ),
        (
            "tests/unit/test_config_*.py",
            "tests/unit/test_git_helper.py",
            "tests/unit/test_logging_config.py",
            "tests/unit/test_login_state_client.py",
            "tests/unit/test_movie_claim_client.py",
            "tests/unit/test_path_helper.py",
            "tests/unit/test_pipeline_service.py",
            "tests/unit/test_proxy_*.py",
            "tests/unit/test_qb_config_security.py",
            "tests/unit/test_request_handler.py",
            "tests/unit/test_runner_registry_client.py",
            "tests/unit/test_setup_runner_registry_client.py",
            "tests/integration/test_spider_gateway.py",
        ),
    ),
    ImpactRule(
        "rust-adapters",
        (
            "packages/python/javdb_platform/bridges/rust_adapters/**",
            "utils/bridges/**",
            f"{RUST_SRC_ROOT}/**",
        ),
        (
            "tests/unit/test_dedup_checker_rust_adapter.py",
            "tests/unit/test_rust_adapters_fallback.py",
            "tests/integration/test_spider_gateway.py",
        ),
    ),
    ImpactRule(
        "docker-and-entrypoints",
        (
            "docker/**",
            "pipeline.py",
            "apps/cli/**",
            "scripts/*.py",
        ),
        (
            "tests/unit/test_docker_legacy_copy.py",
            "tests/unit/test_legacy_spider_wrapper.py",
            "tests/unit/test_pipeline_service.py",
            "tests/smoke/*.py",
            "tests/integration/test_pipeline.py",
        ),
    ),
)


@dataclass
class ImportGraph:
    module_to_path: dict[str, Path]
    path_to_module: dict[Path, str]
    edges: dict[str, set[str]]
    reverse_edges: dict[str, set[str]]
    parse_errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Selection:
    changed_files: list[str]
    pytest_targets: list[str]
    run_full_python: bool
    run_rust: bool
    rust_full: bool
    rust_test_filters: list[str]
    build_rust_wheel: bool
    run_fallback_tests: bool
    reason: list[str]
    selected_count: int
    total_test_files: int

    @property
    def run_selected_python(self) -> bool:
        return bool(self.pytest_targets) and not self.run_full_python

    def as_dict(self) -> dict[str, object]:
        return {
            "changed_files": self.changed_files,
            "pytest_targets": self.pytest_targets,
            "pytest_targets_shell": " ".join(self.pytest_targets),
            "run_full_python": self.run_full_python,
            "run_selected_python": self.run_selected_python,
            "run_rust": self.run_rust,
            "rust_full": self.rust_full,
            "rust_test_filters": self.rust_test_filters,
            "rust_test_filters_shell": " ".join(self.rust_test_filters),
            "build_rust_wheel": self.build_rust_wheel,
            "run_fallback_tests": self.run_fallback_tests,
            "reason": self.reason,
            "selected_count": self.selected_count,
            "total_test_files": self.total_test_files,
        }


def relpath(path: Path, repo_root: Path = REPO_ROOT) -> str:
    return path.relative_to(repo_root).as_posix()


def normalize_changed_file(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts)


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def is_zero_sha(value: str | None) -> bool:
    return bool(value) and set(value) == {"0"}


def is_test_file(path: str) -> bool:
    name = Path(path).name
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def iter_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_SOURCE_ROOTS + TEST_ROOTS:
        base = repo_root / root
        if base.exists():
            files.extend(path for path in base.rglob("*.py") if not is_ignored(path.relative_to(repo_root)))
    for file_name in TOP_LEVEL_PYTHON_FILES:
        path = repo_root / file_name
        if path.exists():
            files.append(path)
    return sorted(set(files))


def iter_test_files(repo_root: Path) -> list[Path]:
    tests_root = repo_root / "tests"
    if not tests_root.exists():
        return []
    return sorted(path for path in tests_root.rglob("test_*.py") if not is_ignored(path.relative_to(repo_root)))


def module_name_for_path(path: Path, repo_root: Path) -> str:
    rel = path.relative_to(repo_root).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def parse_imports(path: Path) -> tuple[set[str], set[str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    alias_targets: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            for alias in node.names:
                imports.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "alias_module":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                alias_targets.add(node.args[1].value)
    return imports, alias_targets


def resolve_local_module(import_name: str, module_to_path: dict[str, Path]) -> str | None:
    parts = import_name.split(".")
    for index in range(len(parts), 0, -1):
        candidate = ".".join(parts[:index])
        if candidate in module_to_path:
            return candidate
    return None


def build_import_graph(repo_root: Path = REPO_ROOT) -> ImportGraph:
    python_files = iter_python_files(repo_root)
    module_to_path: dict[str, Path] = {}
    path_to_module: dict[Path, str] = {}

    for path in python_files:
        module = module_name_for_path(path, repo_root)
        module_to_path[module] = path
        path_to_module[path] = module

    edges: dict[str, set[str]] = defaultdict(set)
    parse_errors: dict[str, str] = {}
    for path in python_files:
        module = path_to_module[path]
        try:
            imports, alias_targets = parse_imports(path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            parse_errors[relpath(path, repo_root)] = str(exc)
            continue

        for import_name in imports | alias_targets:
            resolved = resolve_local_module(import_name, module_to_path)
            if resolved and resolved != module:
                edges[module].add(resolved)

    reverse_edges: dict[str, set[str]] = defaultdict(set)
    for module, dependencies in edges.items():
        for dependency in dependencies:
            reverse_edges[dependency].add(module)

    return ImportGraph(
        module_to_path=module_to_path,
        path_to_module=path_to_module,
        edges=dict(edges),
        reverse_edges=dict(reverse_edges),
        parse_errors=parse_errors,
    )


def test_paths_for_patterns(patterns: Iterable[str], test_files: Iterable[Path], repo_root: Path) -> set[str]:
    selected: set[str] = set()
    relative_tests = {relpath(path, repo_root): path for path in test_files}
    for pattern in patterns:
        for relative_path in relative_tests:
            if fnmatch.fnmatch(relative_path, pattern):
                selected.add(relative_path)
    return selected


def reachable_test_files(module: str, graph: ImportGraph, repo_root: Path) -> set[str]:
    seen: set[str] = set()
    stack = list(graph.reverse_edges.get(module, ()))
    tests: set[str] = set()

    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        path = graph.module_to_path.get(current)
        if path is not None:
            relative_path = relpath(path, repo_root)
            if is_test_file(relative_path):
                tests.add(relative_path)

        stack.extend(graph.reverse_edges.get(current, ()))

    return tests


def module_for_changed_path(path: str, graph: ImportGraph, repo_root: Path) -> str | None:
    absolute_path = repo_root / path
    return graph.path_to_module.get(absolute_path)


def rust_filter_for_path(path: str) -> str | None:
    if not path.startswith(f"{RUST_SRC_ROOT}/") or not path.endswith(".rs"):
        return None
    relative = path.removeprefix(f"{RUST_SRC_ROOT}/").removesuffix(".rs")
    if relative == "lib":
        return None
    parts = relative.split("/")
    if parts[-1] == "mod":
        parts = parts[:-1]
    return "::".join(part for part in parts if part)


def is_python_source_change(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    if path.startswith("tests/"):
        return False
    return (
        any(path.startswith(f"{root}/") for root in PYTHON_SOURCE_ROOTS)
        or path in TOP_LEVEL_PYTHON_FILES
    )


def is_rust_source_change(path: str) -> bool:
    return path.startswith(f"{RUST_ROOT}/") and (path.endswith(".rs") or Path(path).name in {"Cargo.toml", "Cargo.lock"})


def select_for_changed_files(
    changed_files: Iterable[str],
    repo_root: Path = REPO_ROOT,
    event_name: str = "",
    ref_name: str = "",
) -> Selection:
    changed = sorted({normalize_changed_file(path) for path in changed_files if normalize_changed_file(path)})
    test_files = iter_test_files(repo_root)
    total_test_files = len(test_files)
    selected_tests: set[str] = set()
    reason: list[str] = []
    run_full_python = False

    if event_name in {"schedule", "workflow_dispatch"}:
        run_full_python = True
        reason.append(f"{event_name or 'manual'} run uses full Python tests")

    if event_name == "push" and ref_name in {"main", "master"}:
        run_full_python = True
        reason.append(f"push to {ref_name} uses full Python tests")

    if not changed and not run_full_python:
        run_full_python = True
        reason.append("no changed files were detected")

    source_change_count = sum(1 for path in changed if is_python_source_change(path) or is_rust_source_change(path))
    if source_change_count > SOURCE_CHANGE_LIMIT:
        run_full_python = True
        reason.append(f"{source_change_count} source files changed, above limit {SOURCE_CHANGE_LIMIT}")

    for path in changed:
        if matches_any(path, FORCE_FULL_GLOBS):
            run_full_python = True
            reason.append(f"{path} matches full-test guard")
        elif is_test_file(path):
            selected_tests.add(path)
            reason.append(f"{path} changed directly")

    graph = build_import_graph(repo_root)
    changed_python_modules = {
        module_for_changed_path(path, graph, repo_root)
        for path in changed
        if path.endswith(".py")
    }
    changed_python_modules.discard(None)

    if graph.parse_errors:
        changed_parse_errors = sorted(path for path in changed if path in graph.parse_errors)
        if changed_parse_errors:
            run_full_python = True
            reason.append(f"import graph parse failed for changed files: {', '.join(changed_parse_errors)}")

    for module in sorted(changed_python_modules):
        impacted = reachable_test_files(module, graph, repo_root)
        if impacted:
            selected_tests.update(impacted)
            reason.append(f"{module} impacts {len(impacted)} test file(s)")

    for rule in IMPACT_RULES:
        if any(matches_any(path, rule.changed) for path in changed):
            tests = test_paths_for_patterns(rule.tests, test_files, repo_root)
            if tests:
                selected_tests.update(tests)
                reason.append(f"{rule.name} impact rule added {len(tests)} test file(s)")

    run_rust = any(is_rust_source_change(path) for path in changed)
    rust_full = any(
        path in {
            f"{RUST_ROOT}/Cargo.lock",
            f"{RUST_ROOT}/Cargo.toml",
            f"{RUST_SRC_ROOT}/lib.rs",
        }
        for path in changed
    )
    rust_test_filters = sorted(
        filter_value
        for filter_value in {rust_filter_for_path(path) for path in changed}
        if filter_value
    )

    run_fallback_tests = (
        run_rust
        or any(matches_any(path, RUST_ADAPTER_GLOBS) for path in changed)
        or any(path in FALLBACK_TESTS for path in changed)
    )
    if run_fallback_tests:
        selected_tests.update(path for path in FALLBACK_TESTS if (repo_root / path).exists())
        reason.append("Rust/fallback paths changed")

    if total_test_files and len(selected_tests) > total_test_files * SELECTED_TEST_RATIO_LIMIT:
        run_full_python = True
        reason.append(
            f"{len(selected_tests)} selected test files exceed {SELECTED_TEST_RATIO_LIMIT:.0%} of {total_test_files}"
        )

    unknown_python_sources = [
        path
        for path in changed
        if is_python_source_change(path) and module_for_changed_path(path, graph, repo_root) is None
    ]
    if unknown_python_sources:
        run_full_python = True
        reason.append(f"unknown Python source path(s): {', '.join(unknown_python_sources)}")

    build_rust_wheel = run_rust or run_full_python or any(matches_any(path, RUST_ADAPTER_GLOBS) for path in changed)
    selected_targets = sorted(path for path in selected_tests if (repo_root / path).exists())

    if run_full_python:
        selected_targets = []

    return Selection(
        changed_files=changed,
        pytest_targets=selected_targets,
        run_full_python=run_full_python,
        run_rust=run_rust,
        rust_full=rust_full,
        rust_test_filters=rust_test_filters,
        build_rust_wheel=build_rust_wheel,
        run_fallback_tests=run_fallback_tests,
        reason=reason or ["no impacted tests matched"],
        selected_count=len(selected_tests),
        total_test_files=total_test_files,
    )


def run_git_diff(base: str, head: str, repo_root: Path, diff_filter: str = "ACMRTUXB") -> list[str]:
    if not base or is_zero_sha(base):
        raise RuntimeError("base revision is missing or is the zero SHA")
    if not head:
        head = "HEAD"
    diff_range = f"{base}...{head}"
    completed = subprocess.run(
        ["git", "diff", "--name-only", f"--diff-filter={diff_filter}", diff_range],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def selection_from_git(
    repo_root: Path,
    event_name: str,
    ref_name: str,
    base: str,
    head: str,
) -> Selection:
    if event_name in {"schedule", "workflow_dispatch"}:
        return select_for_changed_files([], repo_root=repo_root, event_name=event_name, ref_name=ref_name)

    try:
        changed_files = run_git_diff(base=base, head=head or "HEAD", repo_root=repo_root)
    except Exception as exc:  # pragma: no cover - exercised through CLI behavior in CI
        selection = select_for_changed_files([], repo_root=repo_root, event_name=event_name, ref_name=ref_name)
        selection.run_full_python = True
        selection.build_rust_wheel = True
        selection.reason.append(f"git diff failed, using full Python tests: {exc}")
        return selection

    return select_for_changed_files(changed_files, repo_root=repo_root, event_name=event_name, ref_name=ref_name)


def write_github_outputs(path: str, selection: Selection) -> None:
    if not path:
        return

    values = selection.as_dict()
    output_values = {
        "changed_files_json": json.dumps(values["changed_files"], sort_keys=True),
        "pytest_targets": values["pytest_targets_shell"],
        "pytest_targets_json": json.dumps(values["pytest_targets"], sort_keys=True),
        "run_full_python": str(values["run_full_python"]).lower(),
        "run_selected_python": str(values["run_selected_python"]).lower(),
        "run_rust": str(values["run_rust"]).lower(),
        "rust_full": str(values["rust_full"]).lower(),
        "rust_test_filters": values["rust_test_filters_shell"],
        "build_rust_wheel": str(values["build_rust_wheel"]).lower(),
        "run_fallback_tests": str(values["run_fallback_tests"]).lower(),
        "reason": "; ".join(selection.reason),
        "selected_count": str(values["selected_count"]),
        "total_test_files": str(values["total_test_files"]),
    }

    with open(path, "a", encoding="utf-8") as handle:
        for key, value in output_values.items():
            handle.write(f"{key}={value}\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--ref-name", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--changed-files-json", default="")
    parser.add_argument("--json-output", default="")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    return parser.parse_args(argv)


def changed_files_from_args(args: argparse.Namespace) -> list[str] | None:
    changed: list[str] = []
    changed.extend(args.changed_file)
    if args.changed_files_json:
        parsed = json.loads(args.changed_files_json)
        if not isinstance(parsed, list):
            raise ValueError("--changed-files-json must decode to a list")
        changed.extend(str(item) for item in parsed)
    return changed or None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()

    explicit_changed_files = changed_files_from_args(args)
    if explicit_changed_files is not None:
        selection = select_for_changed_files(
            explicit_changed_files,
            repo_root=repo_root,
            event_name=args.event_name,
            ref_name=args.ref_name,
        )
    else:
        selection = selection_from_git(
            repo_root=repo_root,
            event_name=args.event_name,
            ref_name=args.ref_name,
            base=args.base,
            head=args.head,
        )

    payload = selection.as_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_github_outputs(args.github_output, selection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
