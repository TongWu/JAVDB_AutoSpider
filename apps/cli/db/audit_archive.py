#!/usr/bin/env python3
"""Audit-table archival cron — Phase 4 (Ingestion Perfect Rollback).

Background
----------
The Pending write contract (Phase 3 default) recomputes derived fields
at commit time, so the legacy ``MovieHistoryAudit`` /
``TorrentHistoryAudit`` tables are no longer the source of truth for
rollback.  Phase 4 turns the audit tables read-only: nothing should be
appending new rows under the default workflow.  Anything still present
falls into one of three buckets:

1. **Committed > N days ago** — the owning ``ReportSessions`` row has
   ``Status='committed'`` and ``DateTimeCreated`` is older than the
   archive window (default ``30`` days).  These rows can never be
   rolled back (commit is immutable) and the same prune is already done
   inline by :func:`db_mark_session_committed`; the cron here mops up
   anything that escaped the inline prune (legacy data,
   ``mark_session_committed`` failures, manual SQL).
2. **Orphan session** — the audit row's ``SessionId`` does not exist in
   ``ReportSessions`` at all.  Once the row's ``DateTimeCreated`` is
   older than the window, no legitimate rollback will ever reach for it
   (the X3 rollback CLI rejects sessions that predate
   ``run_started_at - 1h``).  Prune them as cold storage.
3. **Failed / in_progress > N days ago** — owning session exists but
   ``Status`` is one of ``failed`` / ``in_progress`` / ``finalizing``.
   The rollback CLI's safety-net (see ``_rollback_pending_in_progress``)
   already drains audit rows it actually applies, so anything still
   present this long after creation has lost its rollback semantics.

By default the cron prints a summary and writes a JSON report.  Pass
``--apply`` to perform the DELETEs.  The default window is **30 days**;
override with ``--older-than-days`` if a recovery scenario needs a
shorter / longer horizon.

Targets
-------
``--target sqlite|d1|both`` (default ``both``).  When ``both``, sqlite
is processed first so the operator can verify the dry-run sqlite report
before the more expensive D1 pass runs.

Safety
------
* Default mode is dry-run.
* Refuses to ``--apply`` while ``STORAGE_BACKEND in {d1, dual}`` so a
  parallel pipeline can't race the deletes.
* Reports go to
  ``reports/D1/audit_archive/audit_archive_<dryrun|apply>_<ts>.json``.
* Cooperates with :data:`JAVDB_AUDIT_WRITES_DISABLED` — the kill switch
  blocks *new* audit rows; this cron mops up the existing ones.

Exit codes
----------
* ``0`` — success (dry-run or apply with no errors)
* ``2`` — refused (apply requested while STORAGE_BACKEND in {dual, d1})
* ``3`` — could not initialise an adapter (config / connectivity)
* ``4`` — partial failure during apply (some tables succeeded, some
  raised)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

from javdb.storage.db import db as _db
from javdb.infra.config import cfg
from javdb.storage.d1_client import (
    D1Connection,
    get_d1_account_id,
    get_d1_api_token,
    get_d1_database_id,
)
from javdb.infra.logging import (
    get_logger,
    setup_logging,
)


logger = get_logger("scripts.audit_archive")


_AUDIT_TABLES = ("MovieHistoryAudit", "TorrentHistoryAudit")
_DEFAULT_OLDER_THAN_DAYS = 30
_D1_PARAM_CHUNK = 90


# ── CLI ────────────────────────────────────────────────────────────────


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.audit_archive",
        description=(
            "Phase 4 audit archival cron. Prunes MovieHistoryAudit / "
            "TorrentHistoryAudit rows whose owning session is older than "
            "--older-than-days (default 30) and either committed, failed, "
            "or orphaned."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--target",
        choices=["sqlite", "d1", "both"],
        default="both",
        help="Which side to operate on. Default: both.",
    )
    p.add_argument(
        "--older-than-days",
        type=float,
        default=float(_DEFAULT_OLDER_THAN_DAYS),
        help="Window (days) measured against the audit row's "
             "DateTimeCreated. Default: 30.",
    )
    p.set_defaults(dry_run=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="(default) Report what would be archived; no changes.",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Apply the DELETEs to the targeted side(s).",
    )
    p.add_argument(
        "--report-path",
        type=str,
        default=None,
        help="Where to write the JSON report. Defaults to "
             "reports/D1/audit_archive/audit_archive_"
             "<dryrun|apply>_<ts>.json.",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return p.parse_args(argv)


def _refuse_when_dual_or_d1_under_apply(args: argparse.Namespace) -> None:
    """Hard-stop ``--apply`` while live writers may still be appending.

    Mirrors the contract used by :mod:`scripts.cleanup_stale_session_audits`
    pre-Phase-4: a pipeline running with ``STORAGE_BACKEND in {dual,d1}``
    could race the DELETEs and leave the two sides drifted.  Dry-run is
    safe in any backend.
    """
    if args.dry_run:
        return
    backend = (
        os.environ.get("STORAGE_BACKEND")
        or cfg("STORAGE_BACKEND", "sqlite")
        or "sqlite"
    ).strip().lower()
    if backend in ("dual", "d1"):
        logger.error(
            "STORAGE_BACKEND=%s is set; pause live writers and re-run "
            "with STORAGE_BACKEND=sqlite (or unset). Refusing --apply.",
            backend,
        )
        sys.exit(2)


# ── Adapters ───────────────────────────────────────────────────────────


class _SqliteSide:
    """SQLite adapter.  history.db owns the audit tables; reports.db
    owns ReportSessions.  Each session is mapped to a decision via the
    ``Status`` column lookup so we can sort rows into the three buckets.
    """

    label = "sqlite"

    def __init__(self) -> None:
        self._history = sqlite3.connect(_db.HISTORY_DB_PATH)
        self._history.row_factory = sqlite3.Row
        self._reports = sqlite3.connect(_db.REPORTS_DB_PATH)
        self._reports.row_factory = sqlite3.Row

    def fetch_status_by_session(self) -> Dict[str, str]:
        rows = self._reports.execute(
            "SELECT Id, Status FROM ReportSessions"
        ).fetchall()
        out: Dict[str, str] = {}
        for r in rows:
            sid = r["Id"]
            if sid is None:
                continue
            out[str(sid)] = (r["Status"] or "").strip()
        return out

    def fetch_audit_groups(self, table: str) -> Dict[str, Dict[str, Any]]:
        rows = self._history.execute(
            f"SELECT SessionId, COUNT(*) AS c, "
            f"MIN(DateTimeCreated) AS first_at, "
            f"MAX(DateTimeCreated) AS last_at "
            f"FROM {table} WHERE SessionId IS NOT NULL "
            f"GROUP BY SessionId"
        ).fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            sid = str(r["SessionId"])
            out[sid] = {
                "count": int(r["c"] or 0),
                "first_at": r["first_at"],
                "last_at": r["last_at"],
            }
        return out

    def delete_audit_rows(self, table: str, session_ids: List[str]) -> int:
        if not session_ids:
            return 0
        placeholders = ",".join("?" for _ in session_ids)
        cur = self._history.execute(
            f"DELETE FROM {table} WHERE SessionId IN ({placeholders})",
            list(session_ids),
        )
        return cur.rowcount or 0

    def commit(self) -> None:
        self._history.commit()

    def close(self) -> None:
        for conn in (self._history, self._reports):
            try:
                conn.close()
            except Exception:
                pass


class _D1Side:
    """D1 adapter — same surface as the sqlite side."""

    label = "d1"

    def __init__(self) -> None:
        token = get_d1_api_token()
        account = get_d1_account_id()
        self._history = D1Connection(
            account_id=account,
            database_id=get_d1_database_id("history"),
            api_token=token,
        )
        self._reports = D1Connection(
            account_id=account,
            database_id=get_d1_database_id("reports"),
            api_token=token,
        )

    def fetch_status_by_session(self) -> Dict[str, str]:
        cur = self._reports.execute(
            "SELECT Id, Status FROM ReportSessions"
        )
        rows = cur.fetchall() or []
        out: Dict[str, str] = {}
        for r in rows:
            sid = r.get("Id") if isinstance(r, dict) else r[0]
            status = r.get("Status") if isinstance(r, dict) else r[1]
            if sid is None:
                continue
            out[str(sid)] = (status or "").strip()
        return out

    def fetch_audit_groups(self, table: str) -> Dict[str, Dict[str, Any]]:
        cur = self._history.execute(
            f"SELECT SessionId, COUNT(*) AS c, "
            f"MIN(DateTimeCreated) AS first_at, "
            f"MAX(DateTimeCreated) AS last_at "
            f"FROM {table} WHERE SessionId IS NOT NULL "
            f"GROUP BY SessionId"
        )
        rows = cur.fetchall() or []
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            sid = str(r.get("SessionId"))
            out[sid] = {
                "count": int(r.get("c") or 0),
                "first_at": r.get("first_at"),
                "last_at": r.get("last_at"),
            }
        return out

    def delete_audit_rows(self, table: str, session_ids: List[str]) -> int:
        if not session_ids:
            return 0
        statements: List[Tuple[str, List[str]]] = []
        for start in range(0, len(session_ids), _D1_PARAM_CHUNK):
            chunk = session_ids[start: start + _D1_PARAM_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            statements.append((
                f"DELETE FROM {table} WHERE SessionId IN ({placeholders})",
                list(chunk),
            ))
        cursors = self._history.batch_execute(statements)
        return sum(int(c.rowcount or 0) for c in cursors)

    def commit(self) -> None:
        # D1 auto-commits per batch_execute.
        return None

    def close(self) -> None:
        for conn in (self._history, self._reports):
            try:
                conn.close()
            except Exception:
                pass


# ── Decision logic ─────────────────────────────────────────────────────


def _parse_audit_timestamp(value: str) -> Optional[datetime]:
    """Best-effort parse mirroring the cleanup script's helper.

    Audit rows historically carry either ``%Y-%m-%d %H:%M:%S`` or ISO
    8601 with trailing ``Z``.  Returning ``None`` on unknown input is
    handled by the caller (it conservatively skips that audit group).
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    iso = value.strip()
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _normalise_utc(dt: datetime) -> datetime:
    """Treat naïve timestamps as UTC (matches the X3 audit-log writer)
    and convert tz-aware values to UTC so cutoff comparisons stay sane.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _classify(
    sid: str,
    info: Dict[str, Any],
    status_by_session: Dict[str, str],
    cutoff_utc: datetime,
) -> Optional[Dict[str, Any]]:
    """Return a decision dict if *sid*'s audit rows should be pruned.

    ``last_at`` carries the most recent audit-row timestamp for the
    session.  We require *every* audit row in the group to be older
    than the cutoff (i.e. last_at < cutoff) so an in-flight or recently
    finalised session is never touched.
    """
    last_at_raw = info.get("last_at")
    if not last_at_raw:
        return None
    last_dt = _parse_audit_timestamp(str(last_at_raw))
    if last_dt is None:
        return None
    last_dt = _normalise_utc(last_dt)
    if last_dt >= cutoff_utc:
        return None  # too recent; rollback may still need it.

    status = status_by_session.get(sid)
    if status is None:
        reason = "orphan_session"
    elif status == "committed":
        reason = "committed_expired"
    elif status in ("failed", "in_progress", "finalizing"):
        # Failed sessions live long enough that audit retention is
        # pointless; in_progress / finalizing past the 30-day cutoff
        # are stuck rows that the stale-session cron will have refused
        # (no RunId / too old) — treat them as orphans.
        reason = f"{status}_expired"
    else:
        return None

    return {
        "session_id": sid,
        "reason": reason,
        "row_count": int(info.get("count") or 0),
        "first_at": info.get("first_at"),
        "last_at": info.get("last_at"),
        "status": status,
    }


def _build_plan(
    side,
    *,
    cutoff_utc: datetime,
) -> Dict[str, Any]:
    """Build a per-table archival plan for *side*."""
    status_by_session = side.fetch_status_by_session()
    plan: Dict[str, Any] = {
        "side": side.label,
        "cutoff_utc": cutoff_utc.isoformat(timespec="seconds"),
        "tables": {t: [] for t in _AUDIT_TABLES},
        "summary": {"sessions_total": 0, "rows_total": 0},
    }
    for table in _AUDIT_TABLES:
        groups = side.fetch_audit_groups(table)
        for sid, info in groups.items():
            decision = _classify(sid, info, status_by_session, cutoff_utc)
            if decision is None:
                continue
            plan["tables"][table].append(decision)
            plan["summary"]["rows_total"] += decision["row_count"]
        plan["summary"]["sessions_total"] += len(plan["tables"][table])
    return plan


def _apply_plan(side, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the plan; return per-table counts plus a status map."""
    deleted: Dict[str, int] = defaultdict(int)
    table_status: Dict[str, str] = {}
    try:
        for table, items in plan.get("tables", {}).items():
            sids = sorted({item["session_id"] for item in items})
            if not sids:
                table_status[table] = "skipped_empty"
                continue
            n = side.delete_audit_rows(table, sids)
            deleted[table] = n
            table_status[table] = "ok"
            logger.info(
                "Archived %d row(s) from %s on %s",
                n, table, side.label,
            )
        side.commit()
        return {
            "rows_changed": dict(deleted),
            "table_status": table_status,
            "partial_success": False,
        }
    except Exception as exc:  # noqa: BLE001 — propagate as report
        logger.exception(
            "audit_archive apply failed on %s after %d ok table(s): %s",
            side.label,
            sum(1 for v in table_status.values() if v == "ok"),
            exc,
        )
        return {
            "rows_changed": dict(deleted),
            "table_status": table_status,
            "partial_success": True,
            "error": str(exc),
        }


