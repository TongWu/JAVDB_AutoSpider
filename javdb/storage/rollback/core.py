"""Rollback library — core pipeline for DB rollback operations.

Public surface:
  :class:`RollbackRequest`  — input shape.
  :class:`RollbackPlan`     — dry-run output.
  :class:`RollbackResult`   — apply output.
  :func:`plan_rollback`     — resolve + dry-run.
  :func:`apply_rollback`    — resolve + apply.

The pipeline logic (``_drive_rollback``) lives here so the HTTP
endpoints and the CLI share the exact same code path.  The CLI module
(``apps.cli.db.rollback``) is a thin wrapper that adds argparse and
exit-code semantics.

Naming convention: every helper that ``_drive_rollback`` references
through its ``_self`` module-alias pattern is either defined here or
imported into this module's namespace at the top level.  This keeps
the monkeypatch surface in one place.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apps.cli.db._session_helpers import (
    append_jsonl_record,
    attach_run_identity,
    fanout_movie_claim,
    find_run_sessions,
    find_window_sessions,
    normalize_run_started_at,
    read_session_pre_state,
    write_github_output,
)
from javdb.storage.db import db as _db
from javdb.storage.db.db_connection import get_db
from javdb.storage.db.db_reports import db_pending_session_stats
from javdb.storage.db.db_rollback import db_rollback_session
from javdb.infra.logging import get_logger


logger = get_logger(__name__)

_CROSS_DAY_REJECT_HOURS = 1


# ── Public dataclasses ──────────────────────────────────────────────────


@dataclass
class RollbackRequest:
    """Inputs to :func:`plan_rollback` / :func:`apply_rollback`.

    Mirrors the CLI flags 1:1.  ``include_pending`` and
    ``restore_from_audit`` are HTTP-friendly aliases that map onto the
    underlying CLI semantics.
    """

    session_id: Optional[str] = None
    run_id: Optional[str] = None
    run_attempt: Optional[int] = None
    run_started_at: Optional[str] = None
    scope: str = "all"
    include_orphaned: bool = False
    failure_reason: Optional[str] = None
    dry_run: bool = True
    force: bool = False
    shard_date: Optional[str] = None
    no_claim_rollback: bool = False
    include_pending: bool = True
    restore_from_audit: bool = True
    claim_rollback_attempts: int = 3


@dataclass
class RollbackPlan:
    """Output of :func:`plan_rollback` (``dry_run=True``)."""

    session_id: str
    actions: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RollbackResult:
    """Output of :func:`apply_rollback` (``dry_run=False``)."""

    session_id: str
    applied: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


# ── Pipeline helpers (moved from apps.cli.db.rollback) ──────────────────


def _resolve_target_sessions(
    args: argparse.Namespace,
    run_started_at_normalized: Optional[str],
) -> List[int]:
    """Build the set of session ids to act on, honouring scope flags."""
    targets: set = set()
    if args.session_id is not None:
        targets.add(args.session_id)

    if args.run_id is not None:
        for sid in find_run_sessions(args.run_id, args.attempt):
            targets.add(sid)

    if args.run_started_at is not None:
        if args.include_orphaned or not targets:
            for sid in find_window_sessions(
                run_started_at_normalized, raise_on_error=True,
            ):
                targets.add(sid)

    return sorted(targets)


def _detect_cross_day(
    session_id: str,
    run_started_at_normalized: Optional[str],
) -> bool:
    """True if session's DateTimeCreated is far older than run start."""
    if not run_started_at_normalized:
        return False
    try:
        run_started_dt = datetime.strptime(
            run_started_at_normalized, "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return False
    cutoff = run_started_dt - timedelta(hours=_CROSS_DAY_REJECT_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    with get_db(_db.REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT DateTimeCreated FROM ReportSessions WHERE Id=?",
            (session_id,),
        ).fetchone()
    if not row:
        return False
    created = row["DateTimeCreated"]
    if not created:
        return False
    return created < cutoff_str


def _resolve_failure_reason(args: argparse.Namespace) -> Optional[str]:
    """Pick a FailureReason annotation from CLI / env."""
    if args.failure_reason:
        return args.failure_reason
    explicit = os.environ.get("ROLLBACK_REASON")
    if explicit:
        return explicit
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "workflow_dispatch":
        return "manual_dispatch"
    if event:
        return f"workflow_{event}"
    return None


def _emit_pending_verify_for_session(
    session_id: str,
    *,
    pre_status: Optional[str],
    pre_write_mode: Optional[str],
    counts: Optional[dict],
    duration_ms: Optional[int],
    error: Optional[str] = None,
    cleanup_path_mismatch: bool = False,
    worker_stage_rollback_failed: int = 0,
) -> None:
    """Emit one ``pending_session_verify`` JSONL line for a rollback session."""
    if pre_write_mode != "pending":
        return
    counts = counts or {}
    mode = counts.get("mode")
    if mode == "resume_commit":
        final_status = "committed" if not error else "finalizing"
    elif mode == "rollback_pending":
        final_status = "failed" if not error else pre_status or "finalizing"
    elif mode == "audit_replay":
        final_status = pre_status or "failed"
    else:
        final_status = pre_status or "in_progress"

    try:
        stats = db_pending_session_stats(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "db_pending_session_stats failed for session_id=%s: %s; "
            "emitting pending_session_verify with default counts.",
            session_id, exc,
        )
        stats = {}
    pending_applied = int(counts.get("pending_marked_applied", 0) or 0)
    pending_staged = (
        pending_applied
        + int(stats.get("pending_residual_count", 0) or 0)
        + int(counts.get("PendingMovieHistoryWrites", 0) or 0)
        + int(counts.get("PendingTorrentHistoryWrites", 0) or 0)
    )
    record = {
        "kind": "pending_session_verify",
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "rollback",
        "session_id": session_id,
        "write_mode": pre_write_mode,
        "final_status": final_status,
        "rollback_mode": mode,
        "pending_staged_count": pending_staged,
        "pending_applied_count": pending_applied,
        "pending_residual_count": int(
            stats.get("pending_residual_count", 0) or 0,
        ),
        "commit_attempts": 2 if mode == "resume_commit" else 0,
        "commit_duration_ms": duration_ms,
        "hrefs_processed": int(counts.get("hrefs_processed", 0) or 0),
        "torrents_upserted": int(counts.get("torrents_upserted", 0) or 0),
        "torrents_deleted": int(counts.get("torrents_deleted", 0) or 0),
        "movies_upserted": int(counts.get("movies_upserted", 0) or 0),
        "worker_stage_rollback_failed": int(worker_stage_rollback_failed),
        "cleanup_path_mismatch_count": 1 if cleanup_path_mismatch else 0,
        "shadow_audit_enabled": False,
        "derived_recompute_drift": 0,
        "derived_drift_samples": [],
    }
    if error is not None:
        record["error"] = error
    attach_run_identity(record, session_id)
    append_jsonl_record(record)


def _emit_metrics(summary: dict) -> None:
    """Append rollback metrics to reports/D1/d1_drift.jsonl + GITHUB_OUTPUT."""
    claim_rollbacks = summary.get("movie_claim_rollbacks", []) or []
    claim_failed = [
        c for c in claim_rollbacks if not c.get("ok", False)
    ]
    record = {
        "kind": "rollback_summary",
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": summary.get("dry_run"),
        "scope": summary.get("scope"),
        "run_id": summary.get("run_id"),
        "attempt": summary.get("attempt"),
        "drift_total": summary.get("drift_total", 0),
        "orphan_pruned_total": summary.get("orphan_pruned_total", 0),
        "refused_sessions": summary.get("refused_sessions", []),
        "failed_sessions": summary.get("failed_sessions", []),
        "session_ids": [
            s.get("session_id") for s in summary.get("sessions", [])
            if s.get("session_id") is not None
        ],
        "movie_claim_rollback_failures": claim_failed,
    }
    append_jsonl_record(record)

    write_github_output(
        drift_total=record["drift_total"],
        orphan_pruned_total=record["orphan_pruned_total"],
        session_count=len(record["session_ids"]),
    )


# ── Core pipeline ───────────────────────────────────────────────────────


def _drive_rollback(args: argparse.Namespace) -> tuple[dict, int]:
    """Run the planning + apply pipeline, return (summary, exit_code).

    Every name it references is looked up through this module's namespace
    so existing tests can monkeypatch any helper and see the patch take
    effect end-to-end.
    """
    import javdb.storage.rollback.core as _self

    run_started_at_normalized = _self.normalize_run_started_at(
        args.run_started_at,
    )
    failure_reason = _self._resolve_failure_reason(args)
    if args.run_started_at is not None and run_started_at_normalized is None:
        logger.error(
            "Invalid --run-started-at value %r; refusing rollback so the "
            "fallback window scan cannot expand to every in-progress session.",
            args.run_started_at,
        )
        return ({}, 2)

    try:
        sessions = _self._resolve_target_sessions(args, run_started_at_normalized)
    except Exception as e:
        logger.error("Failed to resolve target sessions: %s", e)
        return ({}, 3)

    if not sessions:
        logger.info(
            "No target sessions found — nothing to roll back. (This is the "
            "normal outcome when a run failed before the spider could "
            "create its session.)"
        )
        return ({"_no_targets": True}, 0)

    refused_sessions: List[int] = []
    if run_started_at_normalized and not args.force:
        valid_targets: List[int] = []
        for sid in sessions:
            if _self._detect_cross_day(sid, run_started_at_normalized):
                logger.error(
                    "Refusing session %s: DateTimeCreated predates "
                    "--run-started-at (%s) by more than %dh — looks like "
                    "a stale id from an unrelated run. Pass --force to "
                    "override.",
                    sid, run_started_at_normalized,
                    _CROSS_DAY_REJECT_HOURS,
                )
                refused_sessions.append(sid)
            else:
                valid_targets.append(sid)
        sessions = valid_targets

    if not sessions:
        summary = {
            "dry_run": args.dry_run,
            "scope": args.scope,
            "run_id": args.run_id,
            "attempt": args.attempt,
            "run_started_at": args.run_started_at,
            "sessions": [],
            "drift_total": 0,
            "orphan_pruned_total": 0,
            "failed_sessions": [],
            "refused_sessions": refused_sessions,
        }
        _self._emit_metrics(summary)
        return (summary, 2)

    logger.info(
        "Targeting %d session(s) for %s rollback: %s%s",
        len(sessions),
        "dry-run" if args.dry_run else "real",
        sessions,
        f" (refused {len(refused_sessions)} as stale)" if refused_sessions else "",
    )

    drift_total = 0
    orphan_pruned_total = 0
    failed_sessions: List[int] = []
    refused_committed_sessions: List[int] = []
    summaries: List[dict] = []
    for sid in sessions:
        pre_state = _self.read_session_pre_state(sid)
        pre_write_mode = pre_state.write_mode
        pre_status = pre_state.status
        rollback_started = time.monotonic()
        try:
            counts = _self.db_rollback_session(
                sid,
                dry_run=args.dry_run,
                scope=args.scope,
                force=args.force,
                run_started_at=run_started_at_normalized,
                failure_reason=failure_reason,
                auto_resume_finalizing=args.auto_resume_finalizing,
            )
        except ValueError as e:
            logger.error("Refused to roll back session %s: %s", sid, e)
            failed_sessions.append(sid)
            refused_committed_sessions.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})
            if pre_write_mode == "pending" and not args.dry_run:
                duration_ms = int((time.monotonic() - rollback_started) * 1000)
                _self._emit_pending_verify_for_session(
                    sid,
                    pre_status=pre_status,
                    pre_write_mode=pre_write_mode,
                    counts=None,
                    duration_ms=duration_ms,
                    error=str(e),
                )
            continue
        except Exception as e:
            logger.error("Rollback of session %s failed: %s", sid, e)
            failed_sessions.append(sid)
            summaries.append({"session_id": sid, "error": str(e)})
            if pre_write_mode == "pending" and not args.dry_run:
                duration_ms = int((time.monotonic() - rollback_started) * 1000)
                _self._emit_pending_verify_for_session(
                    sid,
                    pre_status=pre_status,
                    pre_write_mode=pre_write_mode,
                    counts=None,
                    duration_ms=duration_ms,
                    error=str(e),
                )
            continue
        summaries.append({"session_id": sid, "counts": counts})
        history_counts = counts.get("history", {})
        drift = history_counts.get("drift_skipped", 0)
        orphan = history_counts.get("orphan_pruned", 0)
        drift_total += int(drift or 0)
        orphan_pruned_total += int(orphan or 0)
        logger.info(
            "Session %s rollback %s: %s",
            sid,
            "(dry-run)" if args.dry_run else "applied",
            json.dumps(counts, ensure_ascii=False),
        )
        if pre_write_mode == "pending" and not args.dry_run:
            duration_ms = int((time.monotonic() - rollback_started) * 1000)
            _self._emit_pending_verify_for_session(
                sid,
                pre_status=pre_status,
                pre_write_mode=pre_write_mode,
                counts=history_counts,
                duration_ms=duration_ms,
            )

    claim_rollback_summaries: List[dict] = []
    if not args.dry_run and not args.no_claim_rollback:
        rollback_targets = [
            sid for sid in sessions
            if sid not in failed_sessions
        ]
        claim_rollback_summaries = _self.fanout_movie_claim(
            rollback_targets,
            operation="rollback",
            shard_date=args.shard_date,
            max_attempts=args.claim_rollback_attempts,
        )

    summary = {
        "dry_run": args.dry_run,
        "scope": args.scope,
        "run_id": args.run_id,
        "attempt": args.attempt,
        "run_started_at": args.run_started_at,
        "sessions": summaries,
        "drift_total": drift_total,
        "orphan_pruned_total": orphan_pruned_total,
        "failed_sessions": failed_sessions,
        "refused_sessions": refused_sessions,
        "movie_claim_rollbacks": claim_rollback_summaries,
    }
    _self._emit_metrics(summary)

    if failed_sessions:
        logger.error(
            "Rollback partially failed or was refused for sessions %s — "
            "check the per-session summary for details.",
            failed_sessions,
        )
        if (
            len(refused_committed_sessions) == len(failed_sessions)
            and not refused_sessions
        ):
            return (summary, 2)
        return (summary, 4)
    if refused_sessions:
        return (summary, 2)
    if drift_total > 0 and not args.dry_run:
        logger.warning(
            "Rollback completed but %d audit row(s) skipped due to "
            "concurrent-run drift; check the logs above and consider "
            "re-running with --scope history once the conflicting run "
            "settles.",
            drift_total,
        )
        return (summary, 4)
    return (summary, 0)


