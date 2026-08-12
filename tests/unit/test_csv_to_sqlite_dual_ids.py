"""csv_to_sqlite must write cross-backend-stable Ids, not AUTOINCREMENT ones.

``javdb/migrations/tools/csv_to_sqlite.py`` writes through the
backend-routed ``get_db``, so under ``STORAGE_BACKEND=dual`` every INSERT
lands on both SQLite and D1. It used to let both legs allocate their own
AUTOINCREMENT id and then reuse the *SQLite* value as a foreign key:

* ``migrate_single_csv`` took ``cur.lastrowid`` after inserting
  ``ReportMovies`` and used it as ``ReportTorrents.ReportMovieId``.
* ``migrate_history`` re-read ``MovieHistory.Id`` with a ``SELECT`` (which
  ``DualConnection`` routes to *D1*) and used it as
  ``TorrentHistory.MovieHistoryId``.

Either way the FK pointed at a different movie on one of the two
backends — and because the stale id usually exists there, the foreign key
was satisfied and nothing raised. Both sites now supply an explicit
``generate_integer_id()`` value, the same fix ``db_insert_report_rows``
received (see ``test_report_rows_dual_movie_id.py``).

The D1 leg is simulated with a second real SQLite database whose
AUTOINCREMENT counters are deliberately ahead of the mirror's.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import javdb.storage.db as storage_db  # noqa: E402
from javdb.storage.dual_connection import DualConnection  # noqa: E402

_DDL = """
CREATE TABLE ReportSessions (
    Id TEXT PRIMARY KEY, ReportType TEXT, ReportDate TEXT, UrlType TEXT,
    DisplayName TEXT, Url TEXT, StartPage INTEGER, EndPage INTEGER,
    CsvFilename TEXT, DateTimeCreated TEXT
);
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
CREATE TABLE MovieHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL, Href TEXT NOT NULL UNIQUE,
    ActorName TEXT, ActorGender TEXT, ActorLink TEXT, SupportingActors TEXT,
    DateTimeCreated TEXT, DateTimeUpdated TEXT, DateTimeVisited TEXT,
    PerfectMatchIndicator INTEGER, HiResIndicator INTEGER, SessionId TEXT
);
CREATE TABLE TorrentHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    MovieHistoryId INTEGER NOT NULL REFERENCES MovieHistory(Id),
    MagnetUri TEXT, SubtitleIndicator INTEGER, CensorIndicator INTEGER,
    ResolutionType INTEGER, Size TEXT, FileCount INTEGER,
    DateTimeCreated TEXT, DateTimeUpdated TEXT, SessionId TEXT
);
CREATE UNIQUE INDEX uq_torrent_type
    ON TorrentHistory(MovieHistoryId, SubtitleIndicator, CensorIndicator);
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

    The D1 leg already holds rows from older sessions, so its
    ``ReportMovies`` / ``MovieHistory`` AUTOINCREMENT counters run ahead of
    the mirror's — the documented post-migration steady state.
    """
    # Keep any drift-metric sink inside tmp_path instead of the repo's reports/.
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    local = _open(str(tmp_path / "local.db"))
    d1 = _open(str(tmp_path / "d1.db"))

    d1.execute(
        "INSERT INTO ReportSessions (Id, ReportType) VALUES ('OLD', 'daily')"
    )
    for i in range(5):
        d1.execute(
            "INSERT INTO ReportMovies (SessionId, VideoCode) VALUES ('OLD', ?)",
            (f"OLD-{i}",),
        )
        d1.execute(
            "INSERT INTO MovieHistory (VideoCode, Href) VALUES (?, ?)",
            (f"OLD-{i}", f"https://javdb.com/v/OLD{i}"),
        )
    d1.commit()
    local.commit()

    dual = DualConnection(local, _FakeD1Connection(d1), logical_name="reports")

    @contextlib.contextmanager
    def _fake_get_db(_path=None):
        yield dual
        dual.commit()

    monkeypatch.setattr(storage_db, "get_db", _fake_get_db)
    try:
        yield dual, local, d1
    finally:
        local.close()
        d1.close()


_REPORT_HEADER = (
    'href,video_code,page,actor,rate,comment_number,'
    'hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle,'
    'size_hacked_subtitle,size_hacked_no_subtitle,size_subtitle,size_no_subtitle\n'
)


def _write_report_csv(tmp_path, name="Javdb_TodayTitle_20240101.csv"):
    path = tmp_path / name
    path.write_text(
        _REPORT_HEADER
        + '/v/AAA111,ABC-123,1,Someone,4.5,100,,,magnet:?xt=urn:btih:AAA,,,,5.2GB,\n',
        encoding='utf-8',
    )
    return str(path)


def _write_history_csv(tmp_path, name="parsed_movies_history.csv"):
    path = tmp_path / name
    path.write_text(
        'href,video_code,create_datetime,update_datetime,'
        'hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle,'
        'size_hacked_subtitle,size_hacked_no_subtitle,size_subtitle,size_no_subtitle\n'
        '/v/BBB222,DEF-456,2026-05-01 00:00:00,2026-05-02 00:00:00,'
        ',,magnet:?xt=urn:btih:BBB,,,,4.0GB,\n',
        encoding='utf-8',
    )
    return str(path)


def test_report_movie_id_is_identical_on_both_backends(dual_backends, tmp_path):
    """``migrate_single_csv`` must store the same ReportMovies.Id everywhere."""
    from javdb.migrations.tools.csv_to_sqlite import migrate_single_csv

    _dual, local, d1 = dual_backends
    csv_path = _write_report_csv(tmp_path)

    result = migrate_single_csv(
        csv_path, 'Javdb_TodayTitle_20240101.csv',
        is_adhoc=False, db_path=str(tmp_path / "unused.db"), dry_run=False,
    )
    session_id = result['session_id']

    local_id = local.execute(
        "SELECT Id FROM ReportMovies WHERE SessionId=?", (session_id,)
    ).fetchone()["Id"]
    d1_id = d1.execute(
        "SELECT Id FROM ReportMovies WHERE SessionId=?", (session_id,)
    ).fetchone()["Id"]
    assert local_id == d1_id
    # Application-generated snowflake: not a continuation of either backend's
    # counter, and < 2**53 so it survives D1's JSON (IEEE-754) transport.
    assert local_id > 5
    assert local_id < 2 ** 53


def test_report_torrent_fk_points_at_this_session_on_both_backends(
    dual_backends, tmp_path
):
    """The ReportTorrents FK must resolve to this session's movie on D1 too."""
    from javdb.migrations.tools.csv_to_sqlite import migrate_single_csv

    _dual, local, d1 = dual_backends
    csv_path = _write_report_csv(tmp_path)

    result = migrate_single_csv(
        csv_path, 'Javdb_TodayTitle_20240101.csv',
        is_adhoc=False, db_path=str(tmp_path / "unused.db"), dry_run=False,
    )
    session_id = result['session_id']

    for name, conn in (("sqlite", local), ("d1", d1)):
        torrents = conn.execute(
            "SELECT ReportMovieId FROM ReportTorrents"
        ).fetchall()
        assert torrents, f"{name}: no torrent row written"
        for t in torrents:
            parent = conn.execute(
                "SELECT SessionId FROM ReportMovies WHERE Id=?",
                (t["ReportMovieId"],),
            ).fetchone()
            assert parent is not None, (
                f"{name}: ReportMovieId={t['ReportMovieId']} has no movie row"
            )
            assert parent["SessionId"] == session_id, (
                f"{name}: torrent attached to session {parent['SessionId']!r}"
            )


