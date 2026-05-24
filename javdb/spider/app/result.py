from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_spider_result_atomic(path: str | Path, result: SpiderRunResult) -> None:
    _atomic_write_json(Path(path), result.to_json_dict())


def read_spider_result(path: str | Path) -> SpiderRunResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
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
    stats_raw = raw.get("stats")
    stats = SpiderRunStats(**stats_raw) if isinstance(stats_raw, dict) else None
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
        exit_code=int(raw["exit_code"]),
        failure_reason=raw["failure_reason"],
        schema_version=raw["schema_version"],
        kind=raw["kind"],
        generated_at=raw.get("generated_at") or utc_now_iso(),
    )
