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

# CF D1 caps per-request batches at 50 statements. Using the same constant
# avoids importing it from d1_client and keeps the reconciler self-documenting.
_BATCH_SIZE = 50


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


# ── Batched D1 helpers ────────────────────────────────────────────────────


def _batch_select_existing(
    d1: D1Connection,
    table: str,
    key_cols: Sequence[str],
    select_cols: Sequence[str],
    key_value_tuples: Sequence[Tuple],
    *,
    progress_label: Optional[str] = None,
) -> Dict[Tuple, dict]:
    """Bulk-fetch existing D1 rows keyed by *key_cols*, one HTTP roundtrip per
    50 keys.

    Returns ``{key_tuple: row_dict}`` containing rows that exist in D1; missing
    keys are absent from the map. Always includes the ``Id`` column in the
    SELECT so callers can resolve foreign keys via the returned map.

    Falls back to per-row :meth:`D1Connection.execute` on batch failure so a
    single malformed key doesn't mask the rest.
    """
    if not key_value_tuples:
        return {}

    where = " AND ".join(f"{c} = ?" for c in key_cols)
    cols = ", ".join(["Id", *select_cols]) if select_cols else "Id"
    sql = f"SELECT {cols} FROM {table} WHERE {where} LIMIT 1"

    out: Dict[Tuple, dict] = {}
    total = len(key_value_tuples)
    label = progress_label or f"{table} lookup"

    for chunk_start in range(0, total, _BATCH_SIZE):
        chunk_keys = key_value_tuples[chunk_start : chunk_start + _BATCH_SIZE]
        statements = [(sql, list(k)) for k in chunk_keys]
        try:
            cursors = d1.batch_execute(statements)
        except D1Error as batch_exc:
            logger.warning(
                "%s: batched SELECT (%d keys) failed, falling back per-row: %s",
                label, len(chunk_keys), batch_exc,
            )
            cursors = []
            for k in chunk_keys:
                try:
                    cursors.append(d1.execute(sql, list(k)))
                except D1Error as exc:
                    logger.warning("%s: SELECT key=%s failed: %s", label, k, exc)
                    cursors.append(None)
        for key, cur in zip(chunk_keys, cursors):
            if cur is None:
                continue
            row = cur.fetchone()
            if row is not None:
                out[tuple(key)] = _row_to_dict(row)

        scanned = min(chunk_start + _BATCH_SIZE, total)
        # Heartbeat every 10 batches (500 rows) for long scans, plus once at
        # end so users always see the final tally.
        if total > _BATCH_SIZE and (
            scanned == total or scanned % (_BATCH_SIZE * 10) == 0
        ):
            logger.info("  %s: scanned %d/%d (found %d)", label, scanned, total, len(out))

    return out


def _flush_writes(
    d1: D1Connection,
    statements: Sequence[Tuple[str, Sequence[Any]]],
    *,
    label: str,
    stats: TableStats,
    on_success=None,
) -> None:
    """Apply a batch of write statements via ``D1Connection.batch_execute``.

    Each batch of <=50 is sent as one CF /query call. CF batches are atomic on
    failure, so if a chunk raises (UNIQUE collision from a parallel writer,
    etc.) we retry that chunk one row at a time to attribute the error.

    *on_success* is called as ``on_success(global_index, cursor)`` after each
    succeeded statement; INSERT batches use it to capture ``cursor.lastrowid``.
    """
    if not statements:
        return
    total = len(statements)
    for chunk_start in range(0, total, _BATCH_SIZE):
        chunk = list(statements[chunk_start : chunk_start + _BATCH_SIZE])
        try:
            cursors = d1.batch_execute(chunk)
            if on_success is not None:
                for offset, cur in enumerate(cursors):
                    on_success(chunk_start + offset, cur)
        except D1Error as batch_exc:
            logger.warning(
                "%s: batch (%d rows) failed, retrying per-row: %s",
                label, len(chunk), batch_exc,
            )
            for offset, (sql, params) in enumerate(chunk):
                try:
                    cur = d1.execute(sql, list(params))
                    if on_success is not None:
                        on_success(chunk_start + offset, cur)
                except D1Error as exc:
                    stats.errors += 1
                    msg = f"{label} row failed: {exc}"
                    stats.error_messages.append(msg)
                    logger.warning(msg)
        scanned = min(chunk_start + _BATCH_SIZE, total)
        if total > _BATCH_SIZE and (
            scanned == total or scanned % (_BATCH_SIZE * 10) == 0
        ):
            logger.info("  %s: applied %d/%d", label, scanned, total)


