"""v11 -> v12 -> v13: rewrite INTEGER session_id / Seq columns to TEXT.

These tests exercise :func:`packages.python.javdb_platform.db._migrate_session_id_to_text`
end-to-end: build a fixture DB with the pre-migration schema, seed a
handful of rows whose ``Id`` / ``SessionId`` / ``Seq`` are large
snowflake-shaped integers, run the migration, and assert:

* every affected column's declared type is now TEXT in ``sqlite_master``
* row counts are unchanged
* ``PRAGMA foreign_key_check`` is empty
* the seeded values round-trip losslessly (no truncation)
* a representative cross-table query still returns the same joined row

v13 additions: verify the AUTOINCREMENT variant of ``Seq INTEGER
PRIMARY KEY`` (used in the original 2026-05-09 creation DDL) is also
matched and rewritten, and that partially-migrated databases (v12 with
Seq still INTEGER) are repaired on re-run.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from javdb.storage.db import db as db_mod


# A value past Number.MAX_SAFE_INTEGER (2**53 - 1) — the exact problem we
# are fixing. Reading it back as the same integer asserts no precision
# loss in either direction.
_BIG_SID = 7_821_145_332_910_690_304
_BIG_SEQ = 7_821_145_332_910_690_305


_LEGACY_REPORTS_DDL = """
CREATE TABLE SchemaVersion (Version INTEGER NOT NULL);
INSERT INTO SchemaVersion (Version) VALUES (11);

CREATE TABLE ReportSessions (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ReportType TEXT NOT NULL,
    ReportDate TEXT NOT NULL,
    UrlType TEXT,
    DisplayName TEXT,
    Url TEXT,
    StartPage INTEGER,
    EndPage INTEGER,
    CsvFilename TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    Status TEXT,
    RunId TEXT,
    RunAttempt INTEGER,
    FailureReason TEXT,
    WriteMode TEXT
);

CREATE TABLE ReportMovies (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL REFERENCES ReportSessions(Id),
    Href TEXT,
    VideoCode TEXT,
    Page INTEGER,
    Actor TEXT,
    Rate REAL,
    CommentNumber INTEGER
);

CREATE TABLE SpiderStats (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL REFERENCES ReportSessions(Id),
    Phase1Discovered INTEGER
);
"""


_LEGACY_HISTORY_DDL = """
CREATE TABLE SchemaVersion (Version INTEGER NOT NULL);
INSERT INTO SchemaVersion (Version) VALUES (11);

CREATE TABLE MovieHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    Href TEXT NOT NULL UNIQUE,
    SessionId INTEGER
);

CREATE TABLE MovieHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL
);

