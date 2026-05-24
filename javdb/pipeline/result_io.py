from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from javdb.infra.result_io import atomic_write_json
from javdb.pipeline.models import PipelineRunResult, StepResult
from javdb.spider.app.result import read_spider_result, write_spider_result_atomic

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
    atomic_write_json(Path(path), _pipeline_payload(result))


def _require_string_or_none(raw: dict[str, Any], field_name: str, context: str) -> None:
    value = raw[field_name]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid {context} field {field_name}: expected string or null")


def _require_string(raw: dict[str, Any], field_name: str, context: str) -> None:
    if not isinstance(raw[field_name], str):
        raise ValueError(f"Invalid {context} field {field_name}: expected string")


def _validate_step_result(raw: Any, index: int) -> StepResult:
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid pipeline result steps[{index}]: expected object")
    required = {
        "name",
        "status",
        "required",
        "run_on_failure",
        "command",
        "started_at",
        "finished_at",
        "exit_code",
        "failure_reason",
        "result_path",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Missing pipeline result steps[{index}] field(s): {', '.join(missing)}")
    if not isinstance(raw["name"], str):
        raise ValueError(f"Invalid pipeline result steps[{index}].name: expected string")
    if raw["status"] not in ("success", "failed", "timed_out", "skipped"):
        raise ValueError(f"Invalid pipeline result steps[{index}].status: {raw['status']!r}")
    for field_name in ("required", "run_on_failure"):
        if not isinstance(raw[field_name], bool):
            raise ValueError(f"Invalid pipeline result steps[{index}].{field_name}: expected bool")
    if not isinstance(raw["command"], list) or not all(
        isinstance(item, str) for item in raw["command"]
    ):
        raise ValueError(f"Invalid pipeline result steps[{index}].command: expected list of strings")
    for field_name in ("started_at", "finished_at"):
        _require_string(raw, field_name, f"pipeline result steps[{index}]")
    for field_name in ("failure_reason", "result_path"):
        _require_string_or_none(raw, field_name, f"pipeline result steps[{index}]")
    if raw["exit_code"] is not None and (
        not isinstance(raw["exit_code"], int) or isinstance(raw["exit_code"], bool)
    ):
        raise ValueError(f"Invalid pipeline result steps[{index}].exit_code: expected int or null")
    return StepResult(
        name=raw["name"],
        status=raw["status"],
        required=raw["required"],
        run_on_failure=raw["run_on_failure"],
        command=raw["command"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        exit_code=raw["exit_code"],
        failure_reason=raw["failure_reason"],
        result_path=raw["result_path"],
    )


def read_pipeline_result(path: str | Path) -> PipelineRunResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Pipeline result must be a JSON object")
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
    if raw["status"] not in ("success", "failed", "running"):
        raise ValueError(f"Invalid pipeline result status: {raw['status']!r}")
    _require_string(raw, "mode", "pipeline result")
    if raw["mode"] not in ("daily", "adhoc"):
        raise ValueError(f"Invalid pipeline result mode: {raw['mode']!r}")
    for field_name in ("started_at", "finished_at"):
        _require_string(raw, field_name, "pipeline result")
    for field_name in ("url", "failure_reason"):
        _require_string_or_none(raw, field_name, "pipeline result")
    if not isinstance(raw["exit_code"], int) or isinstance(raw["exit_code"], bool):
        raise ValueError("Invalid pipeline result field exit_code: expected int")
    if raw["spider_result"] is not None and not isinstance(raw["spider_result"], dict):
        raise ValueError("Invalid pipeline result field spider_result: expected object or null")
    if not isinstance(raw["steps"], list):
        raise ValueError("Invalid pipeline result field steps: expected list")
    steps = [_validate_step_result(item, index) for index, item in enumerate(raw["steps"])]
    return PipelineRunResult(
        status=raw["status"],
        mode=raw["mode"],
        url=raw["url"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        exit_code=raw["exit_code"],
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
