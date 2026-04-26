"""Reconcile Cloudflare D1 against local SQLite using business keys.

Background
----------
The dual-write path in :mod:`packages.python.javdb_platform.dual_connection`
applies every write to the local SQLite file as the canonical source of truth
and mirrors it to D1 best-effort. When D1 misses a write (timeout, transient
error, long-running export lock, etc.) it appends a structured record to
``reports/d1_drift.jsonl`` and lets the pipeline continue.

This tool consumes that drift log and re-syncs D1 from SQLite. It does NOT
replay the failed SQL verbatim — replay is unsafe because:

* The drift log only stores the first failed SQL of each transaction.
* AUTOINCREMENT IDs already drift between SQLite and D1, so any INSERT carrying
  a SQLite-side ``lastrowid`` would pollute D1 further.

Instead the reconciler walks each affected table and copies SQLite rows into
D1 keyed by **business identity** (e.g. ``MovieHistory.Href``,
``ReportSessions.CsvFilename``), translating parent IDs through D1's own
identity map. The operation is idempotent: rows that already match are left
untouched.

Usage
-----
::

    python -m migration.tools.reconcile_d1_drift              # all dbs, since=earliest jsonl ts
    python -m migration.tools.reconcile_d1_drift --db history # one db only
    python -m migration.tools.reconcile_d1_drift --dry-run    # report only, no writes
    python -m migration.tools.reconcile_d1_drift --since 2026-04-25T00:00:00Z
    python -m migration.tools.reconcile_d1_drift --all-rows   # full scan, ignore --since

After a successful pass the consumed jsonl lines are appended to
``reports/d1_drift.processed.jsonl`` and removed from the live drift log.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.python.javdb_platform.config_helper import cfg  # noqa: E402
from packages.python.javdb_platform.d1_client import (  # noqa: E402
    D1Connection,
    D1Error,
    make_d1_connection,
)
from packages.python.javdb_platform.logging_config import (  # noqa: E402
    get_logger,
    setup_logging,
)

logger = get_logger(__name__)


# ── Defaults / paths ──────────────────────────────────────────────────────

_REPORTS_DIR = cfg("REPORTS_DIR", "reports")
_DEFAULT_DRIFT_LOG = os.path.join(_REPORTS_DIR, "d1_drift.jsonl")
_DEFAULT_PROCESSED_LOG = os.path.join(_REPORTS_DIR, "d1_drift.processed.jsonl")

_LOGICAL_TO_DB_PATH = {
    "history": cfg("HISTORY_DB_PATH", os.path.join(_REPORTS_DIR, "history.db")),
    "reports": cfg("REPORTS_DB_PATH", os.path.join(_REPORTS_DIR, "reports.db")),
    "operations": cfg("OPERATIONS_DB_PATH", os.path.join(_REPORTS_DIR, "operations.db")),
}


# ── Stats per table ───────────────────────────────────────────────────────


@dataclass
class TableStats:
    table: str
    checked: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_equal: int = 0
    skipped_missing_parent: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)

    def as_summary(self) -> str:
        return (
            f"{self.table:<24} checked={self.checked:<5} inserted={self.inserted:<4} "
            f"updated={self.updated:<4} unchanged={self.skipped_equal:<4} "
            f"orphans={self.skipped_missing_parent:<3} errors={self.errors}"
        )


# ── Drift log parsing ─────────────────────────────────────────────────────


def _parse_iso8601(ts: str) -> Optional[datetime]:
    """Parse ISO 8601 timestamps including the trailing ``Z`` UTC suffix."""
    if not ts:
        return None
    candidate = ts.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _datetime_to_sqlite_text(ts: datetime) -> str:
    """Render a datetime in the canonical SQLite text format used by JAVDB.

    All ``DateTime*`` columns store ``"%Y-%m-%d %H:%M:%S"`` strings (UTC by
    convention). The reconciler uses this format for ``WHERE col >= ?``
    filters since direct datetime comparison on TEXT is lexicographic.
    """
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _read_drift_log(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    records: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed drift line %r: %s", line[:80], exc)
    return records


def _earliest_since_per_db(records: Iterable[dict]) -> Dict[str, datetime]:
    """Group drift records by ``db`` and return the earliest ``ts`` per db."""
    out: Dict[str, datetime] = {}
    for rec in records:
        db = rec.get("db")
        if not isinstance(db, str):
            continue
        ts = _parse_iso8601(rec.get("ts", ""))
        if ts is None:
            continue
        existing = out.get(db)
        if existing is None or ts < existing:
            out[db] = ts
    return out


# ── SQLite helpers ────────────────────────────────────────────────────────


def _open_sqlite_readonly(db_path: str) -> sqlite3.Connection:
    """Open *db_path* read-only so the running spider isn't disturbed."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
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