def _process_table(
    *,
    d1: D1Connection,
    table: str,
    key_cols: Sequence[str],
    payload_cols: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    dry_run: bool,
    stats: TableStats,
) -> Dict[Tuple, int]:
    """Reconcile one table from a list of FK-resolved SQLite-row dicts.

    Updates *stats* (checked / inserted / updated / skipped_equal / errors) and
    returns ``{key_tuple: d1_id}`` for both pre-existing rows and rows just
    inserted in this run. Children resolve their FK by looking up the returned
    map.
    """
    key_to_id: Dict[Tuple, int] = {}
    stats.checked += len(rows)

    if not rows:
        return key_to_id

    key_tuples: List[Tuple] = [tuple(r[c] for c in key_cols) for r in rows]
    logger.info(
        "%s: prefetching existing D1 rows for %d candidates...", table, len(key_tuples)
    )
    existing = _batch_select_existing(
        d1, table, key_cols, payload_cols, key_tuples,
        progress_label=f"{table} lookup",
    )
    logger.info(
        "%s: %d already in D1, %d missing", table, len(existing), len(rows) - len(existing)
    )

    all_cols = list(key_cols) + list(payload_cols)
    placeholders = ", ".join("?" for _ in all_cols)
    insert_sql = f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders})"
    set_clause = ", ".join(f"{c} = ?" for c in payload_cols)
    update_sql = f"UPDATE {table} SET {set_clause} WHERE Id = ?"

    insert_stmts: List[Tuple[str, list]] = []
    insert_keys: List[Tuple] = []
    update_stmts: List[Tuple[str, list]] = []

    for key, row in zip(key_tuples, rows):
        existing_row = existing.get(key)
        if existing_row is None:
            insert_stmts.append((insert_sql, [row[c] for c in all_cols]))
            insert_keys.append(key)
            continue
        d1_id = int(existing_row["Id"])
        key_to_id[key] = d1_id
        if _rows_match(existing_row, row, payload_cols):
            stats.skipped_equal += 1
        else:
            update_stmts.append(
                (update_sql, [row[c] for c in payload_cols] + [d1_id])
            )

    if dry_run:
        stats.inserted += len(insert_stmts)
        stats.updated += len(update_stmts)
        return key_to_id

    if insert_stmts:
        logger.info("%s: inserting %d new rows", table, len(insert_stmts))

        def _capture_id(idx: int, cur) -> None:
            stats.inserted += 1
            if cur.lastrowid is not None:
                key_to_id[insert_keys[idx]] = int(cur.lastrowid)

        _flush_writes(
            d1, insert_stmts, label=f"INSERT {table}", stats=stats,
            on_success=_capture_id,
        )

    if update_stmts:
        logger.info("%s: updating %d changed rows", table, len(update_stmts))

        def _bump(_idx: int, _cur) -> None:
            stats.updated += 1

        _flush_writes(
            d1, update_stmts, label=f"UPDATE {table}", stats=stats,
            on_success=_bump,
        )

    return key_to_id


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
    """Sync history.db's two tables (MovieHistory + TorrentHistory) into D1.

    Uses batched D1 SELECTs (50 per HTTP roundtrip) for existence checks and
    batched INSERT/UPDATE for writes. Children resolve their parent FK from
    the in-memory ``href_to_d1_id`` map populated by the MovieHistory pass
    (covers both pre-existing and newly-inserted parents).
    """
    movie_stats = TableStats("MovieHistory")
    torrent_stats = TableStats("TorrentHistory")

    # ── 1. MovieHistory ────────────────────────────────────────────────
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

    movie_rows = [
        _row_to_dict(r)
        for r in sqlite_conn.execute(movie_query, movie_params).fetchall()
    ]
    logger.info("history: scanning %d MovieHistory rows", len(movie_rows))

    href_to_d1_id = _process_table(
        d1=d1,
        table="MovieHistory",
        key_cols=_MOVIE_HISTORY_KEY,
        payload_cols=_MOVIE_HISTORY_PAYLOAD,
        rows=movie_rows,
        dry_run=dry_run,
        stats=movie_stats,
    )

    # ── 2. TorrentHistory ─────────────────────────────────────────────
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

    raw_torrent_rows = sqlite_conn.execute(torrent_query, torrent_params).fetchall()
    logger.info("history: scanning %d TorrentHistory rows", len(raw_torrent_rows))

    # Resolve any parent Hrefs not seen by the MovieHistory pass — possible
    # when --since cuts movie rows but their torrents fall in scope (movie
    # was created earlier but not modified inside the window).
    missing_parents = {
        d.get("_ParentHref")
        for d in (_row_to_dict(r) for r in raw_torrent_rows)
        if d.get("_ParentHref") and (d.get("_ParentHref"),) not in href_to_d1_id
    }
    if missing_parents:
        logger.info(
            "history: looking up %d additional MovieHistory parents in D1",
            len(missing_parents),
        )
        extra = _batch_select_existing(
            d1, "MovieHistory", _MOVIE_HISTORY_KEY, (),
            [(h,) for h in missing_parents],
            progress_label="MovieHistory parent-FK lookup",
        )
        for key, row in extra.items():
            href_to_d1_id[key] = int(row["Id"])

    prepared_torrents: List[Dict[str, Any]] = []
    for raw in raw_torrent_rows:
        d = _row_to_dict(raw)
        parent_href = d.pop("_ParentHref", None)
        if not parent_href:
            torrent_stats.checked += 1
            torrent_stats.skipped_missing_parent += 1
            continue
        d1_movie_id = href_to_d1_id.get((parent_href,))
        if d1_movie_id is None:
            torrent_stats.checked += 1
            torrent_stats.skipped_missing_parent += 1
            logger.warning(
                "TorrentHistory Id=%s skipped: parent MovieHistory(Href=%s) "
                "not in D1 (will retry on next pass)",
                d.get("Id"), parent_href,
            )
            continue
        d["MovieHistoryId"] = d1_movie_id
        prepared_torrents.append(d)

    _process_table(
        d1=d1,
        table="TorrentHistory",
        key_cols=_TORRENT_HISTORY_KEY,
        payload_cols=_TORRENT_HISTORY_PAYLOAD,
        rows=prepared_torrents,
        dry_run=dry_run,
        stats=torrent_stats,
    )

    return [movie_stats, torrent_stats]


