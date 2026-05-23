"""Log search endpoint.

GET /api/logs/search — file grep over logs/jobs/*.log
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from apps.api.infra.auth import require_role
from apps.api.schemas.logs import LogSearchItem, LogSearchResponse

router = APIRouter(prefix="/api/logs", tags=["logs"])

_LOGS_DIR = Path("logs/jobs")
_HARD_CAP = 500


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
        return LogSearchResponse(results=[], total_matched=0, truncated=False)

    candidates = []
    for meta_path in sorted(_LOGS_DIR.glob("*.meta.json"), reverse=True):
        meta = json.loads(meta_path.read_text())
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
        for i, line in enumerate(log_path.read_text().splitlines(), 1):
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
    )


__all__ = [
    "router",
    "search_logs",
]