def _values_equal(a, b) -> bool:
    """Row-cell equality with type-loose comparison.

    SQLite returns Python ints / floats / str / None; D1's HTTP API returns
    JSON-decoded values which may swap int/float. Compare strings stripped,
    and numeric values via ``float()`` to absorb that drift.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return str(a) == str(b)


def _rows_match(d1_row: dict, sqlite_row: dict, columns: Sequence[str]) -> bool:
    for col in columns:
        if not _values_equal(d1_row.get(col), sqlite_row.get(col)):
            return False
    return True


# ── D1 identity-map helpers ───────────────────────────────────────────────


class _D1IdResolver:
    """Cache lookups of D1 row IDs by business key.

    Each (table, key_cols, key_values) tuple is hit at most once per run.
    """

    def __init__(self, d1_conn: D1Connection):
        self._d1 = d1_conn
        self._cache: Dict[Tuple[str, Tuple[str, ...], Tuple[Any, ...]], Optional[int]] = {}

    def get(self, table: str, key_cols: Sequence[str], key_values: Sequence[Any]) -> Optional[int]:
        cache_key = (table, tuple(key_cols), tuple(key_values))
        if cache_key in self._cache:
            return self._cache[cache_key]
        where = " AND ".join(f"{c} = ?" for c in key_cols)
        sql = f"SELECT Id FROM {table} WHERE {where} LIMIT 1"
        try:
            row = self._d1.execute(sql, list(key_values)).fetchone()
        except D1Error as exc:
            logger.warning("D1 lookup %s by %s failed: %s", table, key_cols, exc)
            self._cache[cache_key] = None
            return None
        d1_id = row.get("Id") if row else None
        self._cache[cache_key] = d1_id
        return d1_id


# ── Generic upsert engine ─────────────────────────────────────────────────


def _upsert_by_business_key(
    *,
    d1: D1Connection,
    table: str,
    key_cols: Sequence[str],
    payload_cols: Sequence[str],
    sqlite_row_values: Dict[str, Any],
    stats: TableStats,
    dry_run: bool,
) -> None:
    """Upsert one row into D1, keyed by *key_cols*; write *payload_cols*.

    Algorithm: SELECT-by-key. If missing, INSERT. If present and any payload
    column differs, UPDATE. Otherwise no-op.

    Unlike SQL ``ON CONFLICT``, this works on tables that have no UNIQUE
    constraint declared on the business key (most reports.db tables don't).
    """
    stats.checked += 1
    key_values = [sqlite_row_values[c] for c in key_cols]

    where = " AND ".join(f"{c} = ?" for c in key_cols)
    select_sql = f"SELECT Id, {', '.join(payload_cols)} FROM {table} WHERE {where} LIMIT 1"
    try:
        existing = d1.execute(select_sql, key_values).fetchone()
    except D1Error as exc:
        stats.errors += 1
        msg = f"SELECT {table} by {key_cols}={key_values} failed: {exc}"
        stats.error_messages.append(msg)
        logger.warning(msg)
        return

    payload_values = [sqlite_row_values[c] for c in payload_cols]

    if existing is None:
        if dry_run:
            stats.inserted += 1
            return
        all_cols = list(key_cols) + list(payload_cols)
        all_vals = list(key_values) + list(payload_values)
        placeholders = ", ".join("?" for _ in all_cols)
        insert_sql = f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders})"
        try:
            d1.execute(insert_sql, all_vals)
            stats.inserted += 1
        except D1Error as exc:
            stats.errors += 1
            msg = f"INSERT {table} by {key_cols}={key_values} failed: {exc}"
            stats.error_messages.append(msg)
            logger.warning(msg)
        return

    existing_dict = _row_to_dict(existing)
    if _rows_match(existing_dict, sqlite_row_values, payload_cols):
        stats.skipped_equal += 1
        return

    if dry_run:
        stats.updated += 1
        return
    set_clause = ", ".join(f"{c} = ?" for c in payload_cols)
    update_sql = f"UPDATE {table} SET {set_clause} WHERE Id = ?"
    try:
        d1.execute(update_sql, list(payload_values) + [existing_dict["Id"]])
        stats.updated += 1
    except D1Error as exc:
        stats.errors += 1
        msg = f"UPDATE {table} Id={existing_dict.get('Id')} failed: {exc}"
        stats.error_messages.append(msg)
        logger.warning(msg)


# ── Per-database reconcilers ──────────────────────────────────────────────


_MOVIE_HISTORY_KEY = ("Href",)
_MOVIE_HISTORY_PAYLOAD = (
    "VideoCode",
    "ActorName",
    "ActorGender",
    "ActorLink",
    "SupportingActors",
    "DateTimeCreated",
    "DateTimeUpdated",
    "DateTimeVisited",
    "PerfectMatchIndicator",
    "HiResIndicator",
)


_TORRENT_HISTORY_KEY = ("MovieHistoryId", "SubtitleIndicator", "CensorIndicator")
_TORRENT_HISTORY_PAYLOAD = (
    "MagnetUri",
    "ResolutionType",
    "Size",
    "FileCount",
    "DateTimeCreated",
    "DateTimeUpdated",
)


_REPORT_SESSIONS_KEY = ("CsvFilename",)
_REPORT_SESSIONS_PAYLOAD = (
    "ReportType",
    "ReportDate",
    "UrlType",
    "DisplayName",
    "Url",
    "StartPage",
    "EndPage",
    "DateTimeCreated",
)


_REPORT_MOVIES_KEY = ("SessionId", "Href")
_REPORT_MOVIES_PAYLOAD = (
    "VideoCode",
    "Page",
    "Actor",
    "Rate",
    "CommentNumber",
)


_REPORT_TORRENTS_KEY = ("ReportMovieId", "MagnetUri")
_REPORT_TORRENTS_PAYLOAD = (
    "VideoCode",
    "SubtitleIndicator",
    "CensorIndicator",
    "ResolutionType",
    "Size",
    "FileCount",
)


def _reconcile_history(
    sqlite_conn: sqlite3.Connection,
    d1: D1Connection,
    *,
    since_text: Optional[str],
    dry_run: bool,
) -> List[TableStats]:
    """Sync history.db's two tables (MovieHistory + TorrentHistory) into D1."""
    movie_stats = TableStats("MovieHistory")
    torrent_stats = TableStats("TorrentHistory")
    resolver = _D1IdResolver(d1)

    if since_text:
        movie_query = (
            "SELECT * FROM MovieHistory "
            "WHERE COALESCE(DateTimeUpdated, DateTimeCreated, '') >= ? "
            "ORDER BY Id"
        )
        movie_params: list = [since_text]
    else:
        movie_query = "SELECT * FROM MovieHistory ORDER BY Id"
        movie_params = []

    movie_rows = sqlite_conn.execute(movie_query, movie_params).fetchall()
    logger.info("history: scanning %d MovieHistory rows", len(movie_rows))
    for row in movie_rows:
        _upsert_by_business_key(
            d1=d1,
            table="MovieHistory",
            key_cols=_MOVIE_HISTORY_KEY,
            payload_cols=_MOVIE_HISTORY_PAYLOAD,
            sqlite_row_values=_row_to_dict(row),
            stats=movie_stats,
            dry_run=dry_run,
        )

    if since_text:
        torrent_query = (
            "SELECT t.*, mh.Href AS _ParentHref "
            "FROM TorrentHistory t "
            "JOIN MovieHistory mh ON mh.Id = t.MovieHistoryId "
            "WHERE COALESCE(t.DateTimeUpdated, t.DateTimeCreated, '') >= ? "
            "ORDER BY t.Id"
        )
        torrent_params: list = [since_text]
    else:
        torrent_query = (
            "SELECT t.*, mh.Href AS _ParentHref "
            "FROM TorrentHistory t "
            "JOIN MovieHistory mh ON mh.Id = t.MovieHistoryId "
            "ORDER BY t.Id"
        )
        torrent_params = []

    torrent_rows = sqlite_conn.execute(torrent_query, torrent_params).fetchall()
    logger.info("history: scanning %d TorrentHistory rows", len(torrent_rows))
    for row in torrent_rows:
        d = _row_to_dict(row)
        parent_href = d.pop("_ParentHref", None)
        if not parent_href:
            torrent_stats.skipped_missing_parent += 1
            continue
        d1_movie_id = resolver.get("MovieHistory", ("Href",), (parent_href,))
        if d1_movie_id is None:
            torrent_stats.skipped_missing_parent += 1
            logger.warning(
                "TorrentHistory Id=%s skipped: parent MovieHistory(Href=%s) "
                "not found in D1 yet (will be retried on next pass)",
                d.get("Id"), parent_href,
            )
            continue
        d["MovieHistoryId"] = d1_movie_id
        _upsert_by_business_key(
            d1=d1,
            table="TorrentHistory",
            key_cols=_TORRENT_HISTORY_KEY,
            payload_cols=_TORRENT_HISTORY_PAYLOAD,
            sqlite_row_values=d,
            stats=torrent_stats,
            dry_run=dry_run,
        )

    return [movie_stats, torrent_stats]


def _reconcile_reports(
    sqlite_conn: sqlite3.Connection,
    d1: D1Connection,
    *,
    since_text: Optional[str],
    dry_run: bool,
) -> List[TableStats]:
    """Sync reports.db: ReportSessions → ReportMovies → ReportTorrents."""
    sessions_stats = TableStats("ReportSessions")
    movies_stats = TableStats("ReportMovies")
    torrents_stats = TableStats("ReportTorrents")
    resolver = _D1IdResolver(d1)

    if since_text:
        sessions_query = (
            "SELECT * FROM ReportSessions WHERE DateTimeCreated >= ? ORDER BY Id"
        )
        sessions_params: list = [since_text]
    else:
        sessions_query = "SELECT * FROM ReportSessions ORDER BY Id"
        sessions_params = []

    session_rows = sqlite_conn.execute(sessions_query, sessions_params).fetchall()
    logger.info("reports: scanning %d ReportSessions rows", len(session_rows))

    sqlite_session_id_to_csv: Dict[int, str] = {}
    for row in session_rows:
        d = _row_to_dict(row)
        sqlite_session_id_to_csv[int(d["Id"])] = d["CsvFilename"]
        _upsert_by_business_key(
            d1=d1,
            table="ReportSessions",
            key_cols=_REPORT_SESSIONS_KEY,
            payload_cols=_REPORT_SESSIONS_PAYLOAD,
            sqlite_row_values=d,
            stats=sessions_stats,
            dry_run=dry_run,
        )

    if not sqlite_session_id_to_csv:
        return [sessions_stats, movies_stats, torrents_stats]

    placeholders = ", ".join("?" for _ in sqlite_session_id_to_csv)
    movies_query = (
        "SELECT rm.*, rs.CsvFilename AS _SessionCsv "
        "FROM ReportMovies rm "
        "JOIN ReportSessions rs ON rs.Id = rm.SessionId "
        f"WHERE rm.SessionId IN ({placeholders}) ORDER BY rm.Id"
    )
    movie_rows = sqlite_conn.execute(
        movies_query, list(sqlite_session_id_to_csv.keys())
    ).fetchall()
    logger.info("reports: scanning %d ReportMovies rows", len(movie_rows))

    sqlite_movie_id_to_keys: Dict[int, Tuple[str, str]] = {}
    for row in movie_rows:
        d = _row_to_dict(row)
        session_csv = d.pop("_SessionCsv", None)
        movie_href = d.get("Href")
        if not session_csv or not movie_href:
            movies_stats.skipped_missing_parent += 1
            continue
        d1_session_id = resolver.get(
            "ReportSessions", ("CsvFilename",), (session_csv,)
        )
        if d1_session_id is None:
            movies_stats.skipped_missing_parent += 1
            continue
        d["SessionId"] = d1_session_id
        sqlite_movie_id_to_keys[int(d["Id"])] = (session_csv, movie_href)
        _upsert_by_business_key(
            d1=d1,
            table="ReportMovies",
            key_cols=_REPORT_MOVIES_KEY,
            payload_cols=_REPORT_MOVIES_PAYLOAD,
            sqlite_row_values=d,
            stats=movies_stats,
            dry_run=dry_run,
        )

    if not sqlite_movie_id_to_keys:
        return [sessions_stats, movies_stats, torrents_stats]

    movie_id_placeholders = ", ".join("?" for _ in sqlite_movie_id_to_keys)
    torrents_query = (
        "SELECT rt.*, rm.Href AS _MovieHref, rs.CsvFilename AS _SessionCsv "
        "FROM ReportTorrents rt "
        "JOIN ReportMovies rm ON rm.Id = rt.ReportMovieId "
        "JOIN ReportSessions rs ON rs.Id = rm.SessionId "
        f"WHERE rt.ReportMovieId IN ({movie_id_placeholders}) ORDER BY rt.Id"
    )
    torrent_rows = sqlite_conn.execute(
        torrents_query, list(sqlite_movie_id_to_keys.keys())
    ).fetchall()
    logger.info("reports: scanning %d ReportTorrents rows", len(torrent_rows))

    for row in torrent_rows:
        d = _row_to_dict(row)
        movie_href = d.pop("_MovieHref", None)
        session_csv = d.pop("_SessionCsv", None)
        magnet = d.get("MagnetUri")
        if not (movie_href and session_csv and magnet):
            torrents_stats.skipped_missing_parent += 1
            continue
        d1_session_id = resolver.get(
            "ReportSessions", ("CsvFilename",), (session_csv,)
        )
        if d1_session_id is None:
            torrents_stats.skipped_missing_parent += 1
            continue
        d1_movie_id = resolver.get(
            "ReportMovies", ("SessionId", "Href"), (d1_session_id, movie_href)
        )
        if d1_movie_id is None:
            torrents_stats.skipped_missing_parent += 1
            continue
        d["ReportMovieId"] = d1_movie_id
        _upsert_by_business_key(
            d1=d1,
            table="ReportTorrents",
            key_cols=_REPORT_TORRENTS_KEY,
            payload_cols=_REPORT_TORRENTS_PAYLOAD,
            sqlite_row_values=d,
            stats=torrents_stats,
            dry_run=dry_run,
        )

    return [sessions_stats, movies_stats, torrents_stats]


def _reconcile_operations(
    sqlite_conn: sqlite3.Connection,
    d1: D1Connection,
    *,
    since_text: Optional[str],
    dry_run: bool,
) -> List[TableStats]:
    """operations.db: not in scope for first pass — emit informational stub.

    The plan limits initial table coverage to the four high-frequency tables
    (MovieHistory / TorrentHistory / ReportMovies / ReportTorrents). Add
    table-specific reconcilers here when those operational tables (Rclone,
    Dedup, Pikpak) start showing drift.
    """
    logger.info(
        "operations: skipped (no reconciler implemented yet); "
        "add when RcloneInventory/DedupRecords/PikpakHistory drift is seen"
    )
    return []


_DB_RECONCILERS = {
    "history": _reconcile_history,
    "reports": _reconcile_reports,
    "operations": _reconcile_operations,
}


# ── Drift log archival ────────────────────────────────────────────────────


def _archive_processed_records(
    drift_log: str,
    processed_log: str,
    consumed: List[dict],
    leftover: List[dict],
) -> None:
    """Atomically move *consumed* records to *processed_log* and rewrite *drift_log*.

    Uses temp-file + ``os.replace`` so a crash mid-write doesn't corrupt the
    drift log.
    """
    if consumed:
        os.makedirs(os.path.dirname(processed_log) or ".", exist_ok=True)
        with open(processed_log, "a", encoding="utf-8") as fh:
            for rec in consumed:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    os.makedirs(os.path.dirname(drift_log) or ".", exist_ok=True)
    tmp_path = drift_log + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for rec in leftover:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp_path, drift_log)