# ── Entry point ───────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)
    _refuse_when_dual_or_d1_under_apply(args)

    now_utc = datetime.now(UTC)
    cutoff_utc = now_utc - timedelta(days=float(args.older_than_days))

    sides: List[Tuple[str, Any]] = []
    try:
        if args.target in ("sqlite", "both"):
            sides.append(("sqlite", _SqliteSide()))
        if args.target in ("d1", "both"):
            sides.append(("d1", _D1Side()))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to initialise target adapter: %s", exc)
        for _, s in sides:
            try:
                s.close()
            except Exception:
                pass
        return 3

    overall: Dict[str, Any] = {
        "kind": "audit_archive",
        "dry_run": args.dry_run,
        "older_than_days": args.older_than_days,
        "cutoff_utc": cutoff_utc.isoformat(timespec="seconds"),
        "started_at": now_utc.isoformat(timespec="seconds"),
        "results": [],
    }
    rc = 0
    for name, side in sides:
        try:
            plan = _build_plan(side, cutoff_utc=cutoff_utc)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Plan build failed for side=%s: %s", name, exc,
            )
            overall["results"].append({"side": name, "error": str(exc)})
            rc = 4
            try:
                side.close()
            except Exception:
                pass
            continue

        if args.dry_run:
            overall["results"].append({
                "side": name,
                "plan": plan,
                "applied": False,
            })
            logger.info(
                "[dry-run] %s: sessions_total=%d rows_total=%d "
                "cutoff_utc=%s",
                name,
                plan["summary"]["sessions_total"],
                plan["summary"]["rows_total"],
                plan["cutoff_utc"],
            )
        else:
            result = _apply_plan(side, plan)
            overall["results"].append({
                "side": name,
                "plan": plan,
                "applied": True,
                "rows_changed": result.get("rows_changed", {}),
                "table_status": result.get("table_status", {}),
                "partial_success": result.get("partial_success", False),
                **(
                    {"error": result["error"]}
                    if result.get("error") else {}
                ),
            })
            if result.get("partial_success"):
                rc = 4
        try:
            side.close()
        except Exception:
            pass

    reports_dir = (
        os.environ.get("REPORTS_DIR")
        or cfg("REPORTS_DIR", "reports")
        or "reports"
    )
    report_path = args.report_path or os.path.join(
        reports_dir,
        "D1",
        "audit_archive",
        "audit_archive_{}_{}.json".format(
            "dryrun" if args.dry_run else "apply",
            datetime.now().strftime("%Y%m%d_%H%M%S"),
        ),
    )
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)
    logger.info("Wrote report: %s", report_path)
    print(json.dumps(overall, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
