"""Check ADR-006 30-day bake metrics — the ADR-005 D10 gate.

ADR-006 introduced a 30-day bake period after the Python
``_resolve_write_mode`` default flipped to ``'pending'`` (PR-A, merged
2026-05-16). ADR-005 D10 requires three metrics to hold cleanly across
the trailing 30 days before audit-mode retirement can proceed:

1. **No new audit-mode sessions.** ``ReportSessions.WriteMode='audit'``
   count over the window must be 0. Any non-zero count means a
   workflow is still writing audit-mode rows — investigate which run
   triggered it before declaring the bake clean.

2. **No orphan audit rows.** Audit rows owned by sessions in
   ``Status='committed'`` should not exist; the inline cleanup in
   ``db_mark_session_committed`` prunes them at commit time. Surviving
   rows usually mean a transient D1 hiccup ate the prune INSERT.

3. **Pause-script trigger count ≤ 1 per month.** Counted from
   ``reports/D1/d1_drift.jsonl`` as the number of distinct
   ``(run_id, run_attempt)`` tuples whose ``pending_session_verify``
   records carry ``pending_residual_count > 0``,
   ``derived_recompute_drift > 0``, or ``cleanup_path_mismatch_count > 0``
   (the same critical-fields predicate the email job uses to engage the
   pause). Sustained trigger volume above the threshold means
   Pending Mode is still leaking a root-cause defect; investigate
   before retiring audit.

Run daily during bake. Returns exit 0 if all three checks pass,
exit 1 otherwise.

Usage::

    python3 -m scripts.check_bake_metrics
    python3 -m scripts.check_bake_metrics --window-days 30 --json
    python3 -m scripts.check_bake_metrics --since 2026-05-16

Local SQLite is the only backend supported here — D1 is queried via
HTTP in production but the bake-monitoring use case is reproducible
from a fresh local checkout (``reports/*.db`` is fetched from the
``pipeline-reports-encrypted`` artifact).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# Default thresholds — D10 gate per ADR-005.
_AUDIT_SESSION_MAX = 0          # any audit session in window fails the gate
_ORPHAN_AUDIT_MAX = 0           # any orphan audit row fails the gate
_PAUSE_TRIGGER_MAX_PER_MONTH = 1
_DEFAULT_WINDOW_DAYS = 30

# Critical alert predicate — must mirror the email job logic in
# .github/workflows/DailyIngestion.yml (Alert + pause step).
_CRITICAL_FIELDS = (
    "pending_residual_count",
    "derived_recompute_drift",
    "cleanup_path_mismatch_count",
)

REPORTS_DB = Path("reports/reports.db")
HISTORY_DB = Path("reports/history.db")
JSONL_PATH = Path("reports/D1/d1_drift.jsonl")


# ── Result types ──────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    passed: bool
    actual: int
    threshold: int
    detail: str = ""
    samples: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "actual": self.actual,
            "threshold": self.threshold,
            "detail": self.detail,
            "samples": self.samples,
        }


# ── Query helpers ─────────────────────────────────────────────────────


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection. Raises FileNotFoundError if
    the DB doesn't exist (the caller decides whether that's a hard fail
    or a "no data yet" signal)."""
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True,
    )
    conn.row_factory = sqlite3.Row
    return conn


def check_audit_session_count(
    reports_db: Path, *, since: str,
) -> CheckResult:
    """D10 #1 — count `WriteMode='audit'` sessions created after *since*.

    *since* is a SQLite-friendly UTC string (``%Y-%m-%d %H:%M:%S``).
    """
    samples: List[str] = []
    try:
        with _connect(reports_db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM ReportSessions "
                "WHERE WriteMode='audit' AND DateTimeCreated > ?",
                (since,),
            ).fetchone()[0]
            if count > 0:
                rows = conn.execute(
                    "SELECT Id FROM ReportSessions "
                    "WHERE WriteMode='audit' AND DateTimeCreated > ? "
                    "ORDER BY DateTimeCreated DESC LIMIT 5",
                    (since,),
                ).fetchall()
                samples = [r["Id"] for r in rows]
    except FileNotFoundError as exc:
        return CheckResult(
            name="audit_session_count",
            passed=False,
            actual=-1,
            threshold=_AUDIT_SESSION_MAX,
            detail=f"reports DB not found: {exc}",
        )
    return CheckResult(
        name="audit_session_count",
        passed=count <= _AUDIT_SESSION_MAX,
        actual=count,
        threshold=_AUDIT_SESSION_MAX,
        detail=(
            f"sessions created after {since} with WriteMode='audit'"
        ),
        samples=samples,
    )


