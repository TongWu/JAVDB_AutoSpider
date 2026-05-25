"""Unit tests for spider main backend selection helpers."""

from __future__ import annotations

from argparse import Namespace
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.spider.app.result import read_spider_result


def _base_args(**overrides):
    values = dict(
        dry_run=True,
        output_file="result-test.csv",
        start_page=2,
        end_page=4,
        all=False,
        ignore_history=True,
        use_history=False,
        url=None,
        phase="all",
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        from_pipeline=False,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=True,
        no_rclone_filter=True,
        disable_all_filters=True,
        enable_dedup=False,
        enable_redownload=None,
        redownload_threshold=None,
        result_json=None,
    )
    values.update(overrides)
    return Namespace(**values)


def _patch_lightweight_spider_run(monkeypatch, run_service, tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setenv("JAVDB_FORBID_DB_WRITES", "1")
    monkeypatch.setattr(run_service, "REPORTS_DIR", str(reports_dir))
    monkeypatch.setattr(run_service, "DAILY_REPORT_DIR", str(tmp_path / "daily"))
    monkeypatch.setattr(run_service, "AD_HOC_DIR", str(tmp_path / "adhoc"))
    monkeypatch.setattr(run_service, "DEDUP_DIR", str(tmp_path / "dedup"))
    monkeypatch.setattr(run_service.state, "setup_proxy_pool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_service.state, "initialize_request_handler", lambda: None)
    monkeypatch.setattr(
        run_service.state,
        "ensure_report_dated_dir",
        lambda base: str(tmp_path / os.path.basename(base)),
    )
    monkeypatch.setattr(run_service.state, "ensure_reports_dir", lambda: None)
    monkeypatch.setattr(run_service, "validate_history_file", lambda _path: True)
    monkeypatch.setattr(run_service, "load_parsed_movies_history", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(run_service.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(
        run_service,
        "fetch_all_index_pages",
        lambda **kwargs: {
            "all_index_results_phase1": [{"href": "https://example.test/1"}],
            "all_index_results_phase2": [],
            "any_proxy_banned": False,
            "use_proxy": kwargs["use_proxy"],
            "use_cf_bypass": kwargs["use_cf_bypass"],
            "csv_path": kwargs["csv_path"],
            "last_valid_page": 2,
        },
    )
    monkeypatch.setattr(run_service, "load_rclone_inventory", lambda _path: {})
    monkeypatch.setattr(
        run_service,
        "create_detail_backend",
        lambda **_kwargs: object(),
    )

    def fake_process_detail_entries(**kwargs):
        if not kwargs["entries"]:
            return {
                "use_proxy": False,
                "use_cf_bypass": False,
                "rows": [],
                "skipped_history": 0,
                "failed": 0,
                "failed_movies": [],
                "no_new_torrents": 0,
            }
        return {
            "use_proxy": False,
            "use_cf_bypass": False,
            "rows": [{"href": "https://example.test/1"}],
            "skipped_history": 2,
            "failed": 3,
            "failed_movies": ["ABC-001"],
            "no_new_torrents": 4,
        }

    monkeypatch.setattr(
        run_service,
        "process_detail_entries",
        fake_process_detail_entries,
    )
    monkeypatch.setattr(run_service.movie_sleep_mgr, "sleep", lambda: 0.0)
    monkeypatch.setattr(run_service, "generate_summary_report", lambda **_kwargs: None)
    monkeypatch.setattr(run_service, "has_git_credentials", lambda *_args: False)


def test_spider_main_writes_result_json_on_success(tmp_path, monkeypatch):
    import javdb.spider.app.run_service as run_service

    result_path = tmp_path / "spider-result.json"
    monkeypatch.setattr(
        run_service,
        "parse_arguments",
        lambda: _base_args(result_json=str(result_path)),
    )
    _patch_lightweight_spider_run(monkeypatch, run_service, tmp_path)

    run_service.main()

    result = read_spider_result(result_path)
    assert result.csv_path.endswith("result-test.csv")
    assert result.session_id is None
    assert result.dedup_csv_path is None
    assert result.stats.pages == "2-4"
    assert result.stats.found == 10
    assert result.stats.parsed == 1
    assert result.stats.skipped == 2
    assert result.stats.failed == 3
    assert result.stats.no_new == 4
    assert result.mode == "daily"
    assert result.url is None
    assert result.phase == "all"
    assert result.page_range == "2-4"
    assert result.exit_code == 0
    assert result.failure_reason is None


def test_spider_main_result_uses_actual_mode_from_url(tmp_path, monkeypatch):
    import javdb.spider.app.run_service as run_service

    result_path = tmp_path / "spider-result.json"
    monkeypatch.setattr(
        run_service,
        "parse_arguments",
        lambda: _base_args(
            result_json=str(result_path),
            url="https://javdb.com/actors/EvkJ",
        ),
    )
    _patch_lightweight_spider_run(monkeypatch, run_service, tmp_path)

    run_service.main()

    result = read_spider_result(result_path)
    assert result.mode == "adhoc"
    assert result.url == "https://javdb.com/actors/EvkJ"


def test_spider_main_writes_partial_result_json_on_failure(tmp_path, monkeypatch):
    import javdb.spider.app.run_service as run_service

    result_path = tmp_path / "spider-result.json"
    monkeypatch.setattr(
        run_service,
        "parse_arguments",
        lambda: _base_args(result_json=str(result_path), phase="1", all=True),
    )
    _patch_lightweight_spider_run(monkeypatch, run_service, tmp_path)
    monkeypatch.setattr(
        run_service,
        "fetch_all_index_pages",
        lambda **_kwargs: (_ for _ in ()).throw(SystemExit(2)),
    )

    with pytest.raises(SystemExit) as exc_info:
        run_service.main()

    assert exc_info.value.code == 2
    result = read_spider_result(result_path)
    assert result.csv_path.endswith("result-test.csv")
    assert result.session_id is None
    assert result.stats is None
    assert result.phase == "1"
    assert result.page_range == "2-*"
    assert result.exit_code == 2
    assert result.failure_reason == "exit code 2"


def test_create_detail_backend_selects_parallel(monkeypatch):
    import javdb.spider.app.main as spider_main
    import javdb.spider.app.run_service as run_service

    sentinel = object()
    calls = []

    # ``create_detail_backend`` lives in run_service (main.py re-exports it
    # after W3.5), so the builders it calls must be patched on the
    # canonical module — patching spider_main would have no effect.
    monkeypatch.setattr(
        run_service,
        'build_parallel_detail_backend',
        lambda **kwargs: calls.append(kwargs) or sentinel,
    )
    monkeypatch.setattr(
        run_service,
        'build_sequential_detail_backend',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('sequential builder should not be called')
        ),
    )

    backend = spider_main.create_detail_backend(
        use_parallel=True,
        use_cookie=True,
        is_adhoc_mode=False,
        session=object(),
        use_proxy=True,
        use_cf_bypass=False,
    )

    assert backend is sentinel
    assert calls == [
        {
            'use_cookie': True,
            'use_proxy': True,
            'use_cf_bypass': False,
        }
    ]


def test_create_detail_backend_selects_sequential(monkeypatch):
    import javdb.spider.app.main as spider_main
    import javdb.spider.app.run_service as run_service

    sentinel = object()
    session = object()
    calls = []

    # See parallel-case test above for why builders are patched on
    # run_service rather than spider_main (W3.5 re-export).
    monkeypatch.setattr(
        run_service,
        'build_parallel_detail_backend',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('parallel builder should not be called')
        ),
    )
    monkeypatch.setattr(
        run_service,
        'build_sequential_detail_backend',
        lambda *args, **kwargs: calls.append((args, kwargs)) or sentinel,
    )

    backend = spider_main.create_detail_backend(
        use_parallel=False,
        use_cookie=False,
        is_adhoc_mode=True,
        session=session,
        use_proxy=False,
        use_cf_bypass=True,
    )

    assert backend is sentinel
    assert calls == [
        (
            (session,),
            {
                'use_cookie': False,
                'is_adhoc_mode': True,
                'use_proxy': False,
                'use_cf_bypass': True,
            },
        )
    ]
