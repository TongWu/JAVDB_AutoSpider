from __future__ import annotations

from datetime import datetime, timezone

from apps.cli.db.pending_health import aggregate
from javdb.storage.sessions.pending_verify import (
    F_FINAL_STATUS,
    F_KIND,
    F_STATS_READ_ERROR,
    F_TS,
    KIND_PENDING_SESSION_VERIFY,
)


def test_aggregate_counts_pending_stats_read_errors():
    snapshot = aggregate(
        [
            {
                F_KIND: KIND_PENDING_SESSION_VERIFY,
                F_TS: datetime.now(timezone.utc).isoformat(),
                F_FINAL_STATUS: "committed",
                F_STATS_READ_ERROR: True,
            }
        ],
        window_hours=24,
    )

    assert snapshot["pending_session_count"] == 1
    assert snapshot["total_stats_read_error"] == 1
