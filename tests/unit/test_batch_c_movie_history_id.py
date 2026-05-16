"""Batch C (C.1 + C.2) — MovieHistory / TorrentHistory explicit-Id invariants.

C.1: All INSERT paths supply an application-generated integer Id so that
     SQLite and Cloudflare D1 auto-increment counters never diverge under
     STORAGE_BACKEND=dual.

C.2: Regression net — the dual-write guard detects drift when Id is absent,
     and passes cleanly when Id is explicitly supplied.
"""

from __future__ import annotations

import sqlite3
import sys
import os
from typing import List

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

import utils.infra.db as db_mod
from packages.python.javdb_platform.db import (
    _generate_integer_id,
    _INT_ID_EPOCH_BASE_MS,
)
from packages.python.javdb_platform.dual_connection import (
    APPLICATION_GENERATED_ID_TABLES,
    DualConnection,
    DualWriteIdMismatchError,
)


# ── _generate_integer_id() unit tests ────────────────────────────────────


def test_generate_integer_id_is_positive():
    assert _generate_integer_id() > 0


def test_generate_integer_id_within_d1_safe_range():
    """All generated IDs must stay below 2**53 (D1 JSON Number precision)."""
    _D1_MAX = 2**53
    ids = [_generate_integer_id() for _ in range(500)]
    assert all(v < _D1_MAX for v in ids), (
        f"ID(s) exceed D1-safe ceiling 2**53: {[v for v in ids if v >= _D1_MAX]}"
    )


def test_generate_integer_id_is_monotonic():
    """Rapid successive calls must produce strictly increasing values."""
    ids = [_generate_integer_id() for _ in range(200)]
    for a, b in zip(ids, ids[1:]):
        assert b >= a, f"Monotonicity broken: {a} → {b}"


def test_generate_integer_id_is_unique():
    """1 000 consecutive calls must produce no duplicates."""
    ids = [_generate_integer_id() for _ in range(1000)]
    assert len(set(ids)) == len(ids), "Duplicate IDs generated"


def test_generate_integer_id_large_enough_to_be_snowflake():
    """The raw value must be significantly larger than a typical AUTOINCREMENT
    counter (≫ 10^6), proving it is snowflake-based, not sequential-from-1."""
    # After 2026-01-01, relative_ms is at minimum ~11 500 000 ms;
    # with << 12 shift: value ≥ 11_500_000 * 4096 > 4.7 × 10^10.
    assert _generate_integer_id() > 10**10


# ── APPLICATION_GENERATED_ID_TABLES membership ───────────────────────────


@pytest.mark.parametrize("table", ["MovieHistory", "TorrentHistory"])
def test_history_tables_in_application_id_guard_set(table):
    """C.1: both live-history tables must be guarded against dual-mode Id drift."""
    assert table in APPLICATION_GENERATED_ID_TABLES


# ── INSERT paths produce snowflake-based Ids (SQLite smoke tests) ─────────


