#!/usr/bin/env python3
"""One-shot tool: detect and (optionally) delete phantom audit / history rows.

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

Default mode is dry-run: writes a JSON report to
``reports/cleanup_stale_session_audits_dryrun_<ts>.json`` and prints it
to stdout, but makes no changes.  Run with ``--apply`` to delete /
NULL out the offending rows.

Targets
-------
``--target sqlite|d1|both`` (default ``both``) selects which side to
inspect / clean.  When both, sqlite goes first so the operator can
sanity-check the dry-run there before letting it loose on D1.

Not for cron
------------
Use after :mod:`scripts.sync_d1_to_sqlite` has aligned both sides.
Not safe to run while the spider / pipeline is mutating tables.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
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
            "Identify (and optionally delete) phantom audit/history rows "
            "left behind by botched rollbacks. Dry-run by default."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--target",
        choices=["sqlite", "d1", "both"],
        default="both",
        help="Which side to inspect / clean. Default: both.",
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
        help="Also nullify SessionId on MovieHistory/TorrentHistory rows "
             "where SessionId is NOT NULL but doesn't exist in "
             "ReportSessions. Off by default — most legacy rows have "
             "SessionId=NULL anyway.",
    )
    p.set_defaults(dry_run=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="(default) Detect phantoms and write a report, but make no changes.",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Apply the deletions / NULLs to the targeted side(s).",
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


def _parse_session_ids(raw: Optional[str]) -> Optional[Set[int]]:
    if not raw:
        return None
    ids: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise SystemExit(f"Invalid session id in --session-ids: {part!r}")
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

    def fetch_report_session_ids(self) -> Tuple[Set[int], Set[int]]:
        """Return (all_session_ids, committed_session_ids)."""
        rows = self._reports.execute(
            "SELECT Id, Status FROM ReportSessions"
        ).fetchall()
        all_ids: Set[int] = set()
        committed: Set[int] = set()
        for r in rows:
            sid = r["Id"]
            if sid is None:
                continue
            sid = int(sid)
            all_ids.add(sid)
            if (r["Status"] or "").strip() == "committed":
                committed.add(sid)
        return all_ids, committed

    def fetch_audit_groups(self, table: str) -> Dict[int, Dict[str, Any]]:
        rows = self._history.execute(
            f"SELECT SessionId, COUNT(*) AS c, "
            f"MIN(DateTimeCreated) AS first_at, "
            f"MAX(DateTimeCreated) AS last_at "
            f"FROM {table} WHERE SessionId IS NOT NULL "
            f"GROUP BY SessionId"
        ).fetchall()
        out: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            sid = int(r["SessionId"])
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

    def delete_audit_rows(self, table: str, session_ids: List[int]) -> int:
        if not session_ids:
            return 0
        placeholders = ",".join("?" for _ in session_ids)
        cur = self._history.execute(
            f"DELETE FROM {table} WHERE SessionId IN ({placeholders})",
            list(session_ids),
        )
        return cur.rowcount or 0

    def nullify_history_session(
        self, table: str, session_ids: List[int],
    ) -> int:
        if not session_ids:
            return 0
        placeholders = ",".join("?" for _ in session_ids)
        cur = self._history.execute(
            f"UPDATE {table} SET SessionId=NULL "
            f"WHERE SessionId IN ({placeholders})",
            list(session_ids),
        )
        return cur.rowcount or 0

    def commit(self) -> None:
        self._history.commit()
        self._reports.commit()

    def rollback(self) -> None:
        self._history.rollback()
        self._reports.rollback()

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

    def fetch_report_session_ids(self) -> Tuple[Set[int], Set[int]]:
        cur = self._reports.execute("SELECT Id, Status FROM ReportSessions")
        rows = cur.fetchall() or []
        all_ids: Set[int] = set()
        committed: Set[int] = set()
        for r in rows:
            sid = r.get("Id") if isinstance(r, dict) else r[0]
            status = r.get("Status") if isinstance(r, dict) else r[1]
            if sid is None:
                continue
            sid = int(sid)
            all_ids.add(sid)
            if (status or "").strip() == "committed":
                committed.add(sid)
        return all_ids, committed

    def fetch_audit_groups(self, table: str) -> Dict[int, Dict[str, Any]]:
        cur = self._history.execute(
            f"SELECT SessionId, COUNT(*) AS c, "
            f"MIN(DateTimeCreated) AS first_at, "
            f"MAX(DateTimeCreated) AS last_at "
            f"FROM {table} WHERE SessionId IS NOT NULL "
            f"GROUP BY SessionId"
        )
        rows = cur.fetchall() or []
        out: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            sid = int(r.get("SessionId"))
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

    def delete_audit_rows(self, table: str, session_ids: List[int]) -> int:
        if not session_ids:
            return 0
        # D1 has a 100-bound-param cap per statement.  Build one
        # parameterised statement per chunk and submit the full list via
        # ``batch_execute`` so the chunks land atomically on D1's side —
        # the previous per-chunk ``execute`` loop auto-committed each
        # chunk, leaving partial state behind on failure.
        statements: List[Tuple[str, List[int]]] = []
        for chunk_start in range(0, len(session_ids), 90):
            chunk = session_ids[chunk_start: chunk_start + 90]
            placeholders = ",".join("?" for _ in chunk)
            statements.append((
                f"DELETE FROM {table} WHERE SessionId IN ({placeholders})",
                list(chunk),
            ))
        cursors = self._history.batch_execute(statements)
        return sum(int(c.rowcount or 0) for c in cursors)

    def nullify_history_session(
        self, table: str, session_ids: List[int],
    ) -> int:
        if not session_ids:
            return 0
        statements: List[Tuple[str, List[int]]] = []
        for chunk_start in range(0, len(session_ids), 90):
            chunk = session_ids[chunk_start: chunk_start + 90]
            placeholders = ",".join("?" for _ in chunk)
            statements.append((
                f"UPDATE {table} SET SessionId=NULL "
                f"WHERE SessionId IN ({placeholders})",
                list(chunk),
            ))
        cursors = self._history.batch_execute(statements)
        return sum(int(c.rowcount or 0) for c in cursors)

    def commit(self) -> None:
        # D1 auto-commits per statement.
        return None

    def rollback(self) -> None:
        return None

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
    # Strip tzinfo when only one side carries it so subtraction works.
    if (ta.tzinfo is None) != (tb.tzinfo is None):
        ta = ta.replace(tzinfo=None)
        tb = tb.replace(tzinfo=None)
    return abs((tb - ta).total_seconds()) / 3600.0


def _detect_phantoms(
    side,
    *,
    cross_day_hours: float,
    constrain_to_ids: Optional[Set[int]],
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


# ── Apply ──────────────────────────────────────────────────────────────


def _apply(side, findings: Dict[str, Any]) -> Dict[str, Any]:
    """Apply findings; capture per-table status so callers can see how
    far we got before any failure (no rollback is possible on D1)."""
    deleted: Dict[str, int] = defaultdict(int)
    table_status: Dict[str, str] = {}
    try:
        for audit_table, items in findings.get("audit", {}).items():
            sids = sorted({int(item["session_id"]) for item in items})
            if not sids:
                table_status[audit_table] = "skipped_empty"
                continue
            n = side.delete_audit_rows(audit_table, sids)
            deleted[audit_table] = n
            table_status[audit_table] = "ok"
            logger.info(
                "Deleted %d row(s) from %s on %s", n, audit_table, side.label,
            )
        for history_table, items in findings.get("history", {}).items():
            sids = sorted({int(item["session_id"]) for item in items})
            key = f"{history_table}.SessionId_nulled"
            if not sids:
                table_status[key] = "skipped_empty"
                continue
            n = side.nullify_history_session(history_table, sids)
            deleted[key] = n
            table_status[key] = "ok"
            logger.info(
                "Nulled SessionId on %d row(s) of %s on %s",
                n, history_table, side.label,
            )
        side.commit()
        return {
            "rows_changed": dict(deleted),
            "table_status": table_status,
            "partial_success": False,
        }
    except Exception as exc:
        logger.exception(
            "Apply on %s failed mid-stream after %d table(s) completed: %s",
            side.label, sum(1 for v in table_status.values() if v == "ok"),
            exc,
        )
        return {
            "rows_changed": dict(deleted),
            "table_status": table_status,
            "partial_success": True,
            "error": str(exc),
        }


def _refuse_when_dual_or_d1_under_apply(args: argparse.Namespace) -> None:
    """Hard-stop when --apply runs while live writers may be writing.

    Mirrors :func:`scripts.sync_d1_to_sqlite._refuse_when_dual_or_d1`: a
    pipeline running with ``STORAGE_BACKEND in {dual,d1}`` could race
    the destructive deletes/null-outs in :func:`_apply` and leave the
    two sides drifted in the very way this tool is meant to repair.
    Dry-run is read-only, so the guard only fires under --apply.
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
            "STORAGE_BACKEND=%s is set; pause live writers and re-run with "
            "STORAGE_BACKEND=sqlite (or unset). Refusing --apply.",
            backend,
        )
        sys.exit(1)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(log_level=args.log_level)
    _refuse_when_dual_or_d1_under_apply(args)

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

        if args.dry_run:
            overall["results"].append({
                "side": name,
                "findings": findings,
                "applied": False,
            })
            logger.info(
                "[dry-run] %s: audit_orphans=%d history_orphans=%d",
                name,
                findings["summary"]["audit_orphans_total"],
                findings["summary"]["history_orphans_total"],
            )
        else:
            try:
                apply_result = _apply(side, findings)
                overall["results"].append({
                    "side": name,
                    "findings": findings,
                    "applied": True,
                    "rows_changed": apply_result.get("rows_changed", {}),
                    "table_status": apply_result.get("table_status", {}),
                    "partial_success": apply_result.get(
                        "partial_success", False,
                    ),
                    **(
                        {"error": apply_result["error"]}
                        if apply_result.get("error") else {}
                    ),
                })
                if apply_result.get("partial_success"):
                    rc = 4
            except Exception as exc:
                logger.exception(
                    "Apply failed for side=%s: %s", name, exc,
                )
                try:
                    side.rollback()
                except Exception:
                    pass
                overall["results"].append({
                    "side": name,
                    "findings": findings,
                    "applied": False,
                    "error": str(exc),
                })
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
