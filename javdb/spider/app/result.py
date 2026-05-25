from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from javdb.infra.result_io import atomic_write_json

SPIDER_RESULT_SCHEMA_VERSION = "1.0"
SPIDER_RESULT_KIND = "spider_run_result"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SpiderRunStats:
    pages: str
    found: int
    parsed: int
    skipped: int
    failed: int
    no_new: int


@dataclass(frozen=True)
class SpiderRunResult:
    csv_path: str | None
    session_id: str | None
    dedup_csv_path: str | None
    stats: SpiderRunStats | None
    mode: Literal["daily", "adhoc"]
    url: str | None
    phase: Literal["1", "2", "all"]
    page_range: str | None
    started_at: str | None
    finished_at: str | None
    exit_code: int
    failure_reason: str | None
    schema_version: str = SPIDER_RESULT_SCHEMA_VERSION
    kind: str = SPIDER_RESULT_KIND
    generated_at: str = field(default_factory=utc_now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_spider_result_atomic(path: str | Path, result: SpiderRunResult) -> None:
    atomic_write_json(Path(path), result.to_json_dict())


def _require_string_or_none(raw: dict[str, Any], field_name: str) -> None:
    value = raw[field_name]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid spider result field {field_name}: expected string or null")


def _validate_spider_stats(stats_raw: Any) -> SpiderRunStats | None:
    if stats_raw is None:
        return None
    if not isinstance(stats_raw, dict):
        raise ValueError("Invalid spider result field stats: expected object or null")
    required = {"pages", "found", "parsed", "skipped", "failed", "no_new"}
    missing = sorted(required - stats_raw.keys())
    if missing:
        raise ValueError(f"Missing spider result stats field(s): {', '.join(missing)}")
    if not isinstance(stats_raw["pages"], str):
        raise ValueError("Invalid spider result stats.pages: expected string")
    for field_name in ("found", "parsed", "skipped", "failed", "no_new"):
        if not isinstance(stats_raw[field_name], int) or isinstance(stats_raw[field_name], bool):
            raise ValueError(f"Invalid spider result stats.{field_name}: expected int")
    return SpiderRunStats(
        pages=stats_raw["pages"],
        found=stats_raw["found"],
        parsed=stats_raw["parsed"],
        skipped=stats_raw["skipped"],
        failed=stats_raw["failed"],
        no_new=stats_raw["no_new"],
    )


def read_spider_result(path: str | Path) -> SpiderRunResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Spider result must be a JSON object")
    if raw.get("schema_version") != SPIDER_RESULT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported spider result schema_version: {raw.get('schema_version')!r}")
    if raw.get("kind") != SPIDER_RESULT_KIND:
        raise ValueError(f"Expected spider_run_result, got {raw.get('kind')!r}")
    required = {
        "csv_path",
        "session_id",
        "dedup_csv_path",
        "stats",
        "mode",
        "url",
        "phase",
        "page_range",
        "started_at",
        "finished_at",
        "exit_code",
        "failure_reason",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Missing spider result field(s): {', '.join(missing)}")
    if raw["mode"] not in ("daily", "adhoc"):
        raise ValueError(f"Invalid spider result mode: {raw['mode']!r}")
    if raw["phase"] not in ("1", "2", "all"):
        raise ValueError(f"Invalid spider result phase: {raw['phase']!r}")
    for field_name in (
        "csv_path",
        "session_id",
        "dedup_csv_path",
        "url",
        "page_range",
        "started_at",
        "finished_at",
        "failure_reason",
    ):
        _require_string_or_none(raw, field_name)
    if "generated_at" in raw and not isinstance(raw["generated_at"], str):
        raise ValueError("Invalid spider result field generated_at: expected string")
    if not isinstance(raw["exit_code"], int) or isinstance(raw["exit_code"], bool):
        raise ValueError("Invalid spider result field exit_code: expected int")
    stats = _validate_spider_stats(raw["stats"])
    return SpiderRunResult(
        csv_path=raw["csv_path"],
        session_id=raw["session_id"],
        dedup_csv_path=raw["dedup_csv_path"],
        stats=stats,
        mode=raw["mode"],
        url=raw["url"],
        phase=raw["phase"],
        page_range=raw["page_range"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        exit_code=raw["exit_code"],
        failure_reason=raw["failure_reason"],
        schema_version=raw["schema_version"],
        kind=raw["kind"],
        generated_at=raw.get("generated_at") or utc_now_iso(),
    )
