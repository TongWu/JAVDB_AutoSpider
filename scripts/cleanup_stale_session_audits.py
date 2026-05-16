#!/usr/bin/env python3
"""One-shot tool: detect phantom audit / history rows (read-only since Phase 4).

Background
----------
On 2026-05-08 a long-tail of ``MovieHistoryAudit`` rows tagged
``SessionId=332`` were observed spanning multiple workflow runs (D1
``DateTimeCreated`` from 2026-05-07 09:51 through 2026-05-08 20:24).
The owning ``ReportSessions.Id=332`` was deleted by a prior failed
rollback, leaving 145 audit rows orphaned and confusing every
subsequent rollback attempt.

This script identifies three kinds of phantoms:

1. *orphan-session* — an audit / history row whose ``SessionId``
   does NOT exist in ``ReportSessions``.
2. *cross-day* — a group of audit rows sharing the same ``SessionId``
   but spanning more than 12 hours, i.e. multiple workflow runs got
   merged onto the same id (impossible for a single 5–10 min run).
3. *committed-with-audit* — audit rows whose owning ``ReportSessions``
   is ``Status='committed'`` and therefore should have been pruned by
   :func:`db_mark_session_committed` but weren't (legacy data).

Read-only since Phase 4 (2026-05-13)
------------------------------------
The audit tables are now treated as historical-session forensics only;
their **write** path is owned by :mod:`scripts.audit_archive` (the
30-day commit-window archival cron).  This script therefore writes a
JSON report describing what it would have deleted under the legacy
``--apply`` mode, but never mutates ``MovieHistoryAudit`` /
``TorrentHistoryAudit`` / ``MovieHistory`` / ``TorrentHistory``.
Passing ``--apply`` is accepted only to keep older runbooks parsable —
it logs a warning and behaves identically to ``--dry-run``.

Targets
-------
``--target sqlite|d1|both`` (default ``both``) selects which side to
inspect.  When both, sqlite goes first so the operator can sanity-check
the report there before consulting D1.

Not for cron
------------
Use after :mod:`scripts.sync_d1_to_sqlite` has aligned both sides.
The recurring archival job lives in
:mod:`scripts.audit_archive` (``.github/workflows/AuditArchive.yml``);
this entry point is reserved for one-shot incident-response audits.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

if __package__ in (None, ""):
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

from packages.python.javdb_platform import db as _db
from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_platform.d1_client import (
    D1Connection,
    get_d1_account_id,
    get_d1_api_token,
    get_d1_database_id,
)
from packages.python.javdb_platform.logging_config import (
    get_logger,
    setup_logging,
)


logger = get_logger("scripts.cleanup_stale_session_audits")

_AUDIT_TABLES = ("MovieHistoryAudit", "TorrentHistoryAudit")
_HISTORY_TABLES = ("MovieHistory", "TorrentHistory")
_DEFAULT_CROSS_DAY_HOURS = 12


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.cleanup_stale_session_audits",
        description=(
            "Identify phantom audit / history rows left behind by botched "
            "rollbacks. Read-only since Phase 4 — writes happen in "
            "scripts.audit_archive."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--target",
        choices=["sqlite", "d1", "both"],
        default="both",
        help="Which side to inspect. Default: both.",
    )
    p.add_argument(
        "--session-ids",
        type=str,
        default=None,
        help="Comma-separated session ids to constrain detection to. "
             "When omitted, every SessionId not found in ReportSessions "
             "is candidate.",
    )
    p.add_argument(
        "--cross-day-hours",
        type=float,
        default=_DEFAULT_CROSS_DAY_HOURS,
        help="Audit rows sharing a SessionId but spanning more than this "
             "many hours are flagged as cross-day phantoms (default: 12).",
    )
    p.add_argument(
        "--include-history-tables",
        action="store_true",
        default=False,
        help="Also flag MovieHistory / TorrentHistory rows whose "
             "SessionId is NOT NULL but doesn't exist in ReportSessions. "
             "Off by default — most legacy rows have SessionId=NULL.",
    )
    # ``--apply`` is retained for backwards-compatible CLI usage but now
    # logs a warning and behaves like ``--dry-run`` (Phase 4 read-only
    # contract).  Operators wanting an enforced apply path should use
    # ``scripts.audit_archive``.
    p.set_defaults(dry_run=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="(default) Detect phantoms and write a report, no changes.",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="DEPRECATED. Logs a warning and falls back to dry-run; use "
             "scripts.audit_archive --apply for the cron archival path.",
    )
    p.add_argument(
        "--report-path",
        type=str,
        default=None,
        help="Where to write the JSON report. Defaults to "
             "reports/cleanup_stale_session_audits_<dryrun|apply>_<ts>.json.",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return p.parse_args(argv)


def _parse_session_ids(raw: Optional[str]) -> Optional[Set[str]]:
    if not raw:
        return None
    ids: Set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(part)
    return ids or None


# ── Adapter abstractions ────────────────────────────────────────────────


class _SqliteSide:
    """Adapter: ``conn`` per logical DB, plus a backup helper."""

    label = "sqlite"

    def __init__(self) -> None:
        # Resolve through the db module so conftest fixtures that
        # monkeypatch ``HISTORY_DB_PATH`` / ``REPORTS_DB_PATH`` are
        # honoured by ad-hoc tooling too.
        self._history = sqlite3.connect(_db.HISTORY_DB_PATH)
        self._history.row_factory = sqlite3.Row
        self._reports = sqlite3.connect(_db.REPORTS_DB_PATH)
        self._reports.row_factory = sqlite3.Row

    def fetch_report_session_ids(self) -> Tuple[Set[str], Set[str]]:
        """Return (all_session_ids, committed_session_ids)."""
        rows = self._reports.execute(
            "SELECT Id, Status FROM ReportSessions"
        ).fetchall()
        all_ids: Set[str] = set()
        committed: Set[str] = set()
        for r in rows:
            sid = r["Id"]
            if sid is None:
                continue
            sid = str(sid)
            all_ids.add(sid)
            if (r["Status"] or "").strip() == "committed":
                committed.add(sid)
        return all_ids, committed

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

    def fetch_history_orphans(self, table: str) -> Dict[int, int]:
        rows = self._history.execute(
            f"SELECT SessionId, COUNT(*) AS c FROM {table} "
            f"WHERE SessionId IS NOT NULL GROUP BY SessionId"
        ).fetchall()
        return {int(r["SessionId"]): int(r["c"] or 0) for r in rows}

    def close(self) -> None:
        self._history.close()
        self._reports.close()


class _D1Side:
    """Adapter: one D1Connection per logical DB."""

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

    def fetch_report_session_ids(self) -> Tuple[Set[str], Set[str]]:
        cur = self._reports.execute("SELECT Id, Status FROM ReportSessions")
        rows = cur.fetchall() or []
        all_ids: Set[str] = set()
        committed: Set[str] = set()
        for r in rows:
            sid = r.get("Id") if isinstance(r, dict) else r[0]
            status = r.get("Status") if isinstance(r, dict) else r[1]
            if sid is None:
                continue
            sid = str(sid)
            all_ids.add(sid)
            if (status or "").strip() == "committed":
                committed.add(sid)
        return all_ids, committed

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

    def fetch_history_orphans(self, table: str) -> Dict[int, int]:
        cur = self._history.execute(
            f"SELECT SessionId, COUNT(*) AS c FROM {table} "
            f"WHERE SessionId IS NOT NULL GROUP BY SessionId"
        )
        rows = cur.fetchall() or []
        return {int(r.get("SessionId")): int(r.get("c") or 0) for r in rows}

    def close(self) -> None:
        try:
            self._history.close()
        except Exception:
            pass
        try:
            self._reports.close()
        except Exception:
            pass


# ── Detection ──────────────────────────────────────────────────────────


def _parse_timestamp(value: str) -> Optional[datetime]:
    """Best-effort parse of audit timestamps.

    Audit rows may carry either the legacy ``%Y-%m-%d %H:%M:%S`` shape
    or ISO 8601 (with ``T`` separator and/or trailing ``Z``).  Silently
    returning ``None`` on any unrecognised format used to hide cross-day
    phantoms; on failure we log the offending input so the operator can
    extend this helper.
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
        pass
    try:  # optional dateutil fallback for assorted offsets
        from dateutil import parser as _dateutil_parser  # type: ignore
    except ImportError:
        return None
    try:
        return _dateutil_parser.parse(value)
    except (ValueError, TypeError):
        return None


