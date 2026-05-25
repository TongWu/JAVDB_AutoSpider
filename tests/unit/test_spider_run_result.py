from __future__ import annotations

import json

import pytest

from javdb.spider.app.options import SpiderRunOptions
from javdb.spider.app import run_service
from javdb.spider.app.result import (
    SPIDER_RESULT_KIND,
    SPIDER_RESULT_SCHEMA_VERSION,
    SpiderRunResult,
    SpiderRunStats,
    read_spider_result,
    write_spider_result_atomic,
)


def _result(**overrides):
    payload = dict(
        csv_path="reports/DailyReport/2026/05/Javdb_Today.csv",
        session_id="20260520T010203Z-0001",
        dedup_csv_path=None,
        stats=SpiderRunStats(
            pages="1-10",
            found=42,
            parsed=12,
            skipped=3,
            failed=1,
            no_new=2,
        ),
        mode="daily",
        url=None,
        phase="all",
        page_range="1-10",
        started_at="2026-05-20T01:02:03Z",
        finished_at="2026-05-20T01:05:03Z",
        exit_code=0,
        failure_reason=None,
    )
    payload.update(overrides)
    return SpiderRunResult(**payload)


def test_write_and_read_spider_result_round_trip(tmp_path):
    path = tmp_path / "spider-result.json"

    write_spider_result_atomic(path, _result())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == SPIDER_RESULT_SCHEMA_VERSION
    assert raw["kind"] == SPIDER_RESULT_KIND
    loaded = read_spider_result(path)
    assert loaded.csv_path == "reports/DailyReport/2026/05/Javdb_Today.csv"
    assert loaded.stats.parsed == 12


def test_spider_result_tolerates_unknown_fields(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["new_field"] = "ignored"
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = read_spider_result(path)

    assert loaded.session_id == "20260520T010203Z-0001"


def test_spider_result_rejects_wrong_kind(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["kind"] = "pipeline_run_result"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="spider_run_result"):
        read_spider_result(path)


def test_spider_result_rejects_incompatible_schema_version(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = "2.0"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported spider result schema_version"):
        read_spider_result(path)


def test_spider_result_rejects_missing_required_field(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["mode"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Missing spider result field\\(s\\): mode"):
        read_spider_result(path)


def test_spider_result_rejects_non_object_payload(tmp_path):
    path = tmp_path / "spider-result.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(ValueError, match="Spider result must be a JSON object"):
        read_spider_result(path)


def test_spider_result_rejects_invalid_mode(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["mode"] = "weekly"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid spider result mode"):
        read_spider_result(path)


def test_spider_result_rejects_invalid_stats_shape(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["stats"] = {"pages": "1-10"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Missing spider result stats field"):
        read_spider_result(path)


def test_spider_result_rejects_invalid_exit_code(tmp_path):
    path = tmp_path / "spider-result.json"
    write_spider_result_atomic(path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["exit_code"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid spider result field exit_code"):
        read_spider_result(path)


def test_spider_partial_failure_result_preserves_unknowns_as_none(tmp_path):
    path = tmp_path / "spider-result.json"
    result = _result(
        csv_path=None,
        session_id="20260520T010203Z-0001",
        stats=None,
        exit_code=2,
        failure_reason="proxy ban detected",
    )

    write_spider_result_atomic(path, result)
    loaded = read_spider_result(path)

    assert loaded.csv_path is None
    assert loaded.stats is None
    assert loaded.exit_code == 2
    assert loaded.failure_reason == "proxy ban detected"


def test_run_spider_returns_result_and_writes_sidecar(tmp_path, monkeypatch):
    expected = _result(
        csv_path="reports/DailyReport/x.csv",
        session_id="s1",
        stats=SpiderRunStats(
            pages="1-1",
            found=1,
            parsed=1,
            skipped=0,
            failed=0,
            no_new=0,
        ),
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:01:00Z",
    )
    options = SpiderRunOptions(
        mode="daily",
        url=None,
        start_page=1,
        end_page=1,
        parse_all=False,
        ignore_history=False,
        phase="all",
        output_file="Javdb_Test.csv",
        dry_run=True,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        enable_dedup=False,
        enable_redownload=False,
        redownload_threshold=None,
        result_json=str(tmp_path / "spider-result.json"),
    )
    monkeypatch.setattr(run_service, "_run_spider_impl", lambda received: expected)

    result = run_service.run_spider(options)

    assert result is expected
    loaded = read_spider_result(options.result_json)
    assert loaded.session_id == "s1"
    assert loaded.csv_path == "reports/DailyReport/x.csv"


def test_run_spider_clears_db_context(monkeypatch):
    import javdb.storage.db as db_module

    expected = _result(
        csv_path="reports/DailyReport/x.csv",
        session_id="s1",
        stats=SpiderRunStats(
            pages="1-1",
            found=1,
            parsed=1,
            skipped=0,
            failed=0,
            no_new=0,
        ),
    )
    options = SpiderRunOptions(
        mode="daily",
        url=None,
        start_page=1,
        end_page=1,
        parse_all=False,
        ignore_history=False,
        use_history=False,
        phase="all",
        output_file="Javdb_Test.csv",
        dry_run=True,
        ignore_release_date=False,
        use_proxy=False,
        no_proxy=False,
        always_bypass_time=None,
        from_pipeline=False,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=False,
        no_rclone_filter=False,
        disable_all_filters=False,
        enable_dedup=False,
        enable_redownload=False,
        redownload_threshold=None,
        result_json=None,
    )
    monkeypatch.setattr(run_service, "_run_spider_impl", lambda received: expected)
    cleared = []

    monkeypatch.setattr(db_module, "set_active_write_mode", lambda value: cleared.append(value))
    monkeypatch.setattr(db_module, "set_active_session_id", lambda value: None)
    monkeypatch.setattr(db_module, "set_active_run_identity", lambda run_id, run_attempt: None)

    result = run_service.run_spider(options)

    assert result is expected
    assert cleared == [None]


def test_spider_result_can_represent_failure_without_stats(tmp_path):
    path = tmp_path / "failed-spider-result.json"
    result = SpiderRunResult(
        csv_path=None,
        session_id=None,
        dedup_csv_path=None,
        stats=None,
        mode="daily",
        url=None,
        phase="all",
        page_range=None,
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:00:10Z",
        exit_code=1,
        failure_reason="boom",
    )

    write_spider_result_atomic(path, result)
    loaded = read_spider_result(path)

    assert loaded.exit_code == 1
    assert loaded.failure_reason == "boom"
