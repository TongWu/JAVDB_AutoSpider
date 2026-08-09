"""Pure builder for ``pending_session_verify`` diagnostic JSONL records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

F_KIND = "kind"
F_TS = "ts"
F_SOURCE = "source"
F_SESSION_ID = "session_id"
F_WRITE_MODE = "write_mode"
F_FINAL_STATUS = "final_status"
F_ROLLBACK_MODE = "rollback_mode"
F_PENDING_STAGED_COUNT = "pending_staged_count"
F_PENDING_APPLIED_COUNT = "pending_applied_count"
F_PENDING_RESIDUAL_COUNT = "pending_residual_count"
F_COMMIT_ATTEMPTS = "commit_attempts"
F_COMMIT_DURATION_MS = "commit_duration_ms"
F_HREFS_PROCESSED = "hrefs_processed"
F_TORRENTS_UPSERTED = "torrents_upserted"
F_TORRENTS_DELETED = "torrents_deleted"
F_MOVIES_UPSERTED = "movies_upserted"
F_WORKER_STAGE_ROLLBACK_FAILED = "worker_stage_rollback_failed"
F_CLEANUP_PATH_MISMATCH_COUNT = "cleanup_path_mismatch_count"
F_STAGED_CLAIM_ORPHAN_COUNT = "staged_claim_orphan_count"
F_D1_REQUEST_COUNT_AUDIT_BASELINE_RATIO = (
    "d1_request_count_audit_baseline_ratio"
)
F_SHADOW_AUDIT_ENABLED = "shadow_audit_enabled"
F_DERIVED_RECOMPUTE_DRIFT = "derived_recompute_drift"
F_DERIVED_DRIFT_SAMPLES = "derived_drift_samples"
F_DERIVED_DRIFT_ERROR = "derived_drift_error"
F_ERROR = "error"
F_STATS_READ_ERROR = "stats_read_error"
F_RUN_ID = "run_id"
F_RUN_ATTEMPT = "run_attempt"

KIND_PENDING_SESSION_VERIFY = "pending_session_verify"


def _int_value(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_pending_verify_record(
    session_id: str,
    *,
    source: str,
    write_mode: str,
    final_status: Optional[str],
    drain: Optional[Mapping[str, Any]] = None,
    stats: Optional[Mapping[str, Any]] = None,
    commit_attempts: int = 0,
    commit_duration_ms: Optional[int] = None,
    shadow_audit_enabled: bool = False,
    shadow_audit_result: Optional[Mapping[str, Any]] = None,
    rollback_extras: Optional[Mapping[str, Any]] = None,
    worker_stage_rollback_failed: int = 0,
    stats_read_error: bool = False,
) -> Dict[str, Any]:
    """Build a ``pending_session_verify`` record without writing it.

    Callers own I/O, stats lookup, rollback final-status decisions, and
    shadow-audit computation. This function only assembles the shared schema.
    """
    drain = drain or {}
    stats = stats or {}
    pending_applied = _int_value(drain.get("pending_marked_applied"))
    pending_staged = pending_applied + _int_value(
        stats.get("pending_residual_count"),
    )
    if source == "rollback":
        pending_staged += _int_value(drain.get("PendingMovieHistoryWrites"))
        pending_staged += _int_value(drain.get("PendingTorrentHistoryWrites"))

    record: Dict[str, Any] = {
        F_KIND: KIND_PENDING_SESSION_VERIFY,
        F_TS: datetime.now(timezone.utc).isoformat(),
        F_SOURCE: source,
        F_SESSION_ID: session_id,
        F_WRITE_MODE: write_mode,
        F_FINAL_STATUS: final_status,
        F_PENDING_STAGED_COUNT: pending_staged,
        F_PENDING_APPLIED_COUNT: pending_applied,
        F_PENDING_RESIDUAL_COUNT: _int_value(
            stats.get("pending_residual_count"),
        ),
        F_COMMIT_ATTEMPTS: _int_value(commit_attempts),
        F_COMMIT_DURATION_MS: commit_duration_ms,
        F_HREFS_PROCESSED: _int_value(drain.get("hrefs_processed")),
        F_TORRENTS_UPSERTED: _int_value(drain.get("torrents_upserted")),
        F_TORRENTS_DELETED: _int_value(drain.get("torrents_deleted")),
        F_MOVIES_UPSERTED: _int_value(drain.get("movies_upserted")),
        F_WORKER_STAGE_ROLLBACK_FAILED: _int_value(
            worker_stage_rollback_failed,
        ),
        F_STATS_READ_ERROR: bool(stats_read_error),
        F_SHADOW_AUDIT_ENABLED: bool(shadow_audit_enabled),
    }

    if shadow_audit_result:
        record.update(dict(shadow_audit_result))
    else:
        record[F_DERIVED_RECOMPUTE_DRIFT] = 0
        record[F_DERIVED_DRIFT_SAMPLES] = []

    if rollback_extras:
        record.update(dict(rollback_extras))

    return record