CREATE TABLE PendingMovieHistoryWrites (
    Seq INTEGER PRIMARY KEY NOT NULL,
    SessionId INTEGER NOT NULL,
    Href TEXT NOT NULL,
    DateTimeVisited TEXT NOT NULL,
    CreatedAt TEXT NOT NULL,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
);
"""


def _column_type(conn: sqlite3.Connection, table: str, column: str) -> str:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for r in rows:
        # PRAGMA table_info row layout: cid, name, type, notnull, dflt_value, pk
        if r[1] == column:
            return (r[2] or "").upper()
    raise AssertionError(f"{table}.{column} not found")


def test_reports_migration_rewrites_types_and_preserves_values(tmp_path):
    db_path = str(tmp_path / "reports_legacy.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_REPORTS_DDL)
    seed.execute(
        "INSERT INTO ReportSessions (Id, ReportType, ReportDate, "
        "CsvFilename, DateTimeCreated, Status) "
        "VALUES (?, 'daily', '20260513', 's.csv', '2026-05-13 00:00:00', 'in_progress')",
        (_BIG_SID,),
    )
    seed.execute(
        "INSERT INTO ReportMovies (SessionId, Href, VideoCode) "
        "VALUES (?, '/v/X', 'X-001')",
        (_BIG_SID,),
    )
    seed.execute(
        "INSERT INTO SpiderStats (SessionId, Phase1Discovered) VALUES (?, 7)",
        (_BIG_SID,),
    )
    seed.commit()
    seed.close()

    conn = sqlite3.connect(db_path)
    try:
        db_mod._migrate_session_id_to_text(conn)
    finally:
        conn.close()

    # Reopen so the schema rewrite via PRAGMA writable_schema is reloaded.
    chk = sqlite3.connect(db_path)
    chk.row_factory = sqlite3.Row
    try:
        assert _column_type(chk, "ReportSessions", "Id") == "TEXT"
        assert _column_type(chk, "ReportMovies", "SessionId") == "TEXT"
        assert _column_type(chk, "SpiderStats", "SessionId") == "TEXT"

        # Row counts unchanged.
        assert chk.execute("SELECT COUNT(*) FROM ReportSessions").fetchone()[0] == 1
        assert chk.execute("SELECT COUNT(*) FROM ReportMovies").fetchone()[0] == 1
        assert chk.execute("SELECT COUNT(*) FROM SpiderStats").fetchone()[0] == 1

        # Values round-trip — int comes back as int because SQLite stores
        # the original INTEGER bytes; the TEXT declaration only changes
        # *new* writes' affinity. That's fine, the read still matches.
        row = chk.execute("SELECT Id FROM ReportSessions").fetchone()
        assert int(row["Id"]) == _BIG_SID

        # FK still resolves on the legacy int values.
        violations = chk.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []

        # Cross-table join still finds the row.
        joined = chk.execute(
            "SELECT m.VideoCode FROM ReportMovies m "
            "JOIN ReportSessions s ON s.Id = m.SessionId"
        ).fetchall()
        assert [r["VideoCode"] for r in joined] == ["X-001"]
    finally:
        chk.close()


def test_history_migration_rewrites_seq_and_session_id(tmp_path):
    db_path = str(tmp_path / "history_legacy.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_HISTORY_DDL)
    seed.execute(
        "INSERT INTO PendingMovieHistoryWrites "
        "(Seq, SessionId, Href, DateTimeVisited, CreatedAt) "
        "VALUES (?, ?, '/v/Y', '2026-05-13 00:00:00', '2026-05-13 00:00:01')",
        (_BIG_SEQ, _BIG_SID),
    )
    seed.execute(
        "INSERT INTO MovieHistoryAudit "
        "(TargetId, Action, OldRowJson, SessionId, DateTimeCreated) "
        "VALUES (1, 'INSERT', '{}', ?, '2026-05-13 00:00:00')",
        (_BIG_SID,),
    )
    seed.commit()
    seed.close()

    conn = sqlite3.connect(db_path)
    try:
        db_mod._migrate_session_id_to_text(conn)
    finally:
        conn.close()

    chk = sqlite3.connect(db_path)
    chk.row_factory = sqlite3.Row
    try:
        assert _column_type(chk, "MovieHistory", "SessionId") == "TEXT"
        assert _column_type(chk, "MovieHistoryAudit", "SessionId") == "TEXT"
        assert _column_type(chk, "PendingMovieHistoryWrites", "SessionId") == "TEXT"
        assert _column_type(chk, "PendingMovieHistoryWrites", "Seq") == "TEXT"

        row = chk.execute(
            "SELECT Seq, SessionId FROM PendingMovieHistoryWrites"
        ).fetchone()
        assert int(row["Seq"]) == _BIG_SEQ
        assert int(row["SessionId"]) == _BIG_SID

        audit = chk.execute(
            "SELECT SessionId FROM MovieHistoryAudit"
        ).fetchone()
        assert int(audit["SessionId"]) == _BIG_SID
    finally:
        chk.close()


def test_migration_is_idempotent(tmp_path):
    """Running the migration twice on the same DB is a no-op the second time."""
    db_path = str(tmp_path / "idempotent.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_REPORTS_DDL)
    seed.commit()
    seed.close()

    conn = sqlite3.connect(db_path)
    try:
        db_mod._migrate_session_id_to_text(conn)
        # Second run must not error or change types again.
        db_mod._migrate_session_id_to_text(conn)
    finally:
        conn.close()

    chk = sqlite3.connect(db_path)
    try:
        assert _column_type(chk, "ReportSessions", "Id") == "TEXT"
        assert _column_type(chk, "ReportMovies", "SessionId") == "TEXT"
    finally:
        chk.close()


def test_new_writes_after_migration_use_text_affinity(tmp_path):
    """After the migration a TEXT INSERT survives without coercion to int."""
    db_path = str(tmp_path / "post_migration.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_REPORTS_DDL)
    seed.commit()
    seed.close()

    conn = sqlite3.connect(db_path)
    try:
        db_mod._migrate_session_id_to_text(conn)
    finally:
        conn.close()

    new_sid = "20260513T120000.000000Z-cafe-0001"
    chk = sqlite3.connect(db_path)
    try:
        chk.execute(
            "INSERT INTO ReportSessions (Id, ReportType, ReportDate, "
            "CsvFilename, DateTimeCreated, Status) "
            "VALUES (?, 'daily', '20260513', 'new.csv', "
            "'2026-05-13 12:00:00', 'in_progress')",
            (new_sid,),
        )
        chk.commit()
        row = chk.execute(
            "SELECT Id FROM ReportSessions WHERE CsvFilename='new.csv'"
        ).fetchone()
        assert row[0] == new_sid
        assert isinstance(row[0], str)
    finally:
        chk.close()


# ── v13 regression: AUTOINCREMENT variant ─────────────────────────────

_LEGACY_HISTORY_AUTOINCREMENT_DDL = """
CREATE TABLE SchemaVersion (Version INTEGER NOT NULL);
INSERT INTO SchemaVersion (Version) VALUES (11);

