"""POST /api/test/reset — truncate operational state for E2E tests.

Registered ONLY when TEST_MODE=1 at server boot. Otherwise the route does
not exist (returns 404).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/test", tags=["test-mode"])


def _reports_root() -> Path:
    return Path(os.getenv("REPORTS_DIR", "reports"))


_TRUNCATE_TARGETS = {
    "history.db": ["MovieHistory", "TorrentHistory"],
    "reports.db": ["ReportSessions", "ReportMovies", "ReportTorrents", "Stats"],
    "operations.db": ["RcloneInventory", "DedupRecords", "PikpakHistory", "system_state"],
}


@router.post("/reset")
def reset_state() -> dict[str, bool]:
    root = _reports_root()
    for db_name, tables in _TRUNCATE_TARGETS.items():
        db_path = root / db_name
        if not db_path.exists():
            continue
        with sqlite3.connect(str(db_path)) as conn:
            for table in tables:
                # Use TRY to avoid hard-failing on a table that doesn't
                # exist yet (e.g. system_state on a pre-migration DB).
                try:
                    conn.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
    return {"reset": True}
