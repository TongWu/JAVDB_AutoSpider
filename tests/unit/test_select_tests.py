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


def test_canonical_cli_change_uses_import_graph():
    """A change to one of the canonical apps/cli/ entrypoints should walk the
    import graph and surface the corresponding tests via reverse-deps, not
    just through IMPACT_RULES. ``apps/cli/rclone/manager.py`` (post-ADR-007
    canonical path) replaces the legacy ``scripts/rclone_manager.py`` probe."""
    result = select("apps/cli/rclone/manager.py")

    assert result.run_full_python is False
    assert "tests/unit/test_rclone_manager.py" in result.pytest_targets
    assert any("apps.cli.rclone.manager impacts" in reason for reason in result.reason)


def test_rust_scraper_change_runs_rust_wheel_fallback_and_parser_tests():
    result = select("javdb/rust_core/src/scraper/detail_parser.rs")

    assert result.run_rust is True
    assert result.build_rust_wheel is True
    assert result.run_fallback_tests is True
    assert result.rust_full is False
    assert result.rust_test_filters == ["scraper::detail_parser"]
    assert "tests/unit/test_api_parsers.py" in result.pytest_targets
    assert "tests/unit/test_rust_adapters_fallback.py" in result.pytest_targets


def test_canonical_parser_change_selects_parser_domain_and_rust_wheel():
    result = select("javdb/parsing/fallback/detail_parser.py")

    assert result.run_full_python is False
    assert result.run_selected_python is True
    assert result.build_rust_wheel is True
    assert "tests/unit/test_api_parsers.py" in result.pytest_targets
    assert "tests/unit/test_parser.py" in result.pytest_targets
    assert "tests/unit/test_video_code_search.py" in result.pytest_targets
    assert "tests/unit/test_fallback_shape.py" in result.pytest_targets
    assert any("parser-domain impact rule" in reason for reason in result.reason)


def test_proxy_change_builds_rust_wheel_for_rust_required_tests():
    # ADR-041: the proxy pool is Rust-Required; its tests import javdb.rust_core
    # unconditionally, so selecting them must trigger the wheel build (else CI
    # collection fails with ImportError in a no-wheel environment).
    result = select("javdb/proxy/pool.py")

    assert "tests/unit/test_proxy_pool.py" in result.pytest_targets
    assert result.build_rust_wheel is True


def test_infra_change_selecting_proxy_tests_builds_rust_wheel():
    # The proxy tests are also selected by infra changes (platform-config rule);
    # the wheel must still be built whenever those Rust-Required tests run.
    result = select("javdb/infra/request.py")

    if "tests/unit/test_proxy_pool.py" in result.pytest_targets:
        assert result.build_rust_wheel is True


def test_spider_runtime_change_selecting_sleep_coordinator_builds_rust_wheel():
    # test_sleep_with_coordinator imports RustProxyBanManager directly, so the
    # selective push workflow must install the cached Rust wheel whenever a
    # spider-runtime change selects that test file.
    result = select("javdb/spider/services/content_filter.py")

    assert "tests/unit/test_sleep_with_coordinator.py" in result.pytest_targets
    assert result.build_rust_wheel is True


def test_index_selection_change_selects_parser_domain_tests():
    result = select("javdb/pipeline/index_selection.py")

    assert result.run_full_python is False
    assert result.run_selected_python is True
    assert "tests/unit/test_parser.py" in result.pytest_targets
    assert "tests/unit/test_fallback_shape.py" in result.pytest_targets
    assert any("parser-domain impact rule" in reason for reason in result.reason)


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
    # The changed test file plus the always-run contract guards, which are
    # force-selected on every selective build.
    assert result.pytest_targets == [
        "tests/unit/test_gitattributes_union_merge.py",
        "tests/unit/test_parser.py",
        "tests/unit/test_query_contract_golden.py",
    ]


def test_query_contract_golden_always_runs_on_selective_builds():
    """ADR-018: the query-builder Contract Golden is force-selected on every
    selective build, even for an unrelated change — because a SQL-only edit to
    a covered builder is pruned from impact analysis (string-literal-only), so
    the guard cannot rely on impact selection to run."""
    # An unrelated source change that selects something specific (not full).
    result = select("javdb/pipeline/engine.py")
    assert result.run_full_python is False
    assert "tests/unit/test_query_contract_golden.py" in result.pytest_targets

    # And when the only change is a covered builder file, the guard is selected.
    builder = select("javdb/storage/repos/sessions_repo.py")
    assert (
        builder.run_full_python is True
        or "tests/unit/test_query_contract_golden.py" in builder.pytest_targets
    )


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