CREATE TABLE MovieHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    Href TEXT NOT NULL UNIQUE,
    SessionId INTEGER
);

CREATE TABLE MovieHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL
);

CREATE TABLE PendingMovieHistoryWrites (
    Seq INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL,
    Href TEXT NOT NULL,
    VideoCode TEXT,
    DateTimeVisited TEXT NOT NULL,
    CreatedAt TEXT NOT NULL,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE PendingTorrentHistoryWrites (
    Seq INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL,
    Href TEXT NOT NULL,
    VideoCode TEXT,
    Category TEXT NOT NULL,
    SubtitleIndicator INTEGER NOT NULL,
    CensorIndicator INTEGER NOT NULL,
    MagnetUri TEXT,
    Size TEXT,
    FileCount INTEGER,
    ResolutionType INTEGER,
    DateTimeVisited TEXT NOT NULL,
    CreatedAt TEXT NOT NULL,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
);
"""


def test_autoincrement_seq_is_migrated_to_text(tmp_path):
    """The original 2026-05-09 DDL used ``Seq INTEGER PRIMARY KEY
    AUTOINCREMENT``.  The v12 regex only matched ``NOT NULL`` and left
    Seq as INTEGER, causing ``datatype mismatch`` on TEXT snowflake
    inserts.  Verify the fixed regex converts both Pending tables.
    """
    db_path = str(tmp_path / "history_autoincrement.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_HISTORY_AUTOINCREMENT_DDL)
    seed.commit()
    seed.close()

    conn = sqlite3.connect(db_path)
    try:
        db_mod._migrate_session_id_to_text(conn)
    finally:
        conn.close()

    chk = sqlite3.connect(db_path)
    chk.row_factory = sqlite3.Row
    try:
        assert _column_type(chk, "PendingMovieHistoryWrites", "Seq") == "TEXT"
        assert _column_type(chk, "PendingMovieHistoryWrites", "SessionId") == "TEXT"
        assert _column_type(chk, "PendingTorrentHistoryWrites", "Seq") == "TEXT"
        assert _column_type(chk, "PendingTorrentHistoryWrites", "SessionId") == "TEXT"

        text_seq = "20260514T100000.000000Z-abcd-0001"
        text_sid = "20260514T100000.000000Z-abcd-0002"
        chk.execute(
            "INSERT INTO PendingMovieHistoryWrites "
            "(Seq, SessionId, Href, DateTimeVisited, CreatedAt) "
            "VALUES (?, ?, '/v/Z', '2026-05-14 10:00:00', '2026-05-14 10:00:01')",
            (text_seq, text_sid),
        )
        chk.commit()
        row = chk.execute(
            "SELECT Seq, SessionId FROM PendingMovieHistoryWrites"
        ).fetchone()
        assert row["Seq"] == text_seq
        assert row["SessionId"] == text_sid
    finally:
        chk.close()


def test_partial_v12_migration_repaired_at_v13(tmp_path):
    """Simulate the v12 partial migration where SessionId was converted to
    TEXT but Seq remained ``INTEGER PRIMARY KEY AUTOINCREMENT``.  A second
    migration run (as triggered at v13) must repair the Seq column.
    """
    db_path = str(tmp_path / "history_partial.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_HISTORY_AUTOINCREMENT_DDL)
    seed.execute("UPDATE SchemaVersion SET Version = 12")
    seed.commit()

    partially_migrated_ddl = seed.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='PendingMovieHistoryWrites'"
    ).fetchone()[0]
    assert "AUTOINCREMENT" in partially_migrated_ddl

    new_ddl = partially_migrated_ddl.replace(
        "SessionId INTEGER", "SessionId TEXT"
    )
    seed.execute("DROP TABLE PendingMovieHistoryWrites")
    seed.execute(new_ddl)
    seed.commit()

    partially_migrated_ddl_t = seed.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='PendingTorrentHistoryWrites'"
    ).fetchone()[0]
    new_ddl_t = partially_migrated_ddl_t.replace(
        "SessionId INTEGER", "SessionId TEXT"
    )
    seed.execute("DROP TABLE PendingTorrentHistoryWrites")
    seed.execute(new_ddl_t)
    seed.commit()
    seed.close()

    verify = sqlite3.connect(db_path)
    pmhw_ddl = verify.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='PendingMovieHistoryWrites'"
    ).fetchone()[0]
    assert "Seq INTEGER PRIMARY KEY AUTOINCREMENT" in pmhw_ddl
    assert "SessionId TEXT" in pmhw_ddl
    verify.close()

    conn = sqlite3.connect(db_path)
    try:
        db_mod._migrate_session_id_to_text(conn)
    finally:
        conn.close()

    chk = sqlite3.connect(db_path)
    chk.row_factory = sqlite3.Row
    try:
        assert _column_type(chk, "PendingMovieHistoryWrites", "Seq") == "TEXT"
        assert _column_type(chk, "PendingTorrentHistoryWrites", "Seq") == "TEXT"

        text_seq = "20260514T120000.000000Z-beef-0001"
        chk.execute(
            "INSERT INTO PendingMovieHistoryWrites "
            "(Seq, SessionId, Href, DateTimeVisited, CreatedAt) "
            "VALUES (?, 'sid', '/v/W', '2026-05-14', '2026-05-14')",
            (text_seq,),
        )
        chk.commit()
        row = chk.execute(
            "SELECT Seq FROM PendingMovieHistoryWrites"
        ).fetchone()
        assert row["Seq"] == text_seq
        assert isinstance(row["Seq"], str)
    finally:
        chk.close()


def test_autoincrement_seq_insert_fails_without_fix(tmp_path):
    """Without the migration, inserting a TEXT snowflake into
    ``Seq INTEGER PRIMARY KEY AUTOINCREMENT`` raises ``IntegrityError:
    datatype mismatch`` — the exact production crash.
    """
    db_path = str(tmp_path / "history_unfixed.db")
    seed = sqlite3.connect(db_path)
    seed.executescript(_LEGACY_HISTORY_AUTOINCREMENT_DDL)
    seed.commit()
    seed.close()

    conn = sqlite3.connect(db_path)
    text_seq = "20260514T100000.000000Z-dead-0001"
    with pytest.raises(sqlite3.IntegrityError, match="datatype mismatch"):
        conn.execute(
            "INSERT INTO PendingMovieHistoryWrites "
            "(Seq, SessionId, Href, DateTimeVisited, CreatedAt) "
            "VALUES (?, 123, '/v/X', '2026-05-14', '2026-05-14')",
            (text_seq,),
        )
    conn.close()
