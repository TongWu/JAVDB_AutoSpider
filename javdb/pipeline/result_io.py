from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from javdb.pipeline.models import PipelineRunResult, StepResult
from javdb.spider.app.result import read_spider_result, write_spider_result_atomic
from javdb.spider.app.result import _atomic_write_json as _write_json_atomic

PIPELINE_RESULT_SCHEMA_VERSION = "1.0"
PIPELINE_RESULT_KIND = "pipeline_run_result"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pipeline_payload(result: PipelineRunResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["schema_version"] = PIPELINE_RESULT_SCHEMA_VERSION
    payload["kind"] = PIPELINE_RESULT_KIND
    payload["generated_at"] = utc_now_iso()
    return payload


def write_pipeline_result_atomic(path: str | Path, result: PipelineRunResult) -> None:
    _write_json_atomic(Path(path), _pipeline_payload(result))


def read_pipeline_result(path: str | Path) -> PipelineRunResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema_version") != PIPELINE_RESULT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported pipeline result schema_version: {raw.get('schema_version')!r}")
    if raw.get("kind") != PIPELINE_RESULT_KIND:
        raise ValueError(f"Expected pipeline_run_result, got {raw.get('kind')!r}")
    required = {
        "status",
        "mode",
        "url",
        "started_at",
        "finished_at",
        "exit_code",
        "failure_reason",
        "spider_result",
        "steps",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Missing pipeline result field(s): {', '.join(missing)}")
    steps = [StepResult(**item) for item in raw["steps"]]
    return PipelineRunResult(
        status=raw["status"],
        mode=raw["mode"],
        url=raw["url"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        exit_code=int(raw["exit_code"]),
        failure_reason=raw["failure_reason"],
        spider_result=raw["spider_result"],
        steps=steps,
    )


__all__ = [
    "PIPELINE_RESULT_KIND",
    "PIPELINE_RESULT_SCHEMA_VERSION",
    "read_pipeline_result",
    "read_spider_result",
    "write_pipeline_result_atomic",
    "write_spider_result_atomic",
]
