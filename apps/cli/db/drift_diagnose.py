"""Diagnose and fix pending-write drift between D1 and local SQLite.

Discovers suspect sessions (committed but still carrying orphan
``Pending*HistoryWrites`` rows) and classifies each as CLEAN,
SAFE_TO_APPLY, ESCALATE_LIVE_DIVERGENCE, or UNEXPECTED_PATTERN.

This implements ADR-009 steps D2–D5:

* **Diagnose mode** (default, read-only): D3 suspect discovery + D4 verdict
  classification.
* **Apply mode** (``--apply --session-id <id>``): D5 safe deletion of orphan
  pending rows for a single committed session, guarded by five safety rails.

Exit codes (diagnose mode)
--------------------------
* 0 — all suspects classified CLEAN (or no suspects found)
* 1 — at least one SAFE_TO_APPLY (auto-fixable, but action required)
* 2 — ESCALATE_LIVE_DIVERGENCE or UNEXPECTED_PATTERN detected

Exit codes (apply mode)
-----------------------
* 0 — apply succeeded
* 1 — verdict was not SAFE_TO_APPLY (safety rail 2)
* 2 — argument error, session not committed (rail 3), or exceeds --max-deletes (rail 4)

Usage
-----
::

    python3 -m apps.cli.db.drift_diagnose                    # default 24h window
    python3 -m apps.cli.db.drift_diagnose --since 48         # 48h lookback
    python3 -m apps.cli.db.drift_diagnose --json             # JSON output
    python3 -m apps.cli.db.drift_diagnose --log-level DEBUG  # verbose logging
    python3 -m apps.cli.db.drift_diagnose --apply --session-id <id>  # fix orphans
    python3 -m apps.cli.db.drift_diagnose --apply --session-id <id> --max-deletes 50
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.infra.config import cfg  # noqa: E402
from javdb.infra.logging import get_logger, setup_logging  # noqa: E402
from javdb.storage.d1_client import make_d1_connection  # noqa: E402
from javdb.storage.rollback.session_helpers import append_jsonl_record  # noqa: E402

logger = get_logger(__name__)

# ── Defaults / paths ──────────────────────────────────────────────────────

_REPORTS_DIR = cfg("REPORTS_DIR", "reports")

# ── Verdict constants ────────────────────────────────────────────────────

VERDICT_CLEAN = "CLEAN"
VERDICT_SAFE_TO_APPLY = "SAFE_TO_APPLY"
VERDICT_ESCALATE = "ESCALATE_LIVE_DIVERGENCE"
VERDICT_UNEXPECTED = "UNEXPECTED_PATTERN"

_VERDICT_EXIT_CODE = {
    VERDICT_CLEAN: 0,
    VERDICT_SAFE_TO_APPLY: 1,
    VERDICT_ESCALATE: 2,
    VERDICT_UNEXPECTED: 2,
}

# Columns to compare when checking live-table equivalence between D1 and
# SQLite.  Intentionally excludes ``Id`` (AUTOINCREMENT, drifts by design)
# and ``DateTimeUpdated`` (may differ by milliseconds across backends).
_MOVIE_COMPARE_COLS = ("Href", "VideoCode", "ActorName", "DateTimeCreated")
_TORRENT_COMPARE_COLS = (
    "MagnetUri", "Size", "DateTimeCreated",
)


# ── Timestamp helpers ────────────────────────────────────────────────────


def _parse_ts(raw) -> Optional[datetime]:
    """Parse ISO 8601 timestamps including trailing ``Z``."""
    if not raw:
        return None
    s = str(raw)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── Cell-level comparison ────────────────────────────────────────────────


def _values_equal(a, b) -> bool:
    """Type-loose cell equality (mirrors reconcile_d1_drift._values_equal)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


# ── JSONL reader ─────────────────────────────────────────────────────────


def _read_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    records: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


# ── SQLite helpers ───────────────────────────────────────────────────────