def test_history_movie_id_and_fk_agree_on_both_backends(dual_backends, tmp_path):
    """``migrate_history`` must not derive the FK from one backend's counter.

    The old code re-read ``MovieHistory.Id`` with a ``SELECT``, which
    ``DualConnection`` routes to D1 — so the SQLite leg's
    ``TorrentHistory.MovieHistoryId`` got the D1-side id (6 here, an
    unrelated older movie) instead of its own.
    """
    from javdb.migrations.tools.csv_to_sqlite import migrate_history

    _dual, local, d1 = dual_backends
    csv_path = _write_history_csv(tmp_path)

    assert migrate_history(csv_path, str(tmp_path / "unused.db"), dry_run=False) == 1

    ids = {}
    for name, conn in (("sqlite", local), ("d1", d1)):
        movie = conn.execute(
            "SELECT Id FROM MovieHistory WHERE VideoCode='DEF-456'"
        ).fetchone()
        assert movie is not None, f"{name}: movie row missing"
        ids[name] = movie["Id"]
        torrents = conn.execute(
            "SELECT MovieHistoryId FROM TorrentHistory"
        ).fetchall()
        assert torrents, f"{name}: no torrent row written"
        for t in torrents:
            assert t["MovieHistoryId"] == movie["Id"], (
                f"{name}: TorrentHistory.MovieHistoryId={t['MovieHistoryId']} "
                f"does not point at MovieHistory.Id={movie['Id']}"
            )

    assert ids["sqlite"] == ids["d1"]
    assert ids["sqlite"] > 5
    assert ids["sqlite"] < 2 ** 53


def test_history_reimport_clears_stale_rows_on_both_backends(dual_backends, tmp_path):
    """The pre-insert cleanup must resolve ids per backend, not once via D1.

    Both legs already hold the movie, but under different ids (the
    AUTOINCREMENT counters diverged before the explicit-Id fix landed).
    Deleting by a single pre-fetched id — which ``DualConnection`` reads
    from D1 — would clean D1 and leave the SQLite mirror's rows orphaned,
    so the DELETEs resolve the id from the ``Href`` business key instead.
    """
    from javdb.migrations.tools.csv_to_sqlite import migrate_history

    _dual, local, d1 = dual_backends
    href = "https://javdb.com/v/BBB222"

    # Same movie, different id on each leg, each with a stale torrent child.
    for conn, movie_id, torrent_id in ((local, 101, 901), (d1, 202, 902)):
        conn.execute(
            "INSERT INTO MovieHistory (Id, VideoCode, Href) VALUES (?, 'DEF-456', ?)",
            (movie_id, href),
        )
        conn.execute(
            """INSERT INTO TorrentHistory
               (Id, MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator)
               VALUES (?, ?, 'magnet:?xt=urn:btih:STALE', 1, 1)""",
            (torrent_id, movie_id),
        )
        conn.commit()

    csv_path = _write_history_csv(tmp_path)
    assert migrate_history(csv_path, str(tmp_path / "unused.db"), dry_run=False) == 1

    for name, conn, stale_movie_id in (("sqlite", local, 101), ("d1", d1, 202)):
        movies = conn.execute(
            "SELECT Id FROM MovieHistory WHERE Href=?", (href,)
        ).fetchall()
        assert len(movies) == 1, f"{name}: expected exactly one row for {href}"
        new_id = movies[0]["Id"]
        assert new_id != stale_movie_id, f"{name}: stale movie row survived"

        stale = conn.execute(
            "SELECT Id FROM TorrentHistory WHERE MovieHistoryId=?", (stale_movie_id,)
        ).fetchall()
        assert not stale, (
            f"{name}: stale TorrentHistory rows still point at "
            f"MovieHistoryId={stale_movie_id}"
        )

        torrents = conn.execute(
            "SELECT MovieHistoryId, MagnetUri FROM TorrentHistory"
        ).fetchall()
        assert torrents, f"{name}: no torrent row written"
        for t in torrents:
            assert t["MovieHistoryId"] == new_id, (
                f"{name}: torrent points at {t['MovieHistoryId']}, not {new_id}"
            )
            assert "STALE" not in t["MagnetUri"], f"{name}: stale magnet survived"


@pytest.mark.parametrize(
    "table",
    ["ReportMovies", "MovieHistory", "TorrentHistory"],
)
def test_guarded_inserts_supply_explicit_id_column(dual_backends, tmp_path, table):
    """The emitted SQL must carry ``Id`` so the dual guard stands down."""
    from javdb.migrations.tools.csv_to_sqlite import (
        migrate_history,
        migrate_single_csv,
    )

    dual, _local, _d1 = dual_backends
    seen: list[str] = []
    original = dual.execute

    def _spy(sql, params=(), **kwargs):
        seen.append(sql)
        return original(sql, params, **kwargs)

    dual.execute = _spy  # type: ignore[method-assign]

    migrate_single_csv(
        _write_report_csv(tmp_path), 'Javdb_TodayTitle_20240101.csv',
        is_adhoc=False, db_path=str(tmp_path / "unused.db"), dry_run=False,
    )
    migrate_history(
        _write_history_csv(tmp_path), str(tmp_path / "unused.db"), dry_run=False
    )

    inserts = [s for s in seen if f"INTO {table}\n" in s or f"INTO {table} " in s]
    assert inserts, f"no {table} INSERT observed"
    for sql in inserts:
        cols = sql.split("(", 1)[1].split(")", 1)[0]
        assert "Id" in [c.strip() for c in cols.split(",")], (
            f"{table} INSERT omits the explicit Id column: {sql}"
        )