def _hours_between(a: Optional[str], b: Optional[str]) -> Optional[float]:
    if not a or not b:
        return None
    ta = _parse_timestamp(a)
    tb = _parse_timestamp(b)
    if ta is None or tb is None:
        logger.warning(
            "_hours_between: unparseable timestamp(s) a=%r b=%r", a, b,
        )
        return None
    # P1: when one timestamp is tz-aware and the other naïve, the legacy
    # implementation stripped both to naïve and silently lost the
    # timezone offset — e.g. a UTC timestamp paired with a naïve
    # Asia/Singapore (+08:00) reading produced 8h of phantom drift. Treat
    # any naïve timestamp as UTC (the canonical wire format for our
    # session-audit table) and compute the delta in UTC space.
    from datetime import timezone as _tz
    if ta.tzinfo is None:
        ta = ta.replace(tzinfo=_tz.utc)
    if tb.tzinfo is None:
        tb = tb.replace(tzinfo=_tz.utc)
    ta = ta.astimezone(_tz.utc)
    tb = tb.astimezone(_tz.utc)
    return abs((tb - ta).total_seconds()) / 3600.0


def _detect_phantoms(
    side,
    *,
    cross_day_hours: float,
    constrain_to_ids: Optional[Set[str]],
    include_history_tables: bool,
) -> Dict[str, Any]:
    """Run the three detection passes and return a dry-run-shaped dict."""
    all_ids, committed_ids = side.fetch_report_session_ids()

    def _flag(sid: int, reason: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        return {"session_id": sid, "reason": reason, **extra}

    audit_findings: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in _AUDIT_TABLES
    }
    history_findings: Dict[str, List[Dict[str, Any]]] = {
        t: [] for t in _HISTORY_TABLES
    }

    for audit_table in _AUDIT_TABLES:
        groups = side.fetch_audit_groups(audit_table)
        for sid, info in groups.items():
            if constrain_to_ids is not None and sid not in constrain_to_ids:
                continue
            if sid not in all_ids:
                audit_findings[audit_table].append(_flag(
                    sid, "orphan_session",
                    {"row_count": info["count"], **info},
                ))
                continue
            span_hours = _hours_between(info.get("first_at"), info.get("last_at"))
            if span_hours is not None and span_hours > cross_day_hours:
                audit_findings[audit_table].append(_flag(
                    sid, "cross_day",
                    {"row_count": info["count"], "span_hours": span_hours, **info},
                ))
                continue
            if sid in committed_ids:
                audit_findings[audit_table].append(_flag(
                    sid, "committed_with_audit",
                    {"row_count": info["count"], **info},
                ))

    if include_history_tables:
        for history_table in _HISTORY_TABLES:
            counts = side.fetch_history_orphans(history_table)
            for sid, n in counts.items():
                if constrain_to_ids is not None and sid not in constrain_to_ids:
                    continue
                if sid not in all_ids:
                    history_findings[history_table].append(_flag(
                        sid, "orphan_session", {"row_count": n},
                    ))

    return {
        "side": side.label,
        "audit": audit_findings,
        "history": history_findings,
        "summary": {
            "audit_orphans_total": sum(
                len(v) for v in audit_findings.values()
            ),
            "history_orphans_total": sum(
                len(v) for v in history_findings.values()
            ),
        },
    }