# ── Main orchestration ────────────────────────────────────────────────────


def reconcile(
    *,
    dbs: Sequence[str],
    drift_log: str = _DEFAULT_DRIFT_LOG,
    processed_log: str = _DEFAULT_PROCESSED_LOG,
    since: Optional[datetime] = None,
    all_rows: bool = False,
    dry_run: bool = False,
) -> int:
    """Run the reconciler for the requested logical *dbs*.

    Returns a non-zero exit code on per-table errors so CI can flag failures.
    """
    drift_records = _read_drift_log(drift_log)
    if not drift_records and not all_rows:
        msg = (
            f"No drift records found in {drift_log} and --all-rows not set; "
            "nothing to do."
        )
        logger.info(msg)
        # Print to stdout too so the user sees a result even if no logging
        # handler is configured by the embedding script.
        print(msg)
        return 0

    earliest_per_db = _earliest_since_per_db(drift_records)

    overall_errors = 0
    all_stats: List[TableStats] = []
    consumed: List[dict] = []

    for db in dbs:
        if db not in _DB_RECONCILERS:
            logger.warning("Unknown logical db %r; skipping", db)
            continue

        if all_rows:
            since_for_db = None
        elif since is not None:
            since_for_db = since
        else:
            since_for_db = earliest_per_db.get(db)
            if since_for_db is None:
                logger.info("No drift records for db=%s; skipping", db)
                continue

        since_text = (
            _datetime_to_sqlite_text(since_for_db) if since_for_db is not None else None
        )

        sqlite_path = _LOGICAL_TO_DB_PATH[db]
        if not os.path.exists(sqlite_path):
            logger.error("SQLite db file missing for %s: %s", db, sqlite_path)
            overall_errors += 1
            continue

        try:
            sqlite_conn = _open_sqlite_readonly(sqlite_path)
        except Exception as exc:
            logger.error("Failed to open SQLite %s: %s", sqlite_path, exc)
            overall_errors += 1
            continue

        try:
            d1_conn = make_d1_connection(db)
        except D1Error as exc:
            logger.error("Failed to construct D1 connection for %s: %s", db, exc)
            sqlite_conn.close()
            overall_errors += 1
            continue

        logger.info(
            "Reconciling db=%s (sqlite=%s, since=%s, dry_run=%s)",
            db, sqlite_path, since_text or "<all>", dry_run,
        )

        try:
            stats = _DB_RECONCILERS[db](
                sqlite_conn, d1_conn, since_text=since_text, dry_run=dry_run
            )
        except Exception as exc:
            logger.exception("Unhandled error reconciling %s: %s", db, exc)
            overall_errors += 1
            sqlite_conn.close()
            continue
        finally:
            sqlite_conn.close()

        all_stats.extend(stats)
        any_table_errors = any(s.errors for s in stats)
        if any_table_errors:
            overall_errors += 1
        else:
            consumed.extend(rec for rec in drift_records if rec.get("db") == db)

    print()
    print("=" * 78)
    print("D1 drift reconciliation summary" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 78)
    if not all_stats:
        print("(no tables processed)")
    for stat in all_stats:
        print("  " + stat.as_summary())
    print("=" * 78)

    if dry_run:
        logger.info("Dry-run: leaving drift log untouched")
        return overall_errors

    if consumed:
        leftover = [r for r in drift_records if r not in consumed]
        try:
            _archive_processed_records(drift_log, processed_log, consumed, leftover)
            logger.info(
                "Archived %d drift record(s) to %s; %d remain in %s",
                len(consumed), processed_log, len(leftover), drift_log,
            )
        except OSError as exc:
            logger.error("Failed to archive drift log: %s", exc)
            overall_errors += 1

    return overall_errors


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile Cloudflare D1 against local SQLite by re-applying "
            "rows missed during dual-write outages."
        ),
    )
    parser.add_argument(
        "--db",
        choices=("all", "history", "reports", "operations"),
        default="all",
        help="Logical database to reconcile (default: all that show drift).",
    )
    parser.add_argument(
        "--drift-log",
        default=_DEFAULT_DRIFT_LOG,
        help=f"Path to drift jsonl (default: {_DEFAULT_DRIFT_LOG}).",
    )
    parser.add_argument(
        "--processed-log",
        default=_DEFAULT_PROCESSED_LOG,
        help=f"Where consumed records are appended (default: {_DEFAULT_PROCESSED_LOG}).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO8601 lower bound, e.g. 2026-04-25T00:00:00Z. "
        "Overrides the auto-detected window from the drift log.",
    )
    parser.add_argument(
        "--all-rows",
        action="store_true",
        help="Scan every row regardless of timestamp/drift entries (slow).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report differences without writing to D1 or archiving the drift log.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity for the CLI session (default: INFO).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    # CLI entry-point: install a console handler so info/warning logs are
    # visible. Without this the platform's get_logger() returns a handler-less
    # logger and every logger.info/warning is silently dropped.
    setup_logging(log_level=args.log_level)

    if args.db == "all":
        dbs = ("history", "reports", "operations")
    else:
        dbs = (args.db,)

    since_dt: Optional[datetime] = None
    if args.since:
        since_dt = _parse_iso8601(args.since)
        if since_dt is None:
            print(f"--since {args.since!r} is not a valid ISO 8601 timestamp", file=sys.stderr)
            return 2

    return reconcile(
        dbs=dbs,
        drift_log=args.drift_log,
        processed_log=args.processed_log,
        since=since_dt,
        all_rows=args.all_rows,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
