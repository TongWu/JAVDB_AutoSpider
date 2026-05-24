from __future__ import annotations

import json

import pytest

from javdb.pipeline.models import PipelineRunResult, StepResult
from javdb.pipeline.result_io import (
    PIPELINE_RESULT_KIND,
    PIPELINE_RESULT_SCHEMA_VERSION,
    read_pipeline_result,
    write_pipeline_result_atomic,
)


def _step(name="spider", status="success"):
    return StepResult(
        name=name,
        status=status,
        required=True,
        run_on_failure=False,
        command=["python3", "-m", "apps.cli.spider"],
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:01:00Z",
        exit_code=0,
        failure_reason=None,
        result_path="reports/tmp/spider-result.json",
    )


def _pipeline_result():
    return PipelineRunResult(
        status="success",
        mode="daily",
        url=None,
        started_at="2026-05-20T01:00:00Z",
        finished_at="2026-05-20T01:05:00Z",
        exit_code=0,
        failure_reason=None,
        spider_result={"csv_path": "reports/DailyReport/x.csv"},
        steps=[_step()],
    )


def test_write_and_read_pipeline_result_round_trip(tmp_path):
    path = tmp_path / "pipeline-result.json"

    write_pipeline_result_atomic(path, _pipeline_result())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == PIPELINE_RESULT_SCHEMA_VERSION
    assert raw["kind"] == PIPELINE_RESULT_KIND
    loaded = read_pipeline_result(path)
    assert loaded.status == "success"
    assert loaded.steps[0].name == "spider"


def test_pipeline_result_rejects_missing_required_field(tmp_path):
    path = tmp_path / "pipeline-result.json"
    write_pipeline_result_atomic(path, _pipeline_result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["steps"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="steps"):
        read_pipeline_result(path)


def test_pipeline_result_tolerates_unknown_fields(tmp_path):
    path = tmp_path / "pipeline-result.json"
    write_pipeline_result_atomic(path, _pipeline_result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["extra"] = {"ignored": True}
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = read_pipeline_result(path)

    assert loaded.status == "success"
