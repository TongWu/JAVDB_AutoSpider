from __future__ import annotations

from pathlib import Path

from scripts.ci import select_tests


REPO_ROOT = Path(__file__).resolve().parents[2]


def select(*changed_files: str, event_name: str = "", ref_name: str = "") -> select_tests.Selection:
    return select_tests.select_for_changed_files(
        changed_files,
        repo_root=REPO_ROOT,
        event_name=event_name,
        ref_name=ref_name,
    )


def test_ingestion_change_selects_ingestion_tests_without_full_run():
    result = select("javdb/pipeline/engine.py")

    assert result.run_full_python is False
    assert result.run_selected_python is True
    assert "tests/unit/test_ingestion_engine.py" in result.pytest_targets
    assert "tests/smoke/test_spider_detail_runner.py" in result.pytest_targets


def test_api_service_change_selects_api_and_gateway_tests():
    result = select("apps/api/services/explore_service.py")

    assert result.run_full_python is False
    assert "tests/unit/test_api_explore_security.py" in result.pytest_targets
    assert "tests/unit/test_api_explore_proxy_security.py" in result.pytest_targets
    assert "tests/integration/test_spider_gateway.py" in result.pytest_targets


def test_legacy_wrapper_change_uses_import_graph():
    result = select("scripts/rclone_manager.py")

    assert result.run_full_python is False
    assert "tests/unit/test_rclone_manager.py" in result.pytest_targets
    assert any("scripts.rclone_manager impacts" in reason for reason in result.reason)


def test_rust_scraper_change_runs_rust_wheel_fallback_and_parser_tests():
    result = select("javdb/rust_core/src/scraper/detail_parser.rs")

    assert result.run_rust is True
    assert result.build_rust_wheel is True
    assert result.run_fallback_tests is True
    assert result.rust_full is False
    assert result.rust_test_filters == ["scraper::detail_parser"]
    assert "tests/unit/test_api_parsers.py" in result.pytest_targets
    assert "tests/unit/test_rust_adapters_fallback.py" in result.pytest_targets


def test_cargo_manifest_change_runs_full_rust_tests():
    result = select("javdb/rust_core/Cargo.toml")

    assert result.run_rust is True
    assert result.rust_full is True
    assert result.build_rust_wheel is True


def test_high_risk_files_force_full_python():
    result = select("pytest.ini")

    assert result.run_full_python is True
    assert result.pytest_targets == []
    assert any("pytest.ini matches full-test guard" in reason for reason in result.reason)


def test_dot_directory_high_risk_path_keeps_leading_dot():
    result = select(".github/workflows/unit-tests.yml")

    assert result.run_full_python is True
    assert result.changed_files == [".github/workflows/unit-tests.yml"]
    assert any(".github/workflows/unit-tests.yml matches full-test guard" in reason for reason in result.reason)


def test_changed_test_file_selects_only_that_file():
    result = select("tests/unit/test_parser.py")

    assert result.run_full_python is False
    assert result.pytest_targets == ["tests/unit/test_parser.py"]


def test_large_source_diff_forces_full_python():
    changed = [f"packages/python/generated/module_{index}.py" for index in range(select_tests.SOURCE_CHANGE_LIMIT + 1)]
    result = select(*changed)

    assert result.run_full_python is True
    assert any("above limit" in reason for reason in result.reason)


def test_main_push_forces_full_python_guard():
    result = select("packages/python/javdb_ingestion/engine.py", event_name="push", ref_name="main")

    assert result.run_full_python is True
    assert result.build_rust_wheel is True
    assert any("push to main" in reason for reason in result.reason)


def test_ast_signature_ignores_string_literal_differences():
    """Docstring, prog= name, error message, SQL — all string Constants are
    collapsed to the same sentinel so the AST signature is stable across
    string-only edits."""
    a = '''
"""Old docstring."""

def f(x):
    """Old body docstring."""
    raise ValueError("old message")
'''
    b = '''
"""New docstring."""

def f(x):
    """New body docstring."""
    raise ValueError("new message")
'''
    assert select_tests._ast_signature(a) == select_tests._ast_signature(b)


def test_ast_signature_detects_real_code_change():
    """Adding a statement, changing an operator, or renaming an identifier
    must surface as different signatures."""
    a = "x = 1\n"
    b = "x = 2\n"
    assert select_tests._ast_signature(a) != select_tests._ast_signature(b)

    c = "def f(x):\n    return x + 1\n"
    d = "def f(x):\n    return x - 1\n"
    assert select_tests._ast_signature(c) != select_tests._ast_signature(d)


def test_docstring_only_changes_skip_impact_analysis(monkeypatch):
    """When a Python source change is classified as docstring-only, it must
    not contribute to source_change_count, not trigger import-graph reverse
    tracking, and not be matched against IMPACT_RULES. The file still shows
    up in `changed_files` and `docstring_only_files` for reporting."""

    docstring_only_paths = {
        f"javdb/storage/file_{i}.py" for i in range(select_tests.SOURCE_CHANGE_LIMIT + 5)
    }

    def fake_filter(path, base, repo_root):  # noqa: ARG001
        return path in docstring_only_paths

    monkeypatch.setattr(select_tests, "is_docstring_only_change", fake_filter)

    result = select_tests.select_for_changed_files(
        sorted(docstring_only_paths),
        repo_root=REPO_ROOT,
        base="fake-base-sha",
    )

    assert result.run_full_python is False, (
        "docstring-only files must not trip SOURCE_CHANGE_LIMIT"
    )
    assert set(result.docstring_only_files) == docstring_only_paths
    assert set(result.changed_files) == docstring_only_paths, (
        "filtered files still surface in changed_files for visibility"
    )
    assert any(
        "docstring/string-literal-only" in reason for reason in result.reason
    )


def test_docstring_only_filter_disabled_without_base():
    """No `base` arg (e.g. CLI explicit `--changed-file` mode) leaves the
    classification step a no-op so behaviour matches pre-feature."""
    result = select_tests.select_for_changed_files(
        ["javdb/storage/file.py"],
        repo_root=REPO_ROOT,
        # no base= arg
    )
    assert result.docstring_only_files == []