# ── Internal helpers ────────────────────────────────────────────────────


def _build_namespace(
    req: RollbackRequest, *, dry_run: bool,
) -> argparse.Namespace:
    """Translate a :class:`RollbackRequest` into the Namespace shape
    consumed by :func:`_drive_rollback`."""
    return argparse.Namespace(
        session_id=req.session_id,
        run_id=req.run_id,
        attempt=req.run_attempt,
        run_started_at=req.run_started_at,
        scope=req.scope,
        include_orphaned=req.include_orphaned,
        failure_reason=req.failure_reason,
        dry_run=dry_run,
        force=req.force,
        shard_date=req.shard_date,
        no_claim_rollback=req.no_claim_rollback,
        auto_resume_finalizing=req.include_pending,
        claim_rollback_attempts=req.claim_rollback_attempts,
    )


def _session_exists(session_id: str) -> bool:
    """Probe ReportSessions for *session_id*."""
    try:
        with get_db(_db.REPORTS_DB_PATH) as conn:
            row = conn.execute(
                "SELECT 1 FROM ReportSessions WHERE Id=? LIMIT 1",
                (session_id,),
            ).fetchone()
        return row is not None
    except Exception:
        return True


# ── Public entrypoints ──────────────────────────────────────────────────


def plan_rollback(req: RollbackRequest) -> RollbackPlan:
    """Resolve targets and run the rollback pipeline in dry-run.

    Raises :class:`LookupError` when an explicit ``session_id`` (or
    ``run_id`` + ``run_attempt``) cannot be resolved.
    """
    if req.session_id is not None and not _session_exists(req.session_id):
        raise LookupError(
            f"Session not found: session_id={req.session_id!r}"
        )

    args = _build_namespace(req, dry_run=True)
    summary, _exit_code = _drive_rollback(args)

    if summary.get("_no_targets") and req.run_id is not None:
        raise LookupError(
            f"No sessions matched the rollback request "
            f"(run_id={req.run_id!r}, run_attempt={req.run_attempt!r})"
        )

    clean = {k: v for k, v in summary.items() if not k.startswith("_")}
    sessions = list(clean.get("sessions") or [])
    session_id = (
        req.session_id
        or (sessions[0].get("session_id") if sessions else "")
    )
    rest = {k: v for k, v in clean.items() if k != "sessions"}
    return RollbackPlan(
        session_id=str(session_id) if session_id is not None else "",
        actions=sessions,
        summary=rest,
    )


def apply_rollback(req: RollbackRequest) -> RollbackResult:
    """Resolve targets and run the rollback for real.

    Raises :class:`LookupError` when an explicit ``session_id`` (or
    ``run_id`` + ``run_attempt``) cannot be resolved.
    """
    if req.session_id is not None and not _session_exists(req.session_id):
        raise LookupError(
            f"Session not found: session_id={req.session_id!r}"
        )

    args = _build_namespace(req, dry_run=False)
    summary, _exit_code = _drive_rollback(args)

    if summary.get("_no_targets") and req.run_id is not None:
        raise LookupError(
            f"No sessions matched the rollback request "
            f"(run_id={req.run_id!r}, run_attempt={req.run_attempt!r})"
        )

    clean = {k: v for k, v in summary.items() if not k.startswith("_")}
    sessions = list(clean.get("sessions") or [])
    session_id = (
        req.session_id
        or (sessions[0].get("session_id") if sessions else "")
    )
    rest = {k: v for k, v in clean.items() if k != "sessions"}
    return RollbackResult(
        session_id=str(session_id) if session_id is not None else "",
        applied=sessions,
        summary=rest,
    )