def check_orphan_audit_rows(
    history_db: Path, reports_db: Path,
) -> CheckResult:
    """D10 #2 — count audit rows whose owning session is `committed`.

    These should have been pruned at commit time by
    ``db_mark_session_committed``; surviving rows are usually leftovers
    from a transient D1 hiccup during the commit-path step.
    """
    try:
        with _connect(history_db) as conn:
            conn.execute(
                f"ATTACH DATABASE 'file:{reports_db}?mode=ro' "
                f"AS r KEY ''"
            )
            movie_orphan = conn.execute(
                "SELECT COUNT(*) FROM MovieHistoryAudit ma "
                "WHERE ma.SessionId IN ("
                "  SELECT Id FROM r.ReportSessions WHERE Status='committed'"
                ")"
            ).fetchone()[0]
            torrent_orphan = conn.execute(
                "SELECT COUNT(*) FROM TorrentHistoryAudit ta "
                "WHERE ta.SessionId IN ("
                "  SELECT Id FROM r.ReportSessions WHERE Status='committed'"
                ")"
            ).fetchone()[0]
            total = movie_orphan + torrent_orphan
    except FileNotFoundError as exc:
        return CheckResult(
            name="orphan_audit_rows",
            passed=False,
            actual=-1,
            threshold=_ORPHAN_AUDIT_MAX,
            detail=f"history / reports DB not found: {exc}",
        )
    except sqlite3.OperationalError as exc:
        # Either audit table doesn't exist (post-ADR-005 schema) or
        # cross-DB ATTACH failed. The former is GOOD news.
        if "no such table" in str(exc).lower():
            return CheckResult(
                name="orphan_audit_rows",
                passed=True,
                actual=0,
                threshold=_ORPHAN_AUDIT_MAX,
                detail="audit tables already dropped (ADR-005 complete?)",
            )
        return CheckResult(
            name="orphan_audit_rows",
            passed=False,
            actual=-1,
            threshold=_ORPHAN_AUDIT_MAX,
            detail=f"sqlite error: {exc}",
        )
    return CheckResult(
        name="orphan_audit_rows",
        passed=total <= _ORPHAN_AUDIT_MAX,
        actual=total,
        threshold=_ORPHAN_AUDIT_MAX,
        detail=(
            f"MovieHistoryAudit orphans={movie_orphan}, "
            f"TorrentHistoryAudit orphans={torrent_orphan}, "
            f"owned by Status='committed' sessions"
        ),
    )


def check_pause_trigger_count(
    jsonl_path: Path, *, since_ts: datetime,
    threshold_per_month: int = _PAUSE_TRIGGER_MAX_PER_MONTH,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> CheckResult:
    """D10 #3 — distinct (run_id, run_attempt) tuples whose
    ``pending_session_verify`` records meet the email job's critical
    predicate after *since_ts*.

    The threshold scales with the window length: ≤ N per 30 days, so
    ≤ N*(window_days/30) per *window_days*. Rounded up to be lenient on
    short windows.
    """
    if not jsonl_path.exists():
        return CheckResult(
            name="pause_trigger_count",
            passed=True,
            actual=0,
            threshold=threshold_per_month,
            detail=f"no jsonl at {jsonl_path} (no triggers yet)",
        )
    triggered_runs: Set[Tuple[str, str]] = set()
    sample_lines: List[str] = []
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") != "pending_session_verify":
                    continue
                ts = rec.get("ts")
                if not ts:
                    continue
                try:
                    rec_dt = datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00"),
                    )
                except ValueError:
                    continue
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                if rec_dt < since_ts:
                    continue
                # Apply the same critical predicate the email job uses.
                if not any(
                    int(rec.get(k) or 0) > 0 for k in _CRITICAL_FIELDS
                ):
                    continue
                run_id = str(rec.get("run_id") or "")
                attempt = str(rec.get("run_attempt") or "")
                triggered_runs.add((run_id, attempt))
                if len(sample_lines) < 5:
                    sample_lines.append(
                        f"{ts} session={rec.get('session_id')} "
                        + " ".join(
                            f"{k}={rec.get(k)}"
                            for k in _CRITICAL_FIELDS
                            if int(rec.get(k) or 0) > 0
                        )
                    )
    except OSError as exc:
        return CheckResult(
            name="pause_trigger_count",
            passed=False,
            actual=-1,
            threshold=threshold_per_month,
            detail=f"read failed: {exc}",
        )
    actual = len(triggered_runs)
    scaled_threshold = max(
        1, (threshold_per_month * window_days + 29) // 30,
    )
    return CheckResult(
        name="pause_trigger_count",
        passed=actual <= scaled_threshold,
        actual=actual,
        threshold=scaled_threshold,
        detail=(
            f"{actual} distinct (run_id, attempt) pairs with a critical "
            f"pending_session_verify since {since_ts.isoformat()}; "
            f"threshold {scaled_threshold} (= {threshold_per_month}/month "
            f"scaled to {window_days}d)"
        ),
        samples=sample_lines,
    )


