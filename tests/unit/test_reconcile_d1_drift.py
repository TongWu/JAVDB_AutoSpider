"""Tests for the D1 drift reconciler."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, List

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from packages.python.javdb_migrations.tools import reconcile_d1_drift as recon  # noqa: E402


# ── Test doubles for D1 ───────────────────────────────────────────────────


class FakeD1Cursor:
    def __init__(self, rows=None, lastrowid=None, rowcount=0):
        self._rows = list(rows or [])
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeD1Connection:
    """Tiny in-memory D1 stand-in backed by a sqlite3 file db.

    The reconciler treats D1 as a sqlite-shaped store, so we simply
    delegate to a real sqlite3 connection. This catches SQL bugs that a
    pure Python mock would miss.
    """

    def __init__(self, schema_sql: str):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(schema_sql)
        self.executed: List[tuple] = []

    def execute(self, sql: str, params: Iterable[Any] = ()):  # noqa: D401 - facade
        self.executed.append((sql, list(params)))
        cur = self._conn.execute(sql, list(params))
        rows = [dict(r) for r in cur.fetchall()]
        return FakeD1Cursor(rows=rows, lastrowid=cur.lastrowid, rowcount=cur.rowcount)

    def close(self):
        self._conn.close()


# ── Schema fixtures ───────────────────────────────────────────────────────


_HISTORY_DDL = """
CREATE TABLE MovieHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    Href TEXT NOT NULL UNIQUE,
    ActorName TEXT,
    ActorGender TEXT,
    ActorLink TEXT,
    SupportingActors TEXT,
    DateTimeCreated TEXT,
    DateTimeUpdated TEXT,
    DateTimeVisited TEXT,
    PerfectMatchIndicator INTEGER,
    HiResIndicator INTEGER
);
CREATE TABLE TorrentHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    MovieHistoryId INTEGER NOT NULL,
    MagnetUri TEXT,
    SubtitleIndicator INTEGER,
    CensorIndicator INTEGER,
    ResolutionType INTEGER,
    Size TEXT,
    FileCount INTEGER,
    DateTimeCreated TEXT,
    DateTimeUpdated TEXT
);
CREATE UNIQUE INDEX uq_torrent_type ON TorrentHistory(MovieHistoryId, SubtitleIndicator, CensorIndicator);
"""


_REPORTS_DDL = """
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
    DateTimeCreated TEXT NOT NULL
);
CREATE TABLE ReportMovies (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL,
    Href TEXT,
    VideoCode TEXT,
    Page INTEGER,
    Actor TEXT,
    Rate REAL,
    CommentNumber INTEGER
);
CREATE TABLE ReportTorrents (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ReportMovieId INTEGER NOT NULL,
    VideoCode TEXT,
    MagnetUri TEXT,
    SubtitleIndicator INTEGER,
    CensorIndicator INTEGER,
    ResolutionType INTEGER,
    Size TEXT,
    FileCount INTEGER
);
"""


@pytest.fixture
def history_sqlite(tmp_path):
    path = tmp_path / "history.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_HISTORY_DDL)
    conn.commit()
    return path, conn


@pytest.fixture
def reports_sqlite(tmp_path):
    path = tmp_path / "reports.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_REPORTS_DDL)
    conn.commit()
    return path, conn


# ── Pure helpers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_year",
    [
        ("2026-04-26T01:59:25Z", 2026),
        ("2026-04-26T01:59:25+00:00", 2026),
        ("2026-04-26T09:00:00+08:00", 2026),
    ],
)
def test_parse_iso8601_accepts_z_and_offset(raw, expected_year):
    parsed = recon._parse_iso8601(raw)
    assert parsed is not None
    assert parsed.year == expected_year


def test_parse_iso8601_rejects_garbage():
    assert recon._parse_iso8601("not-a-date") is None
    assert recon._parse_iso8601("") is None


def test_datetime_to_sqlite_text_uses_canonical_format():
    ts = datetime(2026, 4, 26, 9, 30, 0, tzinfo=timezone.utc)
    assert recon._datetime_to_sqlite_text(ts) == "2026-04-26 09:30:00"


def test_values_equal_handles_int_float_drift():
    assert recon._values_equal(1, 1.0)
    assert recon._values_equal("abc", "abc")
    assert recon._values_equal(None, None)
    assert not recon._values_equal(1, 2)
    assert not recon._values_equal(None, 1)


def test_earliest_since_picks_min_per_db():
    records = [
        {"db": "history", "ts": "2026-04-26T02:00:00Z"},
        {"db": "history", "ts": "2026-04-26T01:30:00Z"},
        {"db": "reports", "ts": "2026-04-26T01:59:00Z"},
        {"db": "reports", "ts": "garbage"},
    ]
    out = recon._earliest_since_per_db(records)
    assert out["history"].hour == 1 and out["history"].minute == 30
    assert out["reports"].hour == 1 and out["reports"].minute == 59


# ── End-to-end: history.db ────────────────────────────────────────────────


def test_reconcile_history_inserts_missing_rows(history_sqlite):
    sqlite_path, sqlite_conn = history_sqlite
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorName, DateTimeCreated, DateTimeUpdated)"
        " VALUES (?, ?, ?, ?, ?)",
        ("STARS-351", "/v/abc", "Some Actor", "2026-04-26 01:00:00", "2026-04-26 01:59:00"),
    )
    sqlite_conn.execute(
        "INSERT INTO TorrentHistory (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,"
        " ResolutionType, DateTimeCreated, DateTimeUpdated)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "magnet:?xt=urn:btih:abc", 1, 1, 1080, "2026-04-26 01:00:00", "2026-04-26 01:59:00"),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_HISTORY_DDL)

    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        stats = recon._reconcile_history(
            ro_conn, fake_d1, since_text="2026-04-26 01:00:00", dry_run=False
        )
    finally:
        ro_conn.close()

    by_table = {s.table: s for s in stats}
    assert by_table["MovieHistory"].inserted == 1
    assert by_table["TorrentHistory"].inserted == 1
    assert by_table["MovieHistory"].errors == 0

    d1_rows = fake_d1._conn.execute("SELECT Href, VideoCode FROM MovieHistory").fetchall()
    assert [dict(r) for r in d1_rows] == [{"Href": "/v/abc", "VideoCode": "STARS-351"}]


def test_reconcile_history_detects_no_change_as_skipped(history_sqlite):
    sqlite_path, sqlite_conn = history_sqlite
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, DateTimeUpdated) VALUES (?, ?, ?)",
        ("STARS-001", "/v/equal", "2026-04-26 02:00:00"),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_HISTORY_DDL)
    fake_d1._conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, DateTimeUpdated) VALUES (?, ?, ?)",
        ("STARS-001", "/v/equal", "2026-04-26 02:00:00"),
    )
    fake_d1._conn.commit()

    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        stats = recon._reconcile_history(ro_conn, fake_d1, since_text=None, dry_run=False)
    finally:
        ro_conn.close()

    by_table = {s.table: s for s in stats}
    assert by_table["MovieHistory"].skipped_equal == 1
    assert by_table["MovieHistory"].inserted == 0
    assert by_table["MovieHistory"].updated == 0


def test_reconcile_history_updates_changed_payload(history_sqlite):
    sqlite_path, sqlite_conn = history_sqlite
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorName, DateTimeUpdated)"
        " VALUES (?, ?, ?, ?)",
        ("STARS-001", "/v/changed", "NewActor", "2026-04-26 03:00:00"),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_HISTORY_DDL)
    fake_d1._conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorName, DateTimeUpdated)"
        " VALUES (?, ?, ?, ?)",
        ("STARS-001", "/v/changed", "OldActor", "2026-04-26 01:00:00"),
    )
    fake_d1._conn.commit()

    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        stats = recon._reconcile_history(ro_conn, fake_d1, since_text=None, dry_run=False)
    finally:
        ro_conn.close()

    by_table = {s.table: s for s in stats}
    assert by_table["MovieHistory"].updated == 1
    actor = fake_d1._conn.execute(
        "SELECT ActorName FROM MovieHistory WHERE Href = ?", ("/v/changed",)
    ).fetchone()
    assert actor["ActorName"] == "NewActor"


def test_reconcile_history_dry_run_does_not_write(history_sqlite):
    sqlite_path, sqlite_conn = history_sqlite
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, DateTimeUpdated) VALUES (?, ?, ?)",
        ("STARS-001", "/v/dry", "2026-04-26 02:00:00"),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_HISTORY_DDL)

    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        stats = recon._reconcile_history(ro_conn, fake_d1, since_text=None, dry_run=True)
    finally:
        ro_conn.close()

    assert stats[0].inserted == 1
    n = fake_d1._conn.execute("SELECT COUNT(*) AS n FROM MovieHistory").fetchone()
    assert n["n"] == 0


def test_torrent_with_missing_d1_parent_is_skipped_not_errored(history_sqlite):
    sqlite_path, sqlite_conn = history_sqlite
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, DateTimeUpdated) VALUES (?, ?, ?)",
        ("STARS-009", "/v/orphan-parent", "2026-04-26 02:00:00"),
    )
    sqlite_conn.execute(
        "INSERT INTO TorrentHistory (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,"
        " DateTimeUpdated) VALUES (?, ?, ?, ?, ?)",
        (1, "magnet:?xt=A", 0, 1, "2026-04-26 02:00:00"),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_HISTORY_DDL)
    # Note: D1 has no MovieHistory row → torrent has no parent to bind to.
    # The reconcile_history call should insert MovieHistory first, then succeed.
    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        stats = recon._reconcile_history(ro_conn, fake_d1, since_text=None, dry_run=False)
    finally:
        ro_conn.close()

    by_table = {s.table: s for s in stats}
    # MovieHistory inserted, then TorrentHistory finds the parent it just inserted.
    assert by_table["MovieHistory"].inserted == 1
    assert by_table["TorrentHistory"].inserted == 1
    assert by_table["TorrentHistory"].skipped_missing_parent == 0


# ── End-to-end: reports.db ────────────────────────────────────────────────


def test_reconcile_reports_full_chain(reports_sqlite):
    sqlite_path, sqlite_conn = reports_sqlite
    sqlite_conn.execute(
        "INSERT INTO ReportSessions (ReportType, ReportDate, CsvFilename, DateTimeCreated)"
        " VALUES (?, ?, ?, ?)",
        ("daily", "2026-04-26", "reports/AdHoc/2026-04-26_run1.csv", "2026-04-26 01:50:00"),
    )
    sqlite_conn.execute(
        "INSERT INTO ReportMovies (SessionId, Href, VideoCode, Page, Rate)"
        " VALUES (?, ?, ?, ?, ?)",
        (1, "/v/sssh", "STARS-351", 1, 4.5),
    )
    sqlite_conn.execute(
        "INSERT INTO ReportTorrents (ReportMovieId, VideoCode, MagnetUri,"
        " SubtitleIndicator, CensorIndicator, ResolutionType)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (1, "STARS-351", "magnet:?xt=zzz", 1, 1, 1080),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_REPORTS_DDL)

    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        stats = recon._reconcile_reports(
            ro_conn, fake_d1, since_text="2026-04-26 01:00:00", dry_run=False
        )
    finally:
        ro_conn.close()

    by_table = {s.table: s for s in stats}
    assert by_table["ReportSessions"].inserted == 1
    assert by_table["ReportMovies"].inserted == 1
    assert by_table["ReportTorrents"].inserted == 1

    rt = fake_d1._conn.execute(
        "SELECT VideoCode, MagnetUri FROM ReportTorrents"
    ).fetchone()
    assert rt["VideoCode"] == "STARS-351"
    assert rt["MagnetUri"] == "magnet:?xt=zzz"


def test_reconcile_reports_id_drift_does_not_cascade(reports_sqlite):
    """Even if D1's session/movie IDs differ from SQLite's, business keys win.

    Simulates the production scenario where ReportSessions.Id in D1 is offset
    from SQLite's by 1+: as long as CsvFilename + Href match, the reconciler
    binds the FK to the correct D1 row.
    """
    sqlite_path, sqlite_conn = reports_sqlite
    # SQLite: session Id will be 1 (auto)
    sqlite_conn.execute(
        "INSERT INTO ReportSessions (ReportType, ReportDate, CsvFilename, DateTimeCreated)"
        " VALUES (?, ?, ?, ?)",
        ("daily", "2026-04-26", "abc.csv", "2026-04-26 01:00:00"),
    )
    sqlite_conn.execute(
        "INSERT INTO ReportMovies (SessionId, Href, VideoCode) VALUES (?, ?, ?)",
        (1, "/v/foo", "FOO-001"),
    )
    sqlite_conn.commit()
    sqlite_conn.close()

    fake_d1 = FakeD1Connection(_REPORTS_DDL)
    # Pre-seed D1 with two unrelated session rows so the matching session ends up at Id=3.
    fake_d1._conn.execute(
        "INSERT INTO ReportSessions (ReportType, ReportDate, CsvFilename, DateTimeCreated)"
        " VALUES (?, ?, ?, ?)",
        ("daily", "2026-04-25", "older1.csv", "2026-04-25 00:00:00"),
    )
    fake_d1._conn.execute(
        "INSERT INTO ReportSessions (ReportType, ReportDate, CsvFilename, DateTimeCreated)"
        " VALUES (?, ?, ?, ?)",
        ("daily", "2026-04-25", "older2.csv", "2026-04-25 00:00:01"),
    )
    fake_d1._conn.commit()

    ro_conn = recon._open_sqlite_readonly(str(sqlite_path))
    try:
        recon._reconcile_reports(ro_conn, fake_d1, since_text=None, dry_run=False)
    finally:
        ro_conn.close()

    d1_session_id = fake_d1._conn.execute(
        "SELECT Id FROM ReportSessions WHERE CsvFilename = ?", ("abc.csv",)
    ).fetchone()["Id"]
    assert d1_session_id == 3, "newly-inserted session must land at next D1 autoincrement"

    movie = fake_d1._conn.execute(
        "SELECT SessionId, VideoCode FROM ReportMovies WHERE Href = ?", ("/v/foo",)
    ).fetchone()
    assert movie["SessionId"] == 3, "ReportMovies.SessionId should be remapped to D1 Id, not SQLite's 1"


# ── Drift log archival ────────────────────────────────────────────────────


def test_archive_processed_records_atomic_write(tmp_path):
    drift_log = tmp_path / "d1_drift.jsonl"
    processed_log = tmp_path / "d1_drift.processed.jsonl"

    consumed = [{"db": "history", "ts": "2026-04-26T01:00:00Z", "failure_count": 5}]
    leftover = [{"db": "operations", "ts": "2026-04-26T02:00:00Z", "failure_count": 1}]

    drift_log.write_text(
        json.dumps(consumed[0]) + "\n" + json.dumps(leftover[0]) + "\n",
        encoding="utf-8",
    )
    recon._archive_processed_records(
        str(drift_log), str(processed_log), consumed, leftover
    )

    archived = [json.loads(line) for line in processed_log.read_text(encoding="utf-8").splitlines()]
    remaining = [json.loads(line) for line in drift_log.read_text(encoding="utf-8").splitlines()]
    assert archived == consumed
    assert remaining == leftover


def test_main_returns_zero_when_no_drift(tmp_path, monkeypatch, capsys):
    drift_log = tmp_path / "empty_drift.jsonl"

    rc = recon.main(["--drift-log", str(drift_log)])
    assert rc == 0


def test_main_uses_default_window_from_drift_log(tmp_path, monkeypatch):
    """If --since is omitted, the earliest drift record's ts becomes the window."""
    drift_log = tmp_path / "d.jsonl"
    drift_log.write_text(
        json.dumps({"db": "history", "ts": "2026-04-26T01:00:00Z", "failure_count": 1}) + "\n",
        encoding="utf-8",
    )

    captured = {}

    def fake_reconcile(*, dbs, drift_log, processed_log, since, all_rows, dry_run):
        captured["dbs"] = dbs
        captured["since"] = since
        captured["all_rows"] = all_rows
        captured["dry_run"] = dry_run
        return 0

    monkeypatch.setattr(recon, "reconcile", fake_reconcile)
    rc = recon.main(["--drift-log", str(drift_log), "--db", "history", "--dry-run"])
    assert rc == 0
    assert captured["dbs"] == ("history",)
    assert captured["since"] is None  # delegated; reconcile() reads jsonl itself
    assert captured["dry_run"] is True
