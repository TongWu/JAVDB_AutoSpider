"""Aggregate the last 24h of ``pending_session_verify`` records into a snapshot.

Phase 3 (Ingestion Perfect Rollback) email pipeline pre-step.  Reads
``reports/D1/d1_drift.jsonl``, isolates the records emitted by
:mod:`apps.cli.db.commit_session` and :mod:`apps.cli.db.rollback` over the
last ``--window-hours`` (default 24), and writes a small JSON file at
``reports/D1/pending_health_24h.json`` for
:mod:`javdb.integrations.notify.email` to render
the Health Snapshot section.

Stale-session cron resume signals (``stale_session_cleanup`` records
emitted by :mod:`apps.cli.db.cleanup_stale_in_progress`) are folded in
when present so the Snapshot can also report
``stale_resume_successes`` / ``stale_resume_failures``.

Exit codes
----------
* 0 — snapshot written (or no input: snapshot still written with zeroed
  fields so the email rendering branch always finds the file).
* 1 — could not read the input jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from javdb.storage.drift_io import read_jsonl
from javdb.storage.sessions.pending_verify import (
    F_CLEANUP_PATH_MISMATCH_COUNT,
    F_COMMIT_ATTEMPTS,
    F_COMMIT_DURATION_MS,
    F_DERIVED_RECOMPUTE_DRIFT,
    F_FINAL_STATUS,
    F_HREFS_PROCESSED,
    F_KIND,
    F_ROLLBACK_MODE,
    F_STATS_READ_ERROR,
    F_TS,
    F_WORKER_STAGE_ROLLBACK_FAILED,
    KIND_PENDING_SESSION_VERIFY,
)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.aggregate_pending_health",
        description=(
            "Aggregate the last N hours of pending_session_verify "
            "records from d1_drift.jsonl into a Health Snapshot json."
        ),
    )
    p.add_argument(
        "--input",
        default=None,
        help=(
            "Path to d1_drift.jsonl. Defaults to "
            "$REPORTS_DIR/D1/d1_drift.jsonl."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        help=(
            "Path to write the Health Snapshot json. Defaults to "
            "$REPORTS_DIR/D1/pending_health_24h.json."
        ),
    )
    p.add_argument(
        "--window-hours",
        type=float,
        default=24.0,
        help="Look-back window in hours (default 24).",
    )
    return p.parse_args(argv)


def _parse_ts(raw) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw)
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _percentile(values, pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def aggregate(records: Iterable[dict], window_hours: float) -> dict:
    """Build the Health Snapshot dict from *records* within the window."""
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    pending_session_records = []
    stale_records = []
    for rec in records:
        ts = _parse_ts(rec.get(F_TS))
        if ts is None or ts < window_start:
            continue
        kind = rec.get(F_KIND)
        if kind == KIND_PENDING_SESSION_VERIFY:
            pending_session_records.append(rec)
        elif kind == "stale_session_cleanup":
            stale_records.append(rec)

    pending_session_count = len(pending_session_records)
    successful_committed_count = sum(
        1 for r in pending_session_records
        if r.get(F_FINAL_STATUS) == "committed"
    )
    rolled_back_count = sum(
        1 for r in pending_session_records
        if r.get(F_ROLLBACK_MODE) == "rollback_pending"
        or r.get(F_FINAL_STATUS) == "failed"
    )

    # P1: split rolled-back sessions by failure_class so the Phase 3
    # critical-pending alert can distinguish operational failures
    # (D1 dual-write asymmetry, dry-run timeout, runner SIGTERM) from
    # spider crashes (HTML schema drift, login expiry, proxy ban). The
    # legacy aggregator lumped them all together which made automatic
    # fallback decisions noisier than necessary. Records that predate
    # this field are bucketed as ``unknown`` so old runs still summarise.
    failure_class_counts: Dict[str, int] = {}
    for r in pending_session_records:
        if (
            r.get(F_ROLLBACK_MODE) == "rollback_pending"
            or r.get(F_FINAL_STATUS) == "failed"
        ):
            cls = (r.get("failure_class") or "unknown").strip() or "unknown"
            failure_class_counts[cls] = failure_class_counts.get(cls, 0) + 1
    success_rate_percent: Optional[float]
    if pending_session_count > 0:
        success_rate_percent = (
            successful_committed_count / pending_session_count * 100.0
        )
    else:
        success_rate_percent = None

    durations = [
        int(r.get(F_COMMIT_DURATION_MS) or 0)
        for r in pending_session_records
        if r.get(F_COMMIT_DURATION_MS)
    ]
    avg_commit_duration_ms = (
        int(sum(durations) / len(durations)) if durations else 0
    )
    # Per-movie ms is not measured directly; approximate via
    # commit_duration_ms / max(hrefs_processed, 1) for sessions where
    # both fields are populated.
    per_movie_durations = []
    for r in pending_session_records:
        d = r.get(F_COMMIT_DURATION_MS)
        h = r.get(F_HREFS_PROCESSED)
        try:
            d = int(d or 0)
            h = int(h or 0)
        except (TypeError, ValueError):
            continue
        if d > 0 and h > 0:
            per_movie_durations.append(d / h)
    p95_per_movie_ms = int(_percentile(per_movie_durations, 95))
    total_commit_attempts = sum(
        int(r.get(F_COMMIT_ATTEMPTS) or 0)
        for r in pending_session_records
    )
    total_derived_recompute_drift = sum(
        int(r.get(F_DERIVED_RECOMPUTE_DRIFT) or 0)
        for r in pending_session_records
    )
    total_worker_stage_rollback_failed = sum(
        int(r.get(F_WORKER_STAGE_ROLLBACK_FAILED) or 0)
        for r in pending_session_records
    )
    total_stats_read_error = sum(
        1 for r in pending_session_records
        if r.get(F_STATS_READ_ERROR)
    )
    total_cleanup_path_mismatch_count = sum(
        int(r.get(F_CLEANUP_PATH_MISMATCH_COUNT) or 0)
        for r in pending_session_records
    )
    stale_resume_successes = sum(
        int(r.get("stale_resume_successes") or 0)
        for r in stale_records
    )
    stale_resume_failures = sum(
        int(r.get("stale_resume_failures") or 0)
        for r in stale_records
    )

    return {
        "kind": "pending_health_snapshot",
        "generated_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        "pending_session_count": pending_session_count,
        "successful_committed_count": successful_committed_count,
        "rolled_back_count": rolled_back_count,
        "success_rate_percent": success_rate_percent,
        "avg_commit_duration_ms": avg_commit_duration_ms,
        "p95_per_movie_ms": p95_per_movie_ms,
        "total_commit_attempts": total_commit_attempts,
        "total_derived_recompute_drift": total_derived_recompute_drift,
        "total_worker_stage_rollback_failed": (
            total_worker_stage_rollback_failed
        ),
        "total_stats_read_error": total_stats_read_error,
        "total_cleanup_path_mismatch_count": (
            total_cleanup_path_mismatch_count
        ),
        "stale_resume_successes": stale_resume_successes,
        "stale_resume_failures": stale_resume_failures,
        "rolled_back_by_failure_class": failure_class_counts,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    input_path = args.input or os.path.join(
        reports_dir, "D1", "d1_drift.jsonl",
    )
    output_path = args.output or os.path.join(
        reports_dir, "D1", "pending_health_24h.json",
    )
    try:
        records = list(read_jsonl(input_path))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to read {input_path}: {exc}", file=sys.stderr)
        return 1

    snapshot = aggregate(records, args.window_hours)
    Path(os.path.dirname(output_path) or ".").mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(
        f"Wrote Health Snapshot ({len(records)} records inspected, "
        f"window={args.window_hours}h) → {output_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
