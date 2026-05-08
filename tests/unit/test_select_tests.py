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
    result = select("packages/python/javdb_ingestion/engine.py")

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
    result = select("packages/rust/javdb_rust_core/src/scraper/detail_parser.rs")

    assert result.run_rust is True
    assert result.build_rust_wheel is True
    assert result.run_fallback_tests is True
    assert result.rust_full is False
    assert result.rust_test_filters == ["scraper::detail_parser"]
    assert "tests/unit/test_api_parsers.py" in result.pytest_targets
    assert "tests/unit/test_rust_adapters_fallback.py" in result.pytest_targets


def test_cargo_manifest_change_runs_full_rust_tests():
    result = select("packages/rust/javdb_rust_core/Cargo.toml")

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