# ── CLI ───────────────────────────────────────────────────────────────


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.check_bake_metrics",
        description=(
            "Check ADR-006 30-day bake metrics against the ADR-005 D10 "
            "gate. Returns exit 0 if all three checks pass."
        ),
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=_DEFAULT_WINDOW_DAYS,
        help=(
            f"Trailing window in days (default {_DEFAULT_WINDOW_DAYS}). "
            "Mutually exclusive with --since."
        ),
    )
    p.add_argument(
        "--since",
        type=str,
        default=None,
        help=(
            "ISO date (YYYY-MM-DD) or timestamp marking the start of "
            "the bake window. Overrides --window-days. Example: "
            "--since 2026-05-16 anchors the bake to ADR-006 PR-A merge."
        ),
    )
    p.add_argument(
        "--reports-db",
        type=Path,
        default=REPORTS_DB,
        help="Path to reports.db (default: reports/reports.db).",
    )
    p.add_argument(
        "--history-db",
        type=Path,
        default=HISTORY_DB,
        help="Path to history.db (default: reports/history.db).",
    )
    p.add_argument(
        "--jsonl",
        type=Path,
        default=JSONL_PATH,
        help="Path to d1_drift.jsonl (default: reports/D1/d1_drift.jsonl).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout instead of text.",
    )
    return p.parse_args(argv)


def _resolve_window(args: argparse.Namespace) -> Tuple[str, datetime, int]:
    """Return (sqlite_since_str, since_dt, window_days)."""
    now = datetime.now(tz=timezone.utc)
    if args.since:
        try:
            since_dt = datetime.fromisoformat(
                args.since.replace("Z", "+00:00"),
            )
        except ValueError:
            # Try YYYY-MM-DD only.
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(
                tzinfo=timezone.utc,
            )
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
        window_days = max(1, (now - since_dt).days)
    else:
        window_days = max(1, int(args.window_days))
        since_dt = now - timedelta(days=window_days)
    since_utc = since_dt.astimezone(timezone.utc).replace(tzinfo=None)
    return since_utc.strftime("%Y-%m-%d %H:%M:%S"), since_dt, window_days


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    since_sqlite, since_dt, window_days = _resolve_window(args)

    results = [
        check_audit_session_count(args.reports_db, since=since_sqlite),
        check_orphan_audit_rows(args.history_db, args.reports_db),
        check_pause_trigger_count(
            args.jsonl, since_ts=since_dt, window_days=window_days,
        ),
    ]
    all_passed = all(r.passed for r in results)

    if args.json:
        out = {
            "passed": all_passed,
            "window": {
                "since": since_dt.isoformat(),
                "days": window_days,
            },
            "checks": [r.to_dict() for r in results],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(
            f"ADR-006 bake metrics — window: {window_days}d "
            f"(since {since_dt.isoformat()})",
        )
        print("=" * 64)
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {r.name}: {r.actual} (threshold {r.threshold})")
            if r.detail:
                print(f"  {r.detail}")
            for s in r.samples:
                print(f"  sample: {s}")
        print("=" * 64)
        print("RESULT:", "PASS" if all_passed else "FAIL")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