class TestMovieHistoryExplicitId:
    """Verify that each INSERT path writes an explicit (large) Id."""

    def _read_movie_id(self, href: str) -> int:
        from apps.api.parsers.common import movie_href_lookup_values
        path_href, abs_href = movie_href_lookup_values(href, "https://javdb.com")
        variants = [v for v in (path_href, abs_href, href) if v]
        ph = ",".join("?" for _ in variants)
        with db_mod.get_db() as conn:
            row = conn.execute(
                f"SELECT Id FROM MovieHistory WHERE Href IN ({ph})", variants
            ).fetchone()
        assert row is not None, f"No MovieHistory row for {href} (variants={variants})"
        return int(row["Id"])

    def _read_torrent_id(self, movie_id: int) -> List[int]:
        with db_mod.get_db() as conn:
            rows = conn.execute(
                "SELECT Id FROM TorrentHistory WHERE MovieHistoryId=?", (movie_id,)
            ).fetchall()
        return [int(r["Id"]) for r in rows]

    def test_db_upsert_history_movie_id_is_snowflake(self, _isolate_sqlite):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            db_mod.db_upsert_history(
                "/v/C1-UPSERT",
                "C1-UPSERT",
                magnet_links={"subtitle": "magnet:upsert"},
            )
        mid = self._read_movie_id("/v/C1-UPSERT")
        assert mid > 10**10, f"MovieHistory.Id too small (AUTOINCREMENT?): {mid}"
        assert mid < 2**53, f"MovieHistory.Id exceeds D1 safe range: {mid}"

    def test_db_upsert_history_torrent_id_is_snowflake(self, _isolate_sqlite):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            db_mod.db_upsert_history(
                "/v/C1-UPSERT-TH",
                "C1-UPSERT-TH",
                magnet_links={"subtitle": "magnet:upsert-th"},
            )
        mid = self._read_movie_id("/v/C1-UPSERT-TH")
        tids = self._read_torrent_id(mid)
        assert tids, "No TorrentHistory row inserted"
        for tid in tids:
            assert tid > 10**10, f"TorrentHistory.Id too small: {tid}"
            assert tid < 2**53, f"TorrentHistory.Id exceeds D1 safe range: {tid}"

    def test_commit_one_movie_movie_id_is_snowflake(self, _isolate_sqlite):
        """_commit_one_movie path (COMMIT_SESSION_BULK=0)."""
        import os
        os.environ["COMMIT_SESSION_BULK"] = "0"
        try:
            sid = db_mod.db_create_report_session(
                report_type="DailyReport",
                report_date="2026-05-15",
                csv_filename="c1-per-href.csv",
                write_mode="pending",
            )
            db_mod.db_stage_history_write(sid, "movie", {
                "Href": "/v/C1-PER-HREF",
                "VideoCode": "C1-PER-HREF",
                "DateTimeVisited": "2026-05-15 12:00:00",
            })
            db_mod.db_stage_history_write(sid, "torrent", {
                "Href": "/v/C1-PER-HREF",
                "VideoCode": "C1-PER-HREF",
                "Category": "subtitle",
                "MagnetUri": "magnet:per-href",
                "Size": "1.0GB",
                "FileCount": 1,
                "DateTimeVisited": "2026-05-15 12:00:00",
            })
            db_mod.db_commit_session_history(sid)
        finally:
            os.environ.pop("COMMIT_SESSION_BULK", None)

        mid = self._read_movie_id("/v/C1-PER-HREF")
        assert mid > 10**10, f"MovieHistory.Id too small: {mid}"
        tids = self._read_torrent_id(mid)
        assert tids
        for tid in tids:
            assert tid > 10**10, f"TorrentHistory.Id too small: {tid}"

    def test_commit_session_bulk_movie_id_is_snowflake(self, _isolate_sqlite):
        """_commit_session_bulk path (COMMIT_SESSION_BULK=1)."""
        import os
        os.environ["COMMIT_SESSION_BULK"] = "1"
        try:
            sid = db_mod.db_create_report_session(
                report_type="DailyReport",
                report_date="2026-05-15",
                csv_filename="c1-bulk.csv",
                write_mode="pending",
            )
            db_mod.db_stage_history_write(sid, "movie", {
                "Href": "/v/C1-BULK",
                "VideoCode": "C1-BULK",
                "DateTimeVisited": "2026-05-15 12:00:00",
            })
            db_mod.db_stage_history_write(sid, "torrent", {
                "Href": "/v/C1-BULK",
                "VideoCode": "C1-BULK",
                "Category": "subtitle",
                "MagnetUri": "magnet:bulk",
                "Size": "2.0GB",
                "FileCount": 2,
                "DateTimeVisited": "2026-05-15 12:00:00",
            })
            db_mod.db_commit_session_history(sid)
        finally:
            os.environ.pop("COMMIT_SESSION_BULK", None)

        mid = self._read_movie_id("/v/C1-BULK")
        assert mid > 10**10, f"MovieHistory.Id too small: {mid}"
        tids = self._read_torrent_id(mid)
        assert tids
        for tid in tids:
            assert tid > 10**10, f"TorrentHistory.Id too small: {tid}"

    def test_multiple_inserts_get_distinct_ids(self, _isolate_sqlite):
        """Two separate MovieHistory rows must not share an Id."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            db_mod.db_upsert_history("/v/C1-M1", "C1-M1")
            db_mod.db_upsert_history("/v/C1-M2", "C1-M2")
        id1 = self._read_movie_id("/v/C1-M1")
        id2 = self._read_movie_id("/v/C1-M2")
        assert id1 != id2, f"Two rows share the same MovieHistory.Id: {id1}"


# ── Dual-mode guard tests (using FakeD1Connection) ───────────────────────


def _make_movie_history_sqlite(tmp_path):
    """Return a SQLite connection with MovieHistory + TorrentHistory DDL."""
    path = tmp_path / "mh.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE MovieHistory (
            Id INTEGER PRIMARY KEY,
            VideoCode TEXT NOT NULL,
            Href TEXT NOT NULL UNIQUE,
            DateTimeCreated TEXT,
            DateTimeUpdated TEXT,
            DateTimeVisited TEXT,
            SessionId TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE TorrentHistory (
            Id INTEGER PRIMARY KEY,
            MovieHistoryId INTEGER NOT NULL REFERENCES MovieHistory(Id),
            MagnetUri TEXT,
            SubtitleIndicator INTEGER,
            CensorIndicator INTEGER,
            DateTimeCreated TEXT,
            DateTimeUpdated TEXT,
            SessionId TEXT
        )"""
    )
    conn.commit()
    return conn


