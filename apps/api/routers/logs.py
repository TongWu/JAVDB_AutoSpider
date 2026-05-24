"""Log search endpoint.

GET /api/logs/search — file grep over logs/jobs/*.log
"""

from __future__ import annotations

import json
from itertools import islice
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from apps.api.infra.auth import require_role
from apps.api.schemas.logs import LogSearchItem, LogSearchResponse
from apps.api.services import context

router = APIRouter(prefix="/api/logs", tags=["logs"])

_LOGS_DIR = context.RESOLVED_JOB_LOG_DIR
_HARD_CAP = 500
_MAX_META_SCAN = 200


@router.get("/search", response_model=LogSearchResponse)
def search_logs(
    q: str = Query(..., min_length=1, max_length=200),
    job_id: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_HARD_CAP),
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> LogSearchResponse:
    if not _LOGS_DIR.exists():
        return LogSearchResponse(results=[], total_matched=0, truncated=False, scanned_files=0)

    candidates = []
    scanned_files = 0
    for meta_path in islice(sorted(_LOGS_DIR.glob("*.meta.json"), reverse=True), _MAX_META_SCAN):
        scanned_files += 1
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            continue
        jid = meta.get("job_id", meta_path.stem.removesuffix(".meta"))
        if job_id and jid != job_id:
            continue
        created = meta.get("created_at", "")
        if date_from and created < date_from:
            continue
        if date_to and created > date_to + "T23:59:59Z":
            continue
        log_path = meta_path.with_suffix("").with_suffix(".log")
        if log_path.exists():
            candidates.append((jid, log_path, meta))

    results = []
    total = 0
    q_lower = q.lower()
    for jid, log_path, meta in candidates:
        with open(log_path, encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh, 1):
                line = line.rstrip("\n")
                if q_lower in line.lower():
                    total += 1
                    if len(results) < limit:
                        results.append(
                            LogSearchItem(
                                job_id=jid,
                                line_number=i,
                                text=line,
                                kind=meta.get("kind", ""),
                                created_at=meta.get("created_at", ""),
                            )
                        )

    return LogSearchResponse(
        results=results,
        total_matched=total,
        truncated=total > limit,
        scanned_files=scanned_files,
    )


__all__ = [
    "router",
]
