"""JSONL fallback persistence for ADR-026 diagnosis incidents."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path

from javdb.ops.diagnosis.models import OpsIncidentRecord


logger = logging.getLogger(__name__)


def default_incident_jsonl_path(reports_dir: str | None = None) -> Path:
    root = reports_dir or os.environ.get("REPORTS_DIR", "reports")
    return Path(root) / "ops" / "ops_incidents.jsonl"


def append_incident_jsonl(
    record: OpsIncidentRecord,
    path: str | Path | None = None,
) -> Path:
    target = Path(path) if path is not None else default_incident_jsonl_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":")) + "\n")
    return target


def read_incident_jsonl(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict] = []
    with target.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.debug("Skipping malformed ops incident JSONL line %s: %s", line_number, exc)
                continue
            if not isinstance(row, dict):
                logger.debug("Skipping non-object ops incident JSONL line %s", line_number)
                continue
            rows.append(row)
    return rows
