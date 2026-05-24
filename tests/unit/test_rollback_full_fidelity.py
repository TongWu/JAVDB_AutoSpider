"""End-to-end fidelity tests for ``db_rollback_session``.

The rollback CLI must be able to restore D1 (and the local SQLite
mirror under ``STORAGE_BACKEND=dual``) to the exact state it was in
before a failed run touched it. These tests:

1. Cover reports rollback (ReportSessions, ReportMovies, ReportTorrents,
   SpiderStats, UploaderStats, PikpakStats) and operations rollback
   (PikpakHistory, DedupRecords, InventoryAlignNoExactMatch).
2. Use a real ``init_db`` SQLite fixture (same DDL as production).
3. Snapshot ``SELECT *`` *before* mutation and *after* rollback, then
   assert byte-for-byte equality.
4. Cross-check that every column declared in D1 migration files also
   exists in the local DDL (schema-parity contract).
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Dict, List, Set, Tuple

from javdb.storage.db import get_db, db_create_report_session, db_rollback_session


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row | None) -> Dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _columns_of(conn, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _snapshot_table(conn, table: str, where: str = "1=1",
                    params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    # Sort by primary key when present; fall back to a stable
    # column-ordered sort so byte-for-byte comparison is deterministic
    # across SQLite's implicit row ordering.
    cols = _columns_of(conn, table)
    if "Id" in cols:
        order_by = "Id"
    elif cols:
        order_by = ", ".join(cols)
    else:
        order_by = "1"
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY {order_by}",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _create_session(*, run_id: str | None = None,
                    run_attempt: int | None = None,
                    when: str | None = None,
                    csv_filename: str = "fidelity-test.csv") -> int:
    return db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-08",
        csv_filename=csv_filename,
        created_at=when,
        run_id=run_id,
        run_attempt=run_attempt,
    )


# ──────────────────────────────────────────────────────────────────────
# Section B — Reports DB rollback
# ──────────────────────────────────────────────────────────────────────


class TestReportsRollbackPurgesAllTablesForSessionOnly:
    """``_rollback_reports`` deletes session-tagged rows from
    ReportTorrents -> ReportMovies -> SpiderStats / UploaderStats /
    PikpakStats -> ReportSessions. Verify none of the *other* session's
    rows are touched."""

    def _seed_full_reports_payload(self, sid: int, suffix: str) -> None:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO ReportMovies "
                "(SessionId, Href, VideoCode, Page, Actor, Rate, "
                " CommentNumber) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, f"/v/{suffix}", suffix, 1, "Actor", 4.5, 100),
            )
            mid = conn.execute(
                "SELECT Id FROM ReportMovies "
                "WHERE SessionId=? AND VideoCode=?", (sid, suffix),
            ).fetchone()["Id"]
            conn.execute(
                "INSERT INTO ReportTorrents "
                "(ReportMovieId, VideoCode, MagnetUri, "
                " SubtitleIndicator, CensorIndicator, ResolutionType, "
                " Size, FileCount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, suffix, f"magnet:{suffix}", 1, 0, 1080,
                 "1.0GB", 1),
            )
            conn.execute(
                "INSERT INTO SpiderStats "
                "(SessionId, Phase1Discovered, Phase1Processed, "
                " TotalDiscovered, DateTimeCreated) "
                "VALUES (?, 10, 9, 10, ?)",
                (sid, "2026-05-08 10:00:00"),
            )
            conn.execute(
                "INSERT INTO UploaderStats "
                "(SessionId, TotalTorrents, Attempted, "
                " SuccessfullyAdded) VALUES (?, 5, 5, 5)",
                (sid,),
            )
            conn.execute(
                "INSERT INTO PikpakStats "
                "(SessionId, ThresholdDays, TotalTorrents, "
                " FilteredOld, SuccessfulCount, FailedCount, "
                " UploadedCount, DeleteFailedCount, "
                " DateTimeCreated) "
                "VALUES (?, 3, 5, 0, 5, 0, 5, 0, "
                " '2026-05-08 10:00:00')",
                (sid,),
            )

    def test_only_targeted_session_rows_purged(self):
        sid_keep = _create_session(csv_filename="keep.csv")
        sid_drop = _create_session(csv_filename="drop.csv")
        self._seed_full_reports_payload(sid_keep, "KEEP-001")
        self._seed_full_reports_payload(sid_drop, "DROP-001")

        with get_db() as conn:
            keep_before = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("ReportMovies", "SpiderStats",
                           "UploaderStats", "PikpakStats")
            }
            keep_torrents_before = _snapshot_table(
                conn, "ReportTorrents",
                "ReportMovieId IN (SELECT Id FROM ReportMovies "
                "WHERE SessionId=?)", (sid_keep,),
            )
            keep_session_before = _snapshot_table(
                conn, "ReportSessions", "Id=?", (sid_keep,),
            )

        result = db_rollback_session(sid_drop, scope="reports")
        assert result["reports"]["ReportSessions"] == 1
        assert result["reports"]["ReportMovies"] == 1
        assert result["reports"]["ReportTorrents"] == 1
        assert result["reports"]["SpiderStats"] == 1
        assert result["reports"]["UploaderStats"] == 1
        assert result["reports"]["PikpakStats"] == 1

        with get_db() as conn:
            keep_after = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("ReportMovies", "SpiderStats",
                           "UploaderStats", "PikpakStats")
            }
            keep_torrents_after = _snapshot_table(
                conn, "ReportTorrents",
                "ReportMovieId IN (SELECT Id FROM ReportMovies "
                "WHERE SessionId=?)", (sid_keep,),
            )
            keep_session_after = _snapshot_table(
                conn, "ReportSessions", "Id=?", (sid_keep,),
            )
            drop_session = _snapshot_table(
                conn, "ReportSessions", "Id=?", (sid_drop,),
            )
        assert keep_after == keep_before
        assert keep_torrents_after == keep_torrents_before
        assert keep_session_after == keep_session_before
        assert drop_session == [], (
            "rollback must remove the targeted ReportSessions row"
        )


# ──────────────────────────────────────────────────────────────────────
# Section C — Operations DB rollback
# ──────────────────────────────────────────────────────────────────────


class TestOperationsRollbackPurgesAllTablesForSessionOnly:
    def test_only_targeted_session_rows_purged(self):
        sid_keep = _create_session(csv_filename="ops-keep.csv")
        sid_drop = _create_session(csv_filename="ops-drop.csv")
        with get_db() as conn:
            for sid, suffix in (
                (sid_keep, "KEEP"), (sid_drop, "DROP"),
            ):
                conn.execute(
                    "INSERT INTO PikpakHistory "
                    "(TorrentHash, TorrentName, Category, MagnetUri, "
                    " DateTimeAddedToQb, DateTimeDeletedFromQb, "
                    " DateTimeUploadedToPikpak, TransferStatus, "
                    " ErrorMessage, SessionId) "
                    "VALUES (?, ?, 'subtitle', ?, ?, ?, ?, 'OK', "
                    " '', ?)",
                    (
                        f"hash-{suffix}",
                        f"name-{suffix}.mkv",
                        f"magnet:{suffix}",
                        "2026-05-08 10:00:00",
                        "2026-05-08 10:30:00",
                        "2026-05-08 11:00:00",
                        sid,
                    ),
                )
                conn.execute(
                    "INSERT INTO DedupRecords "
                    "(VideoCode, ExistingSensor, ExistingSubtitle, "
                    " ExistingGdrivePath, ExistingFolderSize, "
                    " NewTorrentCategory, DeletionReason, "
                    " DateTimeDetected, IsDeleted, SessionId) "
                    "VALUES (?, 'cen', 'sub', ?, 1024, "
                    " 'subtitle', 'duplicate', "
                    " '2026-05-08 10:00:00', 0, ?)",
                    (f"VID-{suffix}", f"/path/{suffix}", sid),
                )
                conn.execute(
                    "INSERT INTO InventoryAlignNoExactMatch "
                    "(VideoCode, Reason, DateTimeRecorded, "
                    " SessionId) "
                    "VALUES (?, 'no-match', "
                    " '2026-05-08 10:00:00', ?)",
                    (f"VID-NM-{suffix}", sid),
                )

            keep_before = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("PikpakHistory", "DedupRecords",
                           "InventoryAlignNoExactMatch")
            }

        result = db_rollback_session(sid_drop, scope="operations")
        for t in ("PikpakHistory", "DedupRecords",
                   "InventoryAlignNoExactMatch"):
            assert result["operations"][t] == 1, (
                f"{t} should have purged 1 row tagged sid_drop"
            )
        with get_db() as conn:
            keep_after = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("PikpakHistory", "DedupRecords",
                           "InventoryAlignNoExactMatch")
            }
            for t in ("PikpakHistory", "DedupRecords",
                       "InventoryAlignNoExactMatch"):
                drop_left = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {t} WHERE SessionId=?",
                    (sid_drop,),
                ).fetchone()["n"]
                assert drop_left == 0, (
                    f"{t} still has rows for the rolled-back session"
                )
        assert keep_after == keep_before


# ──────────────────────────────────────────────────────────────────────
# Section D — D1 schema parity
# ──────────────────────────────────────────────────────────────────────


_D1_LOGICAL_TO_LOCAL_PATH = {
    "history":    "HISTORY_DB_PATH",
    "reports":    "REPORTS_DB_PATH",
    "operations": "OPERATIONS_DB_PATH",
}

_MIGRATION_SUFFIX_TO_LOGICAL = {
    "history":    "history",
    "reports":    "reports",
    "operations": "operations",
}


def _logical_for_migration_filename(name: str) -> str | None:
    """Map a migration filename to a logical DB."""
    base = os.path.basename(name).lower()
    for suffix, logical in _MIGRATION_SUFFIX_TO_LOGICAL.items():
        if suffix in base:
            return logical
    if "sessionid_decouple" in base or "unique_in_progress" in base:
        return "reports"
    return None


def _columns_in_migration_sql(sql: str) -> Dict[str, Set[str]]:
    """Extract ``{table_name: {column_names_referenced}}`` from a
    migration file. Recognises ``ALTER TABLE X ADD COLUMN Y`` and
    ``CREATE TABLE [IF NOT EXISTS] X (...)`` (column names parsed by
    splitting on commas at depth 0). Index DDL is ignored.
    """
    out: Dict[str, Set[str]] = {}

    # ALTER TABLE X ADD COLUMN Y type
    for m in re.finditer(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
        sql, flags=re.IGNORECASE,
    ):
        table, col = m.group(1), m.group(2)
        out.setdefault(table, set()).add(col)

    # CREATE TABLE X ( ... )
    # Exclude the "_new" intermediary tables created by the 12-step ALTER
    # pattern used in D1 migrations (e.g. MovieHistory_new -> MovieHistory).
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
        sql, flags=re.IGNORECASE,
    ):
        table = m.group(1)
        if table.endswith("_new"):
            continue
        # Find the matching closing paren accounting for nested.
        start = m.end() - 1  # position of '('
        depth = 0
        end = -1
        for i, ch in enumerate(sql[start:], start):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            continue
        body = sql[start + 1:end]
        # Split on commas at depth 0.
        cols: List[str] = []
        buf: List[str] = []
        depth2 = 0
        for ch in body:
            if ch == '(':
                depth2 += 1
            elif ch == ')':
                depth2 -= 1
            if ch == ',' and depth2 == 0:
                cols.append(''.join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        if buf:
            cols.append(''.join(buf).strip())
        for c in cols:
            tok = c.strip()
            if not tok:
                continue
            head = tok.split(None, 1)[0].upper()
            # Skip table-level constraint clauses.
            if head in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK",
                         "CONSTRAINT"}:
                continue
            name = tok.split(None, 1)[0]
            out.setdefault(table, set()).add(name)
    return out


class TestD1MigrationsAreCoveredByLocalSchema:
    """Every column declared in a D1 migration must also exist in the
    local SQLite schema after ``init_db`` + ``_ensure_rollback_columns``.

    This guarantees that the rollback CLI's SQL against SQLite will also
    be valid on D1 (same column names exist on both sides) — which is
    the entire contract that makes dual-write rollback work.
    """

    def test_every_d1_migration_column_exists_in_local_ddl(self):
        # The autouse ``_isolate_sqlite`` fixture has already run
        # ``init_db`` + ``_ensure_rollback_columns`` against the test
        # SQLite, so every column the production code path declares
        # is present.
        with get_db() as conn:
            local_cols: Dict[str, Set[str]] = {}
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall():
                tbl = row[0]
                local_cols[tbl] = {
                    r[1] for r in conn.execute(
                        f"PRAGMA table_info({tbl})"
                    )
                }

        migration_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "javdb", "migrations", "d1",
        )
        files = sorted(
            f for f in os.listdir(migration_dir) if f.endswith(".sql")
        )
        assert files, "no D1 migration files found"

        missing: List[str] = []
        for fname in files:
            with open(os.path.join(migration_dir, fname),
                       encoding="utf-8") as fh:
                sql = fh.read()
            for table, cols in _columns_in_migration_sql(sql).items():
                if table in {"MovieHistoryAudit", "TorrentHistoryAudit"}:
                    continue
                if table not in local_cols:
                    missing.append(
                        f"{fname}: table {table!r} not in local DDL"
                    )
                    continue
                for c in cols:
                    if c not in local_cols[table]:
                        missing.append(
                            f"{fname}: {table}.{c} not in local DDL"
                        )
        assert not missing, (
            "D1 migrations declare columns that local SQLite DDL is "
            "missing — rollback can't round-trip them:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )
