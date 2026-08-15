"""ReportMovies/ReportTorrents FK must agree across SQLite and D1.

Regression tests for ``db_insert_report_rows``, which used to read the
new ``ReportMovies.Id`` back from ``cur.lastrowid``. Under
``STORAGE_BACKEND=dual`` that cursor surfaces the *SQLite* rowid
(``DualCursor.lastrowid``), while D1 keeps its own AUTOINCREMENT counter.
Any past asymmetric INSERT leaves the two counters permanently offset, so
the SQLite rowid used as ``ReportTorrents.ReportMovieId`` pointed at a
different movie on D1 — and because that Id usually *does* exist there,
the foreign key was satisfied and nothing raised.

The D1 leg is simulated with a second real SQLite database whose
``ReportMovies`` counter is deliberately ahead of the mirror's.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.storage.db import _db_reports as _reports  # noqa: E402
from javdb.storage.dual_connection import (  # noqa: E402
    APPLICATION_GENERATED_ID_PK_COLUMN,
    DualConnection,
)

_DDL = """
CREATE TABLE ReportSessions (Id TEXT PRIMARY KEY, ReportType TEXT);
CREATE TABLE ReportMovies (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL REFERENCES ReportSessions(Id),
    Href TEXT, VideoCode TEXT, Page INTEGER, Actor TEXT,
    Rate REAL, CommentNumber INTEGER
);
CREATE TABLE ReportTorrents (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ReportMovieId INTEGER NOT NULL REFERENCES ReportMovies(Id),
    VideoCode TEXT, MagnetUri TEXT, SubtitleIndicator INTEGER,
    CensorIndicator INTEGER, ResolutionType INTEGER, Size TEXT, FileCount INTEGER
);
"""


def _dict_row(row):
    return None if row is None else {k: row[k] for k in row.keys()}


class _FakeD1Cursor:
    """D1Cursor-compatible wrapper: dict rows + ``lastrowid``."""

    def __init__(self, cur):
        self.lastrowid = cur.lastrowid
        self.rowcount = cur.rowcount
        self._rows = [_dict_row(r) for r in cur.fetchall()]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeD1Connection:
    """D1Connection stand-in backed by a real SQLite file."""

    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None

    def execute(self, sql, params=(), *, policy=None):
        return _FakeD1Cursor(self._conn.execute(sql, tuple(params)))

    def executemany(self, sql, seq_of_params):
        return _FakeD1Cursor(
            self._conn.executemany(sql, [tuple(p) for p in seq_of_params])
        )

    def commit(self):
        self._conn.commit()
        return []

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _open(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    return conn


@pytest.fixture()
def dual_backends(tmp_path, monkeypatch):
    """Yield (dual_conn, sqlite_conn, d1_conn) with offset D1 counters.

    The D1 leg already holds five ``ReportMovies`` rows from an older
    session, so its AUTOINCREMENT counter is 5 ahead of the mirror's —
    the documented post-migration steady state.
    """
    local = _open(str(tmp_path / "local.db"))
    d1 = _open(str(tmp_path / "d1.db"))

    d1.execute("INSERT INTO ReportSessions (Id, ReportType) VALUES ('OLD', 'daily')")
    for i in range(5):
        d1.execute(
            "INSERT INTO ReportMovies (SessionId, VideoCode) VALUES ('OLD', ?)",
            (f"OLD-{i}",),
        )
    for conn in (local, d1):
        conn.execute(
            "INSERT INTO ReportSessions (Id, ReportType) VALUES ('S1', 'daily')"
        )
        conn.commit()

    dual = DualConnection(local, _FakeD1Connection(d1), logical_name="reports")

    _reports._ensure_imports()

    @contextlib.contextmanager
    def _fake_get_db(_path=None):
        yield dual
        dual.commit()

    monkeypatch.setattr(_reports, "_get_db", _fake_get_db)
    try:
        yield dual, local, d1
    finally:
        local.close()
        d1.close()


_ROW = {
    "href": "/v/AAA111",
    "video_code": "ABC-123",
    "page": "1",
    "actor": "Someone",
    "rate": "4.5",
    "comment_number": "100",
    "subtitle": "magnet:?xt=urn:btih:DEADBEEF",
    "size_subtitle": "5.2GB",
    "file_count_subtitle": 3,
    "resolution_subtitle": 1080,
}


def test_torrent_fk_matches_movie_id_on_both_backends(dual_backends):
    """The FK must resolve to *this* session's movie on the D1 leg too."""
    _dual, local, d1 = dual_backends

    assert _reports.db_insert_report_rows("S1", [dict(_ROW)]) == 1

    for name, conn in (("sqlite", local), ("d1", d1)):
        movie_id = conn.execute(
            "SELECT Id FROM ReportMovies WHERE SessionId='S1'"
        ).fetchone()["Id"]
        torrents = conn.execute(
            "SELECT ReportMovieId FROM ReportTorrents"
        ).fetchall()
        assert torrents, f"{name}: no torrent row written"
        for t in torrents:
            assert t["ReportMovieId"] == movie_id, (
                f"{name}: ReportTorrents.ReportMovieId={t['ReportMovieId']} "
                f"does not point at this session's ReportMovies.Id={movie_id}"
            )
        parent = conn.execute(
            "SELECT SessionId FROM ReportMovies WHERE Id=?", (movie_id,)
        ).fetchone()
        assert parent["SessionId"] == "S1", (
            f"{name}: torrent attached to session {parent['SessionId']!r}"
        )


def test_movie_id_is_identical_on_both_backends(dual_backends):
    """Both legs must store the same application-generated ``Id``.

    Reading the id back from ``cur.lastrowid`` yielded 1 locally and 6 on
    the offset D1 leg; an explicit Id keeps the row addressable by the
    same value on either backend (rollback, reconcile and the API all
    join on it).
    """
    _dual, local, d1 = dual_backends

    _reports.db_insert_report_rows("S1", [dict(_ROW)])

    local_id = local.execute(
        "SELECT Id FROM ReportMovies WHERE SessionId='S1'"
    ).fetchone()["Id"]
    d1_id = d1.execute(
        "SELECT Id FROM ReportMovies WHERE SessionId='S1'"
    ).fetchone()["Id"]
    assert local_id == d1_id
    # Application-generated snowflake: must stay < 2**53 for D1's JSON
    # (IEEE-754 double) transport, and must not be a bare AUTOINCREMENT
    # rowid continuing either backend's counter.
    assert local_id > 5
    assert local_id < 2 ** 53


def test_multiple_rows_do_not_share_a_movie_id(dual_backends):
    """Each movie gets its own Id, and its torrents follow it."""
    _dual, local, d1 = dual_backends

    rows = []
    for code in ("ABC-1", "ABC-2", "ABC-3"):
        row = dict(_ROW)
        row["video_code"] = code
        row["href"] = f"/v/{code}"
        row["subtitle"] = f"magnet:?xt=urn:btih:{code}"
        rows.append(row)

    assert _reports.db_insert_report_rows("S1", rows) == 3

    for name, conn in (("sqlite", local), ("d1", d1)):
        pairs = conn.execute(
            "SELECT rm.VideoCode AS movie_code, rt.VideoCode AS torrent_code "
            "FROM ReportTorrents rt "
            "JOIN ReportMovies rm ON rm.Id = rt.ReportMovieId"
        ).fetchall()
        assert len(pairs) == 3, f"{name}: expected 3 joined torrents"
        for p in pairs:
            assert p["movie_code"] == p["torrent_code"], (
                f"{name}: torrent {p['torrent_code']} joined movie "
                f"{p['movie_code']}"
            )


def test_reportmovies_is_registered_as_application_generated_id():
    """Guard the invariant so a future caller cannot silently regress it.

    With ``ReportMovies`` in the guarded map, an INSERT that omits ``Id``
    and disagrees across backends raises ``DualWriteIdMismatchError``
    instead of corrupting the D1 leg.
    """
    assert APPLICATION_GENERATED_ID_PK_COLUMN.get("ReportMovies") == "Id"


def test_insert_supplies_explicit_id_column(dual_backends):
    """The emitted SQL must carry ``Id`` so the dual guard stands down."""
    dual, _local, _d1 = dual_backends
    seen = []
    original = dual.execute

    def _spy(sql, params=(), **kwargs):
        seen.append(sql)
        return original(sql, params, **kwargs)

    dual.execute = _spy  # type: ignore[method-assign]
    _reports.db_insert_report_rows("S1", [dict(_ROW)])

    movie_inserts = [s for s in seen if "INSERT INTO ReportMovies" in s]
    assert movie_inserts, "no ReportMovies INSERT observed"
    for sql in movie_inserts:
        cols = sql.split("(", 1)[1].split(")", 1)[0]
        assert "Id" in [c.strip() for c in cols.split(",")], (
            f"ReportMovies INSERT omits the explicit Id column: {sql}"
        )
