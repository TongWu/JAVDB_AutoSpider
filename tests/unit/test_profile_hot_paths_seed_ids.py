"""The hot-path profiler must stay sqlite-local and mint its own ids.

BFR-034 sweep. ``apps/cli/ops/profile_hot_paths.py`` seeds a throwaway
tempfile DB through ``get_db()``, which routes purely on
``STORAGE_BACKEND`` — the *path* argument plays no part. So under
``STORAGE_BACKEND=d1`` / ``dual`` the seeder was handed to the real backend
router (it stopped at ``_logical_name_for()``'s unmapped-path ``ValueError``,
which ``main()`` swallowed as ``FAILED``), and it read the new
``MovieHistory`` id back from ``cur.lastrowid`` to use as
``TorrentHistory.MovieHistoryId`` — the exact pattern BFR-034 removed, on
two tables that are guarded in
``dual_connection.APPLICATION_GENERATED_ID_PK_COLUMN``.

The fix forces the sqlite-only override for the whole DB benchmark and
supplies both ids explicitly.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.cli.ops import profile_hot_paths as prof  # noqa: E402
from javdb.storage.db._db_connection import current_backend  # noqa: E402

_OVERRIDE_ENV = "_STORAGE_BACKEND_INIT_OVERRIDE"


# ── _sqlite_only_backend ────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["dual", "d1"])
def test_sqlite_only_backend_forces_sqlite(monkeypatch, backend):
    """Inside the block the DB layer resolves to local SQLite."""
    monkeypatch.setenv("STORAGE_BACKEND", backend)
    monkeypatch.delenv(_OVERRIDE_ENV, raising=False)

    assert current_backend() == backend
    with prof._sqlite_only_backend():
        assert current_backend() == "sqlite"
    assert current_backend() == backend


def test_sqlite_only_backend_restores_absent_override(monkeypatch):
    """The override env var is removed again when it was not set before."""
    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    monkeypatch.delenv(_OVERRIDE_ENV, raising=False)

    with prof._sqlite_only_backend():
        assert os.environ[_OVERRIDE_ENV] == "sqlite"
    assert _OVERRIDE_ENV not in os.environ


def test_sqlite_only_backend_restores_previous_override(monkeypatch):
    """A pre-existing override is put back verbatim, not clobbered."""
    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    monkeypatch.setenv(_OVERRIDE_ENV, "dual")

    with prof._sqlite_only_backend():
        assert os.environ[_OVERRIDE_ENV] == "sqlite"
    assert os.environ[_OVERRIDE_ENV] == "dual"


def test_sqlite_only_backend_restores_on_exception(monkeypatch):
    """The override must not leak out of a failing benchmark."""
    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    monkeypatch.delenv(_OVERRIDE_ENV, raising=False)

    with pytest.raises(RuntimeError):
        with prof._sqlite_only_backend():
            raise RuntimeError("benchmark exploded")
    assert _OVERRIDE_ENV not in os.environ


# ── _seed_history ───────────────────────────────────────────────────────


class _RecordingConn:
    """Captures (sql, params) instead of writing anything."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        # Deliberately returns no cursor: a seeder that goes back to
        # ``cur.lastrowid`` fails here with AttributeError on None.
        return None


def test_seed_history_sql_supplies_explicit_ids(monkeypatch):
    """Both INSERTs list ``Id`` first and bind an application-generated value.

    The lighter of the two assertions: no DB is created, only the SQL and
    the bound parameters are inspected. It fails if anyone reintroduces the
    AUTOINCREMENT-plus-``lastrowid`` shape.
    """
    conn = _RecordingConn()

    @contextlib.contextmanager
    def _fake_get_db(_path=None):
        yield conn

    monkeypatch.setattr("javdb.storage.db.get_db", _fake_get_db)

    prof._seed_history("/nonexistent/profiler.db", 2)

    assert len(conn.calls) == 4  # 2 movies + 2 torrents
    movie_ids = []
    for idx in (0, 2):
        sql, params = conn.calls[idx]
        assert "INSERT INTO MovieHistory" in sql
        assert "(Id, Href, VideoCode" in sql
        movie_ids.append(params[0])
    torrent_ids = []
    for offset, idx in enumerate((1, 3)):
        sql, params = conn.calls[idx]
        assert "INSERT INTO TorrentHistory" in sql
        assert "(Id, MovieHistoryId," in sql
        torrent_ids.append(params[0])
        # The FK is the id the seeder just minted, not a rowid read back.
        assert params[1] == movie_ids[offset]

    ids = movie_ids + torrent_ids
    assert len(set(ids)) == len(ids), "ids must be unique"
    # 52-bit application snowflakes, never AUTOINCREMENT's 1/2/3, and always
    # below D1's JSON-safe ceiling.
    for value in ids:
        assert 2**32 < value < 2**53


def test_seed_history_writes_locally_under_dual_env(monkeypatch):
    """End-to-end: seeding works under ``STORAGE_BACKEND=dual`` and stays local.

    Pre-fix this raised ``ValueError: No D1 logical-name mapping for
    db_path=...`` because the profiler's tempfile went through the backend
    router. The rows are read back with a raw ``sqlite3`` connection, so a
    D1 / dual detour could not satisfy these assertions.
    """
    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    monkeypatch.delenv(_OVERRIDE_ENV, raising=False)

    with prof._sqlite_only_backend():
        db_path = prof._setup_in_memory_db()
        try:
            prof._seed_history(db_path, 3)

            raw = sqlite3.connect(db_path)
            raw.row_factory = sqlite3.Row
            try:
                movies = {
                    r["Id"]: r["Href"]
                    for r in raw.execute("SELECT Id, Href FROM MovieHistory")
                }
                torrents = raw.execute(
                    "SELECT Id, MovieHistoryId FROM TorrentHistory"
                ).fetchall()
            finally:
                raw.close()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(db_path)

    assert len(movies) == 3
    assert sorted(movies.values()) == [
        "/v/PROF-000000", "/v/PROF-000001", "/v/PROF-000002",
    ]
    for movie_id in movies:
        assert 2**32 < movie_id < 2**53
    assert len(torrents) == 3
    # One torrent per movie, each pointing at a movie that exists here.
    assert sorted(r["MovieHistoryId"] for r in torrents) == sorted(movies)
    for row in torrents:
        assert 2**32 < row["Id"] < 2**53
