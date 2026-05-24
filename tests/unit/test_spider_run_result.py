from __future__ import annotations

import json

import pytest

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