# ── Apply (removed in Phase 4) ─────────────────────────────────────────
#
# This entry point used to support ``--apply`` for destructive cleanup
# of phantom audit rows.  The Phase 4 contract makes the audit tables
# read-only (forensics for historic sessions only); the recurring
# archival path is owned by :mod:`scripts.audit_archive`.  Operators
# who reach for a one-shot destructive cleanup should use the archival
# script directly.


def _enforce_readonly(args: argparse.Namespace) -> None:
    """Phase 4 contract: this tool is now strictly read-only.

    Operators (and CI runbooks) that still pass ``--apply`` get a clear
    deprecation log and silently degrade to dry-run.  The destructive
    archival path is owned by :mod:`scripts.audit_archive`, which targets
    the 30-day commit-window only and runs from a dedicated cron.
    """
    if args.dry_run:
        return
    logger.warning(
        "--apply on scripts.cleanup_stale_session_audits is deprecated "
        "since Phase 4 (2026-05). The audit tables are now read-only; "
        "use `python3 -m scripts.audit_archive --apply` for the 30-day "
        "commit-window archival cron. Falling back to dry-run."
    )
    args.dry_run = True


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)
    _enforce_readonly(args)

    constrain = _parse_session_ids(args.session_ids)

    sides: List[Tuple[str, Any]] = []
    try:
        if args.target in ("sqlite", "both"):
            sides.append(("sqlite", _SqliteSide()))
        if args.target in ("d1", "both"):
            sides.append(("d1", _D1Side()))
    except Exception as exc:
        logger.error("Failed to initialise target adapter: %s", exc)
        for _, s in sides:
            try:
                s.close()
            except Exception:
                pass
        return 3

    overall: Dict[str, Any] = {
        "kind": "cleanup_stale_session_audits",
        "dry_run": args.dry_run,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cross_day_hours": args.cross_day_hours,
        "constrain_to_session_ids": (
            sorted(constrain) if constrain else None
        ),
        "include_history_tables": args.include_history_tables,
        "results": [],
    }
    rc = 0
    for name, side in sides:
        try:
            findings = _detect_phantoms(
                side,
                cross_day_hours=args.cross_day_hours,
                constrain_to_ids=constrain,
                include_history_tables=args.include_history_tables,
            )
        except Exception as exc:
            logger.exception(
                "Detection failed for side=%s: %s", name, exc,
            )
            overall["results"].append({"side": name, "error": str(exc)})
            rc = 4
            continue

        # Phase 4: read-only. ``_enforce_readonly`` already coerced
        # ``args.dry_run`` to True, so we only ever record findings here.
        overall["results"].append({
            "side": name,
            "findings": findings,
            "applied": False,
        })
        logger.info(
            "[read-only] %s: audit_orphans=%d history_orphans=%d",
            name,
            findings["summary"]["audit_orphans_total"],
            findings["summary"]["history_orphans_total"],
        )
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
        "cleanup_stale_session_audits",
        "cleanup_stale_session_audits_{}_{}.json".format(
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