def _open_sqlite_readonly(db_path: str) -> sqlite3.Connection:
    """Open *db_path* read-only."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row)


# ── D3: Suspect discovery — verify-metric path ──────────────────────────


def discover_suspects_from_verify_log(
    drift_log_path: str,
    since_hours: float,
) -> Dict[str, dict]:
    """Read ``d1_drift.jsonl`` and extract sessions with residual pending rows.

    Returns ``{session_id: {pending_residual_count, ts}}`` for records with
    ``kind == "pending_session_verify"`` and ``pending_residual_count > 0``
    within the lookback window.
    """
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(hours=since_hours)

    records = _read_jsonl(drift_log_path)
    suspects: Dict[str, dict] = {}

    for rec in records:
        if rec.get("kind") != "pending_session_verify":
            continue
        ts = _parse_ts(rec.get("ts"))
        if ts is None or ts < window_start:
            continue
        residual = int(rec.get("pending_residual_count", 0))
        if residual <= 0:
            continue
        session_id = rec.get("session_id", "")
        if not session_id:
            continue
        # Keep the highest residual count if multiple records exist
        existing = suspects.get(session_id)
        if existing is None or residual > existing["pending_residual_count"]:
            suspects[session_id] = {
                "pending_residual_count": residual,
                "ts": rec.get("ts"),
            }

    return suspects


# ── D3: Suspect discovery — D1 sweep path ───────────────────────────────


def discover_suspects_from_d1_sweep(
    d1_reports,
    d1_history,
    since_hours: float,
) -> Dict[str, dict]:
    """Query D1 for committed sessions with orphan pending rows.

    Returns ``{session_id: {d1_pending_movie_count, d1_pending_torrent_count}}``
    for sessions that have any non-zero pending row count.
    """
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(hours=since_hours)
    window_start_text = window_start.strftime("%Y-%m-%d %H:%M:%S")

    # Fetch committed sessions within the window
    try:
        cur = d1_reports.execute(
            "SELECT Id, Status, DateTimeCreated FROM ReportSessions "
            "WHERE Status = 'committed' AND DateTimeCreated >= ?",
            [window_start_text],
        )
        sessions = cur.fetchall()
    except Exception as exc:
        logger.warning("D1 sweep: failed to query ReportSessions: %s", exc)
        return {}

    suspects: Dict[str, dict] = {}

    for session_row in sessions:
        if isinstance(session_row, dict):
            session_id = session_row.get("Id", "")
        else:
            session_id = session_row["Id"]

        if not session_id:
            continue

        # Count orphan pending rows for this session
        movie_count = 0
        torrent_count = 0
        try:
            cur = d1_history.execute(
                "SELECT COUNT(*) AS cnt FROM PendingMovieHistoryWrites "
                "WHERE SessionId = ?",
                [session_id],
            )
            row = cur.fetchone()
            if row:
                movie_count = int(row.get("cnt", 0) if isinstance(row, dict) else row["cnt"])
        except Exception as exc:
            logger.warning("D1 sweep: PendingMovieHistoryWrites query failed for %s: %s",
                           session_id, exc)

        try:
            cur = d1_history.execute(
                "SELECT COUNT(*) AS cnt FROM PendingTorrentHistoryWrites "
                "WHERE SessionId = ?",
                [session_id],
            )
            row = cur.fetchone()
            if row:
                torrent_count = int(row.get("cnt", 0) if isinstance(row, dict) else row["cnt"])
        except Exception as exc:
            logger.warning("D1 sweep: PendingTorrentHistoryWrites query failed for %s: %s",
                           session_id, exc)

        if movie_count > 0 or torrent_count > 0:
            suspects[session_id] = {
                "d1_pending_movie_count": movie_count,
                "d1_pending_torrent_count": torrent_count,
            }

    return suspects


# ── D3: Merge suspects ──────────────────────────────────────────────────


def merge_suspects(
    verify_suspects: Dict[str, dict],
    sweep_suspects: Dict[str, dict],
) -> List[dict]:
    """Union both signal sources and tag each with provenance."""
    all_session_ids = set(verify_suspects) | set(sweep_suspects)
    merged: List[dict] = []

    for sid in sorted(all_session_ids):
        in_verify = sid in verify_suspects
        in_sweep = sid in sweep_suspects
        if in_verify and in_sweep:
            provenance = "both"
        elif in_verify:
            provenance = "verify-tagged"
        else:
            provenance = "sweep-only"

        entry: dict = {
            "session_id": sid,
            "provenance": provenance,
        }
        if in_verify:
            entry.update(verify_suspects[sid])
        if in_sweep:
            entry.update(sweep_suspects[sid])

        merged.append(entry)

    return merged


# ── D4: Verdict classification ──────────────────────────────────────────


def classify_verdict(
    suspect: dict,
    d1_history,
    *,
    d1_reports=None,
    sqlite_conn: Optional[sqlite3.Connection],
) -> dict:
    """Classify a single suspect session into a verdict.

    Returns a dict with ``verdict``, ``d1_orphan_movie_count``,
    ``d1_orphan_torrent_count``, and optionally ``suggested_command``
    or ``note``.

    When *d1_reports* is provided, the session's ``Status`` in
    ``ReportSessions`` is checked first.  If the status is not
    ``'committed'``, the session is immediately classified as
    ``UNEXPECTED_PATTERN`` (ADR-009 D4).
    """
    session_id = suspect["session_id"]
    result: dict = {
        "session_id": session_id,
        "provenance": suspect["provenance"],
        "d1_orphan_movie_count": 0,
        "d1_orphan_torrent_count": 0,
    }

    # ADR-009 D4: reject sessions that are not committed
    if d1_reports is not None:
        try:
            cur = d1_reports.execute(
                "SELECT Status FROM ReportSessions WHERE Id = ?",
                [session_id],
            )
            status_row = cur.fetchone()
            if status_row is not None:
                status = (
                    status_row.get("Status", "")
                    if isinstance(status_row, dict)
                    else status_row["Status"]
                )
                if status != "committed":
                    result["verdict"] = VERDICT_UNEXPECTED
                    result["note"] = (
                        f"Session status is '{status}', expected 'committed'"
                    )
                    return result
        except Exception as exc:
            logger.warning(
                "classify: ReportSessions query failed for %s: %s",
                session_id, exc,
            )

    # Fetch actual orphan pending rows from D1 for this session
    d1_orphan_movies: List[dict] = []
    d1_orphan_torrents: List[dict] = []

    try:
        cur = d1_history.execute(
            "SELECT * FROM PendingMovieHistoryWrites WHERE SessionId = ?",
            [session_id],
        )
        d1_orphan_movies = cur.fetchall()
    except Exception as exc:
        logger.warning("classify: PendingMovieHistoryWrites query failed for %s: %s",
                       session_id, exc)

    try:
        cur = d1_history.execute(
            "SELECT * FROM PendingTorrentHistoryWrites WHERE SessionId = ?",
            [session_id],
        )
        d1_orphan_torrents = cur.fetchall()
    except Exception as exc:
        logger.warning("classify: PendingTorrentHistoryWrites query failed for %s: %s",
                       session_id, exc)

    result["d1_orphan_movie_count"] = len(d1_orphan_movies)
    result["d1_orphan_torrent_count"] = len(d1_orphan_torrents)

    # No orphans at all → CLEAN (false alarm from verify log)
    if not d1_orphan_movies and not d1_orphan_torrents:
        result["verdict"] = VERDICT_CLEAN
        return result

    # Orphans exist but no SQLite connection → can't compare → UNEXPECTED
    if sqlite_conn is None:
        result["verdict"] = VERDICT_UNEXPECTED
        result["note"] = (
            "D1 orphans found but SQLite unavailable (d1-only mode); "
            "cannot verify live-table equivalence"
        )
        return result

    # Check SQLite-side pending tables for this session
    sqlite_has_orphans = False
    try:
        row = sqlite_conn.execute(
            "SELECT COUNT(*) AS cnt FROM PendingMovieHistoryWrites "
            "WHERE SessionId = ?",
            [session_id],
        ).fetchone()
        if row and (row["cnt"] if isinstance(row, sqlite3.Row) else row.get("cnt", 0)) > 0:
            sqlite_has_orphans = True
    except sqlite3.OperationalError:
        pass

    if not sqlite_has_orphans:
        try:
            row = sqlite_conn.execute(
                "SELECT COUNT(*) AS cnt FROM PendingTorrentHistoryWrites "
                "WHERE SessionId = ?",
                [session_id],
            ).fetchone()
            if row and (row["cnt"] if isinstance(row, sqlite3.Row) else row.get("cnt", 0)) > 0:
                sqlite_has_orphans = True
        except sqlite3.OperationalError:
            pass

    if sqlite_has_orphans:
        result["verdict"] = VERDICT_UNEXPECTED
        result["note"] = (
            "Both D1 and SQLite have orphan pending rows for this session"
        )
        return result

    # D1-only orphan — compare live tables for the affected Hrefs
    orphan_hrefs = set()
    for row in d1_orphan_movies:
        href = row.get("Href", "") if isinstance(row, dict) else row["Href"]
        if href:
            orphan_hrefs.add(href)
    for row in d1_orphan_torrents:
        href = row.get("Href", "") if isinstance(row, dict) else row["Href"]
        if href:
            orphan_hrefs.add(href)

    live_diverges = False
    for href in orphan_hrefs:
        if _live_rows_diverge(href, d1_history, sqlite_conn):
            live_diverges = True
            break

    if live_diverges:
        result["verdict"] = VERDICT_ESCALATE
        return result

    # D1 orphan, SQLite clean, live tables match → safe to clean up
    result["verdict"] = VERDICT_SAFE_TO_APPLY
    result["suggested_command"] = (
        f"python3 -m apps.cli.db.drift_diagnose --apply "
        f"--session-id {session_id}"
    )
    return result


def _live_rows_diverge(
    href: str,
    d1_history,
    sqlite_conn: sqlite3.Connection,
) -> bool:
    """Compare MovieHistory + TorrentHistory rows for *href* on both sides.

    Returns True if any compared column differs.
    """
    # Fetch D1 side
    try:
        cur = d1_history.execute(
            "SELECT * FROM MovieHistory WHERE Href = ?", [href],
        )
        d1_movie = cur.fetchone()
    except Exception as exc:
        logger.warning("live_rows_diverge: D1 MovieHistory query failed for %s: %s", href, exc)
        return True  # Can't compare → treat as divergence

    # Fetch SQLite side
    try:
        sqlite_row = sqlite_conn.execute(
            "SELECT * FROM MovieHistory WHERE Href = ?", (href,),
        ).fetchone()
        sqlite_movie = _row_to_dict(sqlite_row) if sqlite_row else None
    except sqlite3.OperationalError:
        return True

    d1_movie_dict = d1_movie if isinstance(d1_movie, dict) else (
        _row_to_dict(d1_movie) if d1_movie else None
    )

    # Both missing is fine (movie deleted on both sides)
    if d1_movie_dict is None and sqlite_movie is None:
        return False
    # One side missing → divergence
    if d1_movie_dict is None or sqlite_movie is None:
        return True

    # Compare columns
    for col in _MOVIE_COMPARE_COLS:
        if not _values_equal(d1_movie_dict.get(col), sqlite_movie.get(col)):
            return True

    # Compare torrent children
    d1_movie_id = d1_movie_dict.get("Id")
    sqlite_movie_id = sqlite_movie.get("Id")

    d1_torrents: List[dict] = []
    if d1_movie_id is not None:
        try:
            cur = d1_history.execute(
                "SELECT * FROM TorrentHistory WHERE MovieHistoryId = ?",
                [d1_movie_id],
            )
            d1_torrents = cur.fetchall()
        except Exception as exc:
            logger.warning("live_rows_diverge: D1 TorrentHistory query failed for movie %s: %s", d1_movie_id, exc)
            return True

    sqlite_torrents: List[dict] = []
    if sqlite_movie_id is not None:
        try:
            rows = sqlite_conn.execute(
                "SELECT * FROM TorrentHistory WHERE MovieHistoryId = ?",
                (sqlite_movie_id,),
            ).fetchall()
            sqlite_torrents = [_row_to_dict(r) for r in rows]
        except sqlite3.OperationalError:
            return True

    # Compare by natural key (SubtitleIndicator, CensorIndicator)
    def _torrent_key(t: dict):
        return (
            t.get("SubtitleIndicator", 0),
            t.get("CensorIndicator", 0),
        )

    d1_by_key = {_torrent_key(t): t for t in d1_torrents}
    sqlite_by_key = {_torrent_key(t): t for t in sqlite_torrents}

    if set(d1_by_key.keys()) != set(sqlite_by_key.keys()):
        return True

    for key in d1_by_key:
        d1_t = d1_by_key[key]
        sq_t = sqlite_by_key[key]
        for col in _TORRENT_COMPARE_COLS:
            if not _values_equal(d1_t.get(col), sq_t.get(col)):
                return True

    return False


# ── Output formatting ────────────────────────────────────────────────────


def compute_exit_code(results: List[dict]) -> int:
    """Compute the process exit code as max(verdict codes)."""
    if not results:
        return 0
    return max(_VERDICT_EXIT_CODE.get(r.get("verdict", ""), 0) for r in results)


def format_output(results: List[dict], *, as_json: bool) -> str:
    """Format results for display."""
    verdicts = [r.get("verdict", VERDICT_CLEAN) for r in results]
    max_verdict = VERDICT_CLEAN
    max_code = 0
    for v in verdicts:
        code = _VERDICT_EXIT_CODE.get(v, 0)
        if code > max_code:
            max_code = code
            max_verdict = v

    if as_json:
        output = {
            "suspects": results,
            "max_verdict": max_verdict,
        }
        return json.dumps(output, indent=2, ensure_ascii=False)

    # Text output
    lines: List[str] = []
    if not results:
        lines.append("No suspect sessions found.")
    else:
        lines.append(f"Found {len(results)} suspect session(s):")
        lines.append("")
        for r in results:
            lines.append(f"  Session: {r['session_id']}")
            lines.append(f"    Provenance:          {r['provenance']}")
            lines.append(f"    Verdict:             {r['verdict']}")
            lines.append(
                f"    D1 orphan movies:    {r.get('d1_orphan_movie_count', 0)}"
            )
            lines.append(
                f"    D1 orphan torrents:  {r.get('d1_orphan_torrent_count', 0)}"
            )
            if "suggested_command" in r:
                lines.append(f"    Suggested fix:       {r['suggested_command']}")
            if "note" in r:
                lines.append(f"    Note:                {r['note']}")
            lines.append("")
        lines.append(f"Max verdict: {max_verdict}")

    return "\n".join(lines)


# ── Core diagnose orchestration ──────────────────────────────────────────


def diagnose(
    *,
    drift_log_path: str,
    since_hours: float,
    sqlite_history_path: Optional[str],
) -> tuple:
    """Run the full D3+D4 diagnose flow.

    Returns ``(results, exit_code)`` where *results* is a list of per-suspect
    verdict dicts and *exit_code* follows the ADR-009 convention.
    """
    # D3: Discover suspects from verify log
    verify_suspects = discover_suspects_from_verify_log(
        drift_log_path, since_hours,
    )
    logger.info(
        "Verify-metric path: %d suspect(s) from drift log", len(verify_suspects),
    )

    # D3: Discover suspects from D1 sweep
    d1_reports = make_d1_connection("reports")
    d1_history = make_d1_connection("history")
    try:
        sweep_suspects = discover_suspects_from_d1_sweep(
            d1_reports, d1_history, since_hours,
        )
    except Exception:
        d1_reports.close()
        d1_history.close()
        raise
    logger.info(
        "D1-sweep path: %d suspect(s) from committed sessions", len(sweep_suspects),
    )

    # D3: Merge both signals
    merged = merge_suspects(verify_suspects, sweep_suspects)
    if not merged:
        logger.info("No suspect sessions found")
        d1_reports.close()
        d1_history.close()
        return [], 0

    logger.info("Total unique suspect sessions: %d", len(merged))

    # D4: Classify each suspect
    sqlite_conn = None
    if sqlite_history_path and os.path.exists(sqlite_history_path):
        try:
            sqlite_conn = _open_sqlite_readonly(sqlite_history_path)
        except Exception as exc:
            logger.warning(
                "Could not open SQLite history (%s): %s — "
                "live comparison will be skipped",
                sqlite_history_path, exc,
            )

    results: List[dict] = []
    try:
        for suspect in merged:
            verdict_result = classify_verdict(
                suspect, d1_history,
                d1_reports=d1_reports, sqlite_conn=sqlite_conn,
            )
            results.append(verdict_result)
            logger.info(
                "Session %s: verdict=%s (provenance=%s, orphans=%d+%d)",
                verdict_result["session_id"],
                verdict_result["verdict"],
                verdict_result["provenance"],
                verdict_result.get("d1_orphan_movie_count", 0),
                verdict_result.get("d1_orphan_torrent_count", 0),
            )
    finally:
        d1_reports.close()
        d1_history.close()
        if sqlite_conn is not None:
            sqlite_conn.close()

    exit_code = compute_exit_code(results)
    return results, exit_code


# ── D5: Apply fix (delete orphan pending rows) ─────────────────────────


def apply_fix(
    *,
    session_id: str,
    sqlite_history_path: Optional[str],
    max_deletes: int = 100,
) -> int:
    """Delete orphan pending rows for a single committed session.

    Implements ADR-009 D5 with five safety rails:

    1. ``session_id`` must be provided (enforced by caller / argparse).
    2. Verdict must be ``SAFE_TO_APPLY`` at apply time.
    3. Session must have ``Status='committed'`` in ``ReportSessions``.
    4. Total orphan count must be ≤ *max_deletes*.
    5. DELETE SQL must include both ``SessionId=?`` and
       ``ApplyState='pending'`` predicates.

    Returns exit code: 0 = success, 1 = verdict not SAFE_TO_APPLY,
    2 = session not committed or orphan count exceeds max_deletes.
    """
    d1_reports = make_d1_connection("reports")
    d1_history = make_d1_connection("history")
    try:
        return _apply_fix_inner(
            session_id=session_id,
            d1_reports=d1_reports,
            d1_history=d1_history,
            sqlite_history_path=sqlite_history_path,
            max_deletes=max_deletes,
        )
    finally:
        d1_reports.close()
        d1_history.close()


def _apply_fix_inner(
    *,
    session_id: str,
    d1_reports,
    d1_history,
    sqlite_history_path: Optional[str],
    max_deletes: int,
) -> int:
    """Inner implementation of apply_fix with injected connections."""

    # ── Step 1: Re-run classification (safety rail 2) ─────────────────
    sqlite_conn = None
    if sqlite_history_path and os.path.exists(sqlite_history_path):
        try:
            sqlite_conn = _open_sqlite_readonly(sqlite_history_path)
        except Exception as exc:
            logger.warning(
                "apply_fix: could not open SQLite history (%s): %s",
                sqlite_history_path, exc,
            )

    suspect = {"session_id": session_id, "provenance": "apply-target"}
    try:
        verdict_result = classify_verdict(
            suspect, d1_history,
            d1_reports=d1_reports, sqlite_conn=sqlite_conn,
        )
    finally:
        if sqlite_conn is not None:
            sqlite_conn.close()

    verdict = verdict_result.get("verdict", "")
    movie_orphan_count = verdict_result.get("d1_orphan_movie_count", 0)
    torrent_orphan_count = verdict_result.get("d1_orphan_torrent_count", 0)
    total_orphans = movie_orphan_count + torrent_orphan_count

    logger.info(
        "apply_fix: session=%s verdict=%s orphans=%d+%d",
        session_id, verdict, movie_orphan_count, torrent_orphan_count,
    )

    # ── Safety rail 3: session must be committed ──────────────────────
    # classify_verdict already returns UNEXPECTED_PATTERN for non-committed
    # sessions, but we check explicitly so we can emit the right exit code.
    if verdict == VERDICT_UNEXPECTED:
        note = verdict_result.get("note", "")
        if "expected 'committed'" in note:
            logger.error(
                "SAFETY_RAIL_3_SESSION_NOT_COMMITTED: session=%s note=%s",
                session_id, note,
            )
            return 2

    # ── Safety rail 2: verdict must be SAFE_TO_APPLY ──────────────────
    if verdict != VERDICT_SAFE_TO_APPLY:
        logger.error(
            "SAFETY_RAIL_2_VERDICT_NOT_SAFE: session=%s verdict=%s",
            session_id, verdict,
        )
        return 1

    # ── Safety rail 4: orphan count ≤ max_deletes ─────────────────────
    if total_orphans > max_deletes:
        logger.error(
            "SAFETY_RAIL_4_EXCEEDS_MAX_DELETES: session=%s "
            "orphans=%d max_deletes=%d",
            session_id, total_orphans, max_deletes,
        )
        return 2

    # ── Safety rail 5: DELETE with SessionId + ApplyState predicates ──
    logger.info(
        "apply_fix: proceeding with DELETE for session=%s "
        "(movie_orphans=%d, torrent_orphans=%d)",
        session_id, movie_orphan_count, torrent_orphan_count,
    )

    cur = d1_history.execute(
        "DELETE FROM PendingMovieHistoryWrites "
        "WHERE SessionId = ? AND ApplyState = 'pending'",
        [session_id],
    )
    deleted_movies = cur.rowcount

    cur = d1_history.execute(
        "DELETE FROM PendingTorrentHistoryWrites "
        "WHERE SessionId = ? AND ApplyState = 'pending'",
        [session_id],
    )
    deleted_torrents = cur.rowcount

    logger.info(
        "apply_fix: deleted %d movie orphans, %d torrent orphans "
        "for session=%s",
        deleted_movies, deleted_torrents, session_id,
    )

    # ── Audit record ──────────────────────────────────────────────────
    record = {
        "kind": "drift_resolution",
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session_id,
        "source": "drift_diagnose_apply",
        "deleted_movie_orphans": deleted_movies,
        "deleted_torrent_orphans": deleted_torrents,
        "verdict_at_apply": VERDICT_SAFE_TO_APPLY,
    }
    append_jsonl_record(record)

    print(
        f"Applied: deleted {deleted_movies} movie orphan(s) and "
        f"{deleted_torrents} torrent orphan(s) for session {session_id}."
    )
    return 0


# ── Argument parsing ─────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.db.drift_diagnose",
        description=(
            "Diagnose pending-write drift between D1 and local SQLite. "
            "Read-only by default; use --apply --session-id to fix orphans."
        ),
    )

    # ── Diagnose options ─────────────────────────────────────────────
    diag = parser.add_argument_group("diagnose options")
    diag.add_argument(
        "--since",
        type=float,
        default=24.0,
        metavar="HOURS",
        help="Look-back window in hours (default: 24).",
    )
    diag.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON for programmatic consumption.",
    )
    diag.add_argument(
        "--drift-log",
        default=None,
        help=(
            "Path to d1_drift.jsonl. Defaults to "
            "$REPORTS_DIR/D1/d1_drift.jsonl."
        ),
    )
    diag.add_argument(
        "--history-db",
        default=None,
        help=(
            "Path to local history.db for live-table comparison. "
            "Defaults to $REPORTS_DIR/history.db. "
            "Omit or set empty to skip SQLite comparison."
        ),
    )

    # ── Apply options (ADR-009 D5) ────────────────────────────────────
    apply_group = parser.add_argument_group("apply options")
    apply_group.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Delete orphan Pending*HistoryWrites rows for a committed "
            "session whose verdict is SAFE_TO_APPLY. Requires --session-id."
        ),
    )
    apply_group.add_argument(
        "--session-id",
        default=None,
        metavar="SESSION_ID",
        help="Target session for --apply.",
    )
    apply_group.add_argument(
        "--max-deletes",
        type=int,
        default=100,
        metavar="N",
        help=(
            "Maximum total orphan rows allowed for --apply (default: 100). "
            "Protects against accidental bulk DELETEs."
        ),
    )

    # ── General options ──────────────────────────────────────────────
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default: INFO).",
    )

    return parser


# ── CLI main ─────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    setup_logging(log_level=args.log_level)

    if args.apply:
        # Safety rail 1: --apply requires --session-id
        if not args.session_id:
            print(
                "ERROR: --apply requires --session-id. "
                "Specify the session to fix.",
                file=sys.stderr,
            )
            return 2

        reports_dir = os.environ.get("REPORTS_DIR", "reports")
        history_db_path = args.history_db
        if history_db_path is None:
            history_db_path = os.path.join(reports_dir, "history.db")

        return apply_fix(
            session_id=args.session_id,
            sqlite_history_path=history_db_path,
            max_deletes=args.max_deletes,
        )

    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    drift_log_path = args.drift_log or os.path.join(
        reports_dir, "D1", "d1_drift.jsonl",
    )
    history_db_path = args.history_db
    if history_db_path is None:
        history_db_path = os.path.join(reports_dir, "history.db")

    results, exit_code = diagnose(
        drift_log_path=drift_log_path,
        since_hours=args.since,
        sqlite_history_path=history_db_path,
    )

    output = format_output(results, as_json=args.json_output)
    print(output)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