def _reconcile_reports(
    sqlite_conn: sqlite3.Connection,
    d1: D1Connection,
    *,
    since_text: Optional[str],
    dry_run: bool,
) -> List[TableStats]:
    """Sync reports.db: ReportSessions → ReportMovies → ReportTorrents.

    All three levels use batched SELECT/INSERT/UPDATE; child levels resolve
    parent FK via the maps returned by the parent pass, augmented by a
    one-shot batched D1 lookup for any parents not in the SQLite scan window.
    """
    sessions_stats = TableStats("ReportSessions")
    movies_stats = TableStats("ReportMovies")
    torrents_stats = TableStats("ReportTorrents")

    # ── 1. ReportSessions ────────────────────────────────────────────
    if since_text:
        sessions_query = (
            "SELECT * FROM ReportSessions WHERE DateTimeCreated >= ? ORDER BY Id"
        )
        sessions_params: list = [since_text]
    else:
        sessions_query = "SELECT * FROM ReportSessions ORDER BY Id"
        sessions_params = []

    session_rows = [
        _row_to_dict(r)
        for r in sqlite_conn.execute(sessions_query, sessions_params).fetchall()
    ]
    logger.info("reports: scanning %d ReportSessions rows", len(session_rows))

    csv_to_d1_session_id = _process_table(
        d1=d1,
        table="ReportSessions",
        key_cols=_REPORT_SESSIONS_KEY,
        payload_cols=_REPORT_SESSIONS_PAYLOAD,
        rows=session_rows,
        dry_run=dry_run,
        stats=sessions_stats,
    )

    sqlite_session_ids = [int(r["Id"]) for r in session_rows]
    if not sqlite_session_ids:
        return [sessions_stats, movies_stats, torrents_stats]

    # ── 2. ReportMovies ───────────────────────────────────────────────
    placeholders = ", ".join("?" for _ in sqlite_session_ids)
    movies_query = (
        "SELECT rm.*, rs.CsvFilename AS _SessionCsv "
        "FROM ReportMovies rm "
        "JOIN ReportSessions rs ON rs.Id = rm.SessionId "
        f"WHERE rm.SessionId IN ({placeholders}) ORDER BY rm.Id"
    )
    raw_movie_rows = sqlite_conn.execute(movies_query, sqlite_session_ids).fetchall()
    logger.info("reports: scanning %d ReportMovies rows", len(raw_movie_rows))

    prepared_movies: List[Dict[str, Any]] = []
    for raw in raw_movie_rows:
        d = _row_to_dict(raw)
        session_csv = d.pop("_SessionCsv", None)
        movie_href = d.get("Href")
        if not (session_csv and movie_href):
            movies_stats.checked += 1
            movies_stats.skipped_missing_parent += 1
            continue
        d1_session_id = csv_to_d1_session_id.get((session_csv,))
        if d1_session_id is None:
            movies_stats.checked += 1
            movies_stats.skipped_missing_parent += 1
            continue
        d["SessionId"] = d1_session_id
        prepared_movies.append(d)

    movie_keys_to_d1_id = _process_table(
        d1=d1,
        table="ReportMovies",
        key_cols=_REPORT_MOVIES_KEY,
        payload_cols=_REPORT_MOVIES_PAYLOAD,
        rows=prepared_movies,
        dry_run=dry_run,
        stats=movies_stats,
    )

    if not prepared_movies:
        return [sessions_stats, movies_stats, torrents_stats]

    # ── 3. ReportTorrents ─────────────────────────────────────────────
    sqlite_movie_ids = [int(d["Id"]) for d in prepared_movies]
    movie_id_placeholders = ", ".join("?" for _ in sqlite_movie_ids)
    torrents_query = (
        "SELECT rt.*, rm.Href AS _MovieHref, rs.CsvFilename AS _SessionCsv "
        "FROM ReportTorrents rt "
        "JOIN ReportMovies rm ON rm.Id = rt.ReportMovieId "
        "JOIN ReportSessions rs ON rs.Id = rm.SessionId "
        f"WHERE rt.ReportMovieId IN ({movie_id_placeholders}) ORDER BY rt.Id"
    )
    raw_torrent_rows = sqlite_conn.execute(torrents_query, sqlite_movie_ids).fetchall()
    logger.info("reports: scanning %d ReportTorrents rows", len(raw_torrent_rows))

    prepared_torrents: List[Dict[str, Any]] = []
    for raw in raw_torrent_rows:
        d = _row_to_dict(raw)
        movie_href = d.pop("_MovieHref", None)
        session_csv = d.pop("_SessionCsv", None)
        magnet = d.get("MagnetUri")
        if not (movie_href and session_csv and magnet):
            torrents_stats.checked += 1
            torrents_stats.skipped_missing_parent += 1
            continue
        d1_session_id = csv_to_d1_session_id.get((session_csv,))
        if d1_session_id is None:
            torrents_stats.checked += 1
            torrents_stats.skipped_missing_parent += 1
            continue
        d1_movie_id = movie_keys_to_d1_id.get((d1_session_id, movie_href))
        if d1_movie_id is None:
            torrents_stats.checked += 1
            torrents_stats.skipped_missing_parent += 1
            continue
        d["ReportMovieId"] = d1_movie_id
        prepared_torrents.append(d)

    _process_table(
        d1=d1,
        table="ReportTorrents",
        key_cols=_REPORT_TORRENTS_KEY,
        payload_cols=_REPORT_TORRENTS_PAYLOAD,
        rows=prepared_torrents,
        dry_run=dry_run,
        stats=torrents_stats,
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