class _FakeD1Cursor:
    def __init__(self, lastrowid=None, rowcount=1):
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeD1Connection:
    def __init__(self, *, d1_lastrowid: int = 999):
        self._d1_lastrowid = d1_lastrowid

    def execute(self, sql, params=()):
        return _FakeD1Cursor(lastrowid=self._d1_lastrowid)

    def commit(self):
        pass

    def close(self):
        pass


def test_movie_history_without_explicit_id_raises_on_lastrowid_mismatch(
    monkeypatch, tmp_path
):
    """C.2 regression: INSERT MovieHistory without explicit Id triggers
    DualWriteIdMismatchError when SQLite and D1 lastrowids disagree.

    This is the pre-fix behaviour (AUTOINCREMENT on both backends diverges).
    The dual guard must catch it for MovieHistory now that the table is
    guarded.
    """
    from packages.python.javdb_platform import dual_connection as _dual_module

    sqlite_conn = _make_movie_history_sqlite(tmp_path)
    fake_d1 = _FakeD1Connection(d1_lastrowid=999)

    drift_path = tmp_path / "drift.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    with pytest.raises(DualWriteIdMismatchError):
        dual.execute(
            # No 'Id' in the column list → AUTOINCREMENT → mismatch.
            "INSERT INTO MovieHistory (VideoCode, Href, DateTimeCreated) "
            "VALUES (?, ?, ?)",
            ("TST-001", "/v/TST-001", "2026-05-15 00:00:00"),
        )

    assert drift_path.exists()
    assert "application_id_mismatch" in drift_path.read_text()


def test_movie_history_with_explicit_id_bypasses_lastrowid_check(tmp_path):
    """C.2 correctness: INSERT MovieHistory WITH explicit Id skips the
    lastrowid comparison even when the fake D1 returns a different lastrowid.

    This is the post-fix behaviour: the application owns the Id, so D1's
    internal counter disagreement is irrelevant.
    """
    sqlite_conn = _make_movie_history_sqlite(tmp_path)
    explicit_id = _generate_integer_id()
    # D1 returns a different lastrowid — would raise in the pre-fix world.
    fake_d1 = _FakeD1Connection(d1_lastrowid=explicit_id + 1)

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    # Must NOT raise.
    cur = dual.execute(
        "INSERT INTO MovieHistory (Id, VideoCode, Href, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        (explicit_id, "TST-002", "/v/TST-002", "2026-05-15 00:00:00"),
    )
    assert cur is not None
    assert cur.lastrowid == explicit_id


def test_torrent_history_with_explicit_id_bypasses_lastrowid_check(tmp_path):
    """TorrentHistory counterpart of the above."""
    sqlite_conn = _make_movie_history_sqlite(tmp_path)

    # Insert a MovieHistory row so the FK is valid.
    mh_id = _generate_integer_id()
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (Id, VideoCode, Href, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        (mh_id, "TST-003", "/v/TST-003", "2026-05-15 00:00:00"),
    )
    sqlite_conn.commit()

    th_id = _generate_integer_id()
    fake_d1 = _FakeD1Connection(d1_lastrowid=th_id + 42)

    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    cur = dual.execute(
        "INSERT INTO TorrentHistory "
        "(Id, MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator, "
        "DateTimeCreated) VALUES (?, ?, ?, ?, ?, ?)",
        (th_id, mh_id, "magnet:test", 1, 1, "2026-05-15 00:00:00"),
    )
    assert cur is not None
    assert cur.lastrowid == th_id


def test_torrent_history_without_explicit_id_raises_on_mismatch(
    monkeypatch, tmp_path
):
    """TorrentHistory also raises on lastrowid mismatch when Id absent."""
    from packages.python.javdb_platform import dual_connection as _dual_module

    sqlite_conn = _make_movie_history_sqlite(tmp_path)

    mh_id = _generate_integer_id()
    sqlite_conn.execute(
        "INSERT INTO MovieHistory (Id, VideoCode, Href, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        (mh_id, "TST-004", "/v/TST-004", "2026-05-15 00:00:00"),
    )
    sqlite_conn.commit()

    drift_path = tmp_path / "drift2.jsonl"
    monkeypatch.setattr(_dual_module, "_DRIFT_LOG_PATH", str(drift_path))

    fake_d1 = _FakeD1Connection(d1_lastrowid=999)
    dual = DualConnection(sqlite_conn, fake_d1, logical_name="history")

    with pytest.raises(DualWriteIdMismatchError):
        dual.execute(
            "INSERT INTO TorrentHistory "
            "(MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator, "
            "DateTimeCreated) VALUES (?, ?, ?, ?, ?)",
            (mh_id, "magnet:th", 1, 1, "2026-05-15 00:00:00"),
        )

    assert drift_path.exists()
    assert "application_id_mismatch" in drift_path.read_text()