def test_git_diff_default_filter_includes_deletions(monkeypatch):
    """``run_git_diff`` must ask git for deleted paths too. Without ``D`` in
    --diff-filter, deletions never reach the selector at all: they count for
    nothing in SOURCE_CHANGE_LIMIT and cannot trip any conservative valve."""

    captured: dict[str, list[str]] = {}

    class _Completed:
        stdout = "javdb/storage/repos/gone_repo.py\njavdb/pipeline/engine.py\n"

    def fake_run(argv, **kwargs):  # noqa: ARG001
        captured["argv"] = argv
        return _Completed()

    monkeypatch.setattr(select_tests.subprocess, "run", fake_run)

    changed = select_tests.run_git_diff(base="base-sha", head="HEAD", repo_root=REPO_ROOT)

    diff_filter = next(
        arg.removeprefix("--diff-filter=")
        for arg in captured["argv"]
        if arg.startswith("--diff-filter=")
    )
    assert "D" in diff_filter, f"deletions are filtered out of git diff: {diff_filter}"
    assert changed == ["javdb/storage/repos/gone_repo.py", "javdb/pipeline/engine.py"]


def test_deleted_python_source_forces_full_python():
    """A deleted Python source (present in the diff, absent from the working
    tree) must escalate to a full run: the import graph is built from the
    post-deletion tree, so the tests that imported it have no edge to follow."""
    result = select("javdb/storage/repos/gone_repo.py")

    assert result.run_full_python is True
    assert result.pytest_targets == []
    assert any("deleted Python module(s)" in reason for reason in result.reason)


def test_deleted_test_helper_forces_full_python():
    """Test-tree helpers (non ``test_*.py`` modules under ``tests/``) are import
    graph nodes too — deleting one hides its consumers just as thoroughly."""
    result = select("tests/harness/gone_helper.py")

    assert result.run_full_python is True
    assert any("deleted Python module(s)" in reason for reason in result.reason)


def test_deleted_sources_count_toward_source_change_limit():
    """Deletions must be counted by ``source_change_count`` — the guard that
    forces a full run on large refactors. Asserted via the ``above limit``
    reason, which is independent of the deleted-module escalation."""
    changed = [
        f"javdb/storage/repos/gone_repo_{index}.py"
        for index in range(select_tests.SOURCE_CHANGE_LIMIT + 1)
    ]
    result = select(*changed)

    assert result.run_full_python is True
    assert any(
        f"{select_tests.SOURCE_CHANGE_LIMIT + 1} source files changed, above limit" in reason
        for reason in result.reason
    )


def test_deleted_test_file_is_not_passed_to_pytest():
    """A deleted test file must never reach the pytest command line (it would
    fail collection with ``file or directory not found``), and it must not
    escalate to a full run either — there is nothing left to execute."""
    result = select("tests/unit/test_gone_thing.py")

    assert result.run_full_python is False
    assert "tests/unit/test_gone_thing.py" not in result.pytest_targets
    assert result.pytest_targets == [
        "tests/unit/test_gitattributes_union_merge.py",
        "tests/unit/test_query_contract_golden.py",
    ]


def test_deleted_non_python_file_does_not_escalate():
    """A deleted doc gets the same treatment as a modified doc: no escalation,
    no extra selection."""
    result = select("docs/handbook/en/ops/gone-page.md")

    assert result.run_full_python is False
    assert not any("deleted Python module(s)" in reason for reason in result.reason)


def test_mass_deletion_plus_leaf_edit_escalates(monkeypatch):
    """End-to-end regression: 24 deleted storage modules plus one edited leaf
    used to yield ``run_full_python=False`` with 2 of ~400 test files selected,
    because git diff never reported the deletions."""

    deleted = [f"javdb/storage/repos/gone_repo_{index}.py" for index in range(24)]

    class _Completed:
        stdout = "\n".join(deleted + ["javdb/pipeline/engine.py"]) + "\n"

    monkeypatch.setattr(select_tests.subprocess, "run", lambda argv, **kwargs: _Completed())
    monkeypatch.setattr(select_tests, "is_docstring_only_change", lambda *args: False)

    selection = select_tests.selection_from_git(
        repo_root=REPO_ROOT,
        event_name="pull_request",
        ref_name="feature-branch",
        base="base-sha",
        head="HEAD",
    )

    assert selection.run_full_python is True
    assert selection.pytest_targets == []
    assert set(deleted).issubset(set(selection.changed_files))


def test_docstring_only_filter_disabled_without_base():
    """No `base` arg (e.g. CLI explicit `--changed-file` mode) leaves the
    classification step a no-op so behaviour matches pre-feature."""
    result = select_tests.select_for_changed_files(
        ["javdb/storage/file.py"],
        repo_root=REPO_ROOT,
        # no base= arg
    )
    assert result.docstring_only_files == []
