"""Unit tests for ``apps.cli.db.drift_diagnose``."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — fake D1 cursor / connection
# ---------------------------------------------------------------------------


class FakeD1Cursor:
    """Mimics ``D1Cursor`` from d1_client — fetchone/fetchall on list-of-dicts."""

    def __init__(self, rows: List[dict]):
        self._rows = rows
        self.lastrowid = None
        self.rowcount = len(rows)

    def fetchone(self) -> Optional[dict]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[dict]:
        return list(self._rows)


class FakeD1Connection:
    """Programmable fake for ``D1Connection``.

    ``query_results`` is a dict mapping SQL-prefix → list-of-dicts returned by
    ``execute``. The prefix is matched against the first 40 chars (lowercased)
    of the SQL string for easy test wiring.
    """

    def __init__(self, query_results: Optional[Dict[str, List[dict]]] = None):
        self._results: Dict[str, List[dict]] = query_results or {}
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> FakeD1Cursor:
        key = sql.strip().lower()[:60]
        for prefix, rows in self._results.items():
            if key.startswith(prefix.lower()):
                return FakeD1Cursor(rows)
        return FakeD1Cursor([])

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _hours_ago_iso(hours: float) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _write_jsonl(path, records: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _make_sqlite_history(db_path: str, *, movies=None, torrents=None,
                         pending_movies=None, pending_torrents=None):
    """Create a minimal SQLite history.db with the required tables."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS MovieHistory (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Href TEXT UNIQUE,
            VideoCode TEXT,
            ActorName TEXT,
            DateTimeCreated TEXT,
            DateTimeUpdated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS TorrentHistory (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            MovieHistoryId INTEGER,
            SubtitleIndicator INTEGER DEFAULT 0,
            CensorIndicator INTEGER DEFAULT 0,
            MagnetUri TEXT,
            Size TEXT,
            DateTimeCreated TEXT,
            DateTimeUpdated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS PendingMovieHistoryWrites (
            Seq TEXT PRIMARY KEY,
            SessionId TEXT,
            Href TEXT,
            ApplyState TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS PendingTorrentHistoryWrites (
            Seq TEXT PRIMARY KEY,
            SessionId TEXT,
            Href TEXT,
            ApplyState TEXT DEFAULT 'pending'
        )
    """)
    for m in (movies or []):
        conn.execute(
            "INSERT INTO MovieHistory (Href, VideoCode, ActorName, DateTimeCreated) "
            "VALUES (?, ?, ?, ?)",
            (m["Href"], m.get("VideoCode", ""), m.get("ActorName", ""),
             m.get("DateTimeCreated", "2026-01-01 00:00:00")),
        )
    for t in (torrents or []):
        conn.execute(
            "INSERT INTO TorrentHistory (MovieHistoryId, SubtitleIndicator, "
            "CensorIndicator, MagnetUri, Size, DateTimeCreated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (t["MovieHistoryId"], t.get("SubtitleIndicator", 0),
             t.get("CensorIndicator", 0), t.get("MagnetUri", ""),
             t.get("Size", "1GB"), t.get("DateTimeCreated", "2026-01-01 00:00:00")),
        )
    for pm in (pending_movies or []):
        conn.execute(
            "INSERT INTO PendingMovieHistoryWrites (Seq, SessionId, Href, ApplyState) "
            "VALUES (?, ?, ?, ?)",
            (pm["Seq"], pm["SessionId"], pm["Href"], pm.get("ApplyState", "pending")),
        )
    for pt in (pending_torrents or []):
        conn.execute(
            "INSERT INTO PendingTorrentHistoryWrites (Seq, SessionId, Href, ApplyState) "
            "VALUES (?, ?, ?, ?)",
            (pt["Seq"], pt["SessionId"], pt["Href"], pt.get("ApplyState", "pending")),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Import the module under test — deferred so monkeypatch is available
# ---------------------------------------------------------------------------


@pytest.fixture
def drift_mod(monkeypatch):
    """Import drift_diagnose with REPORTS_DIR pointed at a temp location."""
    # Ensure the module can be imported cleanly
    import apps.cli.db.drift_diagnose as mod
    return mod


# ===========================================================================
# Test: _parse_ts
# ===========================================================================


class TestParseTs:
    def test_iso_with_z(self, drift_mod):
        dt = drift_mod._parse_ts("2026-05-17T12:28:04Z")
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026
        assert dt.month == 5

    def test_iso_with_offset(self, drift_mod):
        dt = drift_mod._parse_ts("2026-05-17T12:30:00.000000+00:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_none_input(self, drift_mod):
        assert drift_mod._parse_ts(None) is None
        assert drift_mod._parse_ts("") is None

    def test_invalid_input(self, drift_mod):
        assert drift_mod._parse_ts("not-a-date") is None


# ===========================================================================
# Test: _values_equal
# ===========================================================================


class TestValuesEqual:
    def test_none_both(self, drift_mod):
        assert drift_mod._values_equal(None, None) is True

    def test_none_one_side(self, drift_mod):
        assert drift_mod._values_equal(None, 1) is False
        assert drift_mod._values_equal(1, None) is False

    def test_int_int(self, drift_mod):
        assert drift_mod._values_equal(42, 42) is True
        assert drift_mod._values_equal(42, 43) is False

    def test_int_float(self, drift_mod):
        assert drift_mod._values_equal(42, 42.0) is True

    def test_string(self, drift_mod):
        assert drift_mod._values_equal("abc", "abc") is True
        assert drift_mod._values_equal("abc", "def") is False


# ===========================================================================
# Test: D3 — suspect discovery from verify-metric path
# ===========================================================================


class TestDiscoverFromVerifyLog:
    def test_empty_log(self, drift_mod, tmp_path):
        """No records → no suspects."""
        log_path = str(tmp_path / "d1_drift.jsonl")
        _write_jsonl(log_path, [])
        suspects = drift_mod.discover_suspects_from_verify_log(log_path, since_hours=24)
        assert suspects == {}

    def test_residual_gt_zero_within_window(self, drift_mod, tmp_path):
        """Record with pending_residual_count > 0 within window → suspect."""
        session_id = "20260517T121617.445400Z-ea87-0000"
        log_path = str(tmp_path / "d1_drift.jsonl")
        _write_jsonl(log_path, [
            {
                "kind": "pending_session_verify",
                "ts": _hours_ago_iso(2),
                "session_id": session_id,
                "pending_residual_count": 3,
            },
        ])
        suspects = drift_mod.discover_suspects_from_verify_log(log_path, since_hours=24)
        assert session_id in suspects
        assert suspects[session_id]["pending_residual_count"] == 3

    def test_residual_zero_ignored(self, drift_mod, tmp_path):
        """Record with pending_residual_count == 0 → not a suspect."""
        log_path = str(tmp_path / "d1_drift.jsonl")
        _write_jsonl(log_path, [
            {
                "kind": "pending_session_verify",
                "ts": _hours_ago_iso(2),
                "session_id": "20260517T000000.000000Z-0000-0000",
                "pending_residual_count": 0,
            },
        ])
        suspects = drift_mod.discover_suspects_from_verify_log(log_path, since_hours=24)
        assert suspects == {}

    def test_outside_window_ignored(self, drift_mod, tmp_path):
        """Record outside the since window → not a suspect."""
        log_path = str(tmp_path / "d1_drift.jsonl")
        _write_jsonl(log_path, [
            {
                "kind": "pending_session_verify",
                "ts": _hours_ago_iso(48),
                "session_id": "20260517T000000.000000Z-0000-0000",
                "pending_residual_count": 5,
            },
        ])
        suspects = drift_mod.discover_suspects_from_verify_log(log_path, since_hours=24)
        assert suspects == {}

    def test_non_verify_records_ignored(self, drift_mod, tmp_path):
        """Records with different 'kind' are skipped."""
        log_path = str(tmp_path / "d1_drift.jsonl")
        _write_jsonl(log_path, [
            {
                "kind": "stale_session_cleanup",
                "ts": _hours_ago_iso(2),
                "session_id": "20260517T000000.000000Z-0000-0000",
                "pending_residual_count": 5,
            },
            {
                "ts": _hours_ago_iso(2),
                "db": "history",
                "committed": True,
                "failure_count": 1,
            },
        ])
        suspects = drift_mod.discover_suspects_from_verify_log(log_path, since_hours=24)
        assert suspects == {}


# ===========================================================================
# Test: D3 — suspect discovery from D1 sweep
# ===========================================================================


class TestDiscoverFromD1Sweep:
    def test_no_committed_sessions(self, drift_mod):
        """No committed sessions in window → no suspects."""
        d1_reports = FakeD1Connection({"select id": []})
        d1_history = FakeD1Connection({})
        suspects = drift_mod.discover_suspects_from_d1_sweep(
            d1_reports, d1_history, since_hours=24,
        )
        assert suspects == {}

    def test_committed_with_orphan_pending(self, drift_mod):
        """Committed session with orphan pending rows → suspect."""
        session_id = "20260517T121617.445400Z-ea87-0000"
        d1_reports = FakeD1Connection({
            "select id": [{"Id": session_id, "Status": "committed",
                           "DateTimeCreated": _hours_ago_iso(2)}],
        })
        d1_history = FakeD1Connection({
            "select count(*) as cnt from pendingmoviehistorywrite": [{"cnt": 2}],
            "select count(*) as cnt from pendingtorrenthistorywri": [{"cnt": 1}],
        })
        suspects = drift_mod.discover_suspects_from_d1_sweep(
            d1_reports, d1_history, since_hours=24,
        )
        assert session_id in suspects
        assert suspects[session_id]["d1_pending_movie_count"] == 2
        assert suspects[session_id]["d1_pending_torrent_count"] == 1

    def test_committed_no_orphans(self, drift_mod):
        """Committed session with zero pending rows → not a suspect."""
        session_id = "20260517T121617.445400Z-ea87-0000"
        d1_reports = FakeD1Connection({
            "select id": [{"Id": session_id, "Status": "committed",
                           "DateTimeCreated": _hours_ago_iso(2)}],
        })
        d1_history = FakeD1Connection({
            "select count(*) as cnt from pendingmoviehistorywrite": [{"cnt": 0}],
            "select count(*) as cnt from pendingtorrenthistorywri": [{"cnt": 0}],
        })
        suspects = drift_mod.discover_suspects_from_d1_sweep(
            d1_reports, d1_history, since_hours=24,
        )
        assert suspects == {}


# ===========================================================================
# Test: D3 — merge suspects with provenance tagging
# ===========================================================================


class TestMergeSuspects:
    def test_verify_only(self, drift_mod):
        sid = "s1"
        verify = {sid: {"pending_residual_count": 3}}
        sweep = {}
        merged = drift_mod.merge_suspects(verify, sweep)
        assert len(merged) == 1
        assert merged[0]["session_id"] == sid
        assert merged[0]["provenance"] == "verify-tagged"

    def test_sweep_only(self, drift_mod):
        sid = "s2"
        verify = {}
        sweep = {sid: {"d1_pending_movie_count": 2, "d1_pending_torrent_count": 0}}
        merged = drift_mod.merge_suspects(verify, sweep)
        assert len(merged) == 1
        assert merged[0]["session_id"] == sid
        assert merged[0]["provenance"] == "sweep-only"

    def test_both(self, drift_mod):
        sid = "s3"
        verify = {sid: {"pending_residual_count": 5}}
        sweep = {sid: {"d1_pending_movie_count": 3, "d1_pending_torrent_count": 2}}
        merged = drift_mod.merge_suspects(verify, sweep)
        assert len(merged) == 1
        assert merged[0]["provenance"] == "both"

    def test_empty(self, drift_mod):
        merged = drift_mod.merge_suspects({}, {})
        assert merged == []


# ===========================================================================
# Test: D4 — verdict classification
# ===========================================================================


class TestClassifyVerdict:

    @staticmethod
    def _committed_reports(*session_ids):
        """Return a FakeD1Connection that reports 'committed' for the given IDs."""
        rows = [{"Status": "committed"} for _ in session_ids]
        return FakeD1Connection({
            "select status from reportsessions": rows or [{"Status": "committed"}],
        })

    def test_clean_no_orphans(self, drift_mod):
        """D1 has zero orphan pending rows → CLEAN."""
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [],
            "select * from pendingtorrenthistorywrites": [],
        })
        d1_reports = self._committed_reports("s1")
        suspect = {"session_id": "s1", "provenance": "verify-tagged"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=None,
        )
        assert result["verdict"] == "CLEAN"
        assert result["d1_orphan_movie_count"] == 0
        assert result["d1_orphan_torrent_count"] == 0

    def test_safe_to_apply_d1_orphan_sqlite_clean(self, drift_mod, tmp_path):
        """D1 orphans exist, SQLite has no orphans, live tables match → SAFE_TO_APPLY."""
        session_id = "s-safe"
        # D1 has orphan pending rows
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [
                {"Seq": "seq1", "SessionId": session_id, "Href": "/v/abc",
                 "ApplyState": "pending"},
            ],
            "select * from pendingtorrenthistorywrites": [],
            # Live table comparison — D1 MovieHistory for href
            "select * from moviehistory where href": [
                {"Href": "/v/abc", "VideoCode": "ABC-001", "ActorName": "Test",
                 "DateTimeCreated": "2026-01-01 00:00:00"},
            ],
            "select * from torrenthistory where moviehistoryid": [],
        })
        d1_reports = self._committed_reports(session_id)
        # SQLite side — no orphan pending, live data matches
        db_path = str(tmp_path / "history.db")
        _make_sqlite_history(db_path, movies=[
            {"Href": "/v/abc", "VideoCode": "ABC-001", "ActorName": "Test",
             "DateTimeCreated": "2026-01-01 00:00:00"},
        ])
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_conn.row_factory = sqlite3.Row

        suspect = {"session_id": session_id, "provenance": "sweep-only"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=sqlite_conn,
        )
        assert result["verdict"] == "SAFE_TO_APPLY"
        assert result["d1_orphan_movie_count"] == 1
        assert "suggested_command" in result
        sqlite_conn.close()

    def test_escalate_live_divergence(self, drift_mod, tmp_path):
        """D1 orphans exist, live tables differ → ESCALATE_LIVE_DIVERGENCE."""
        session_id = "s-diverge"
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [
                {"Seq": "seq1", "SessionId": session_id, "Href": "/v/xyz",
                 "ApplyState": "pending"},
            ],
            "select * from pendingtorrenthistorywrites": [],
            "select * from moviehistory where href": [
                {"Href": "/v/xyz", "VideoCode": "XYZ-001", "ActorName": "D1Actor",
                 "DateTimeCreated": "2026-01-01 00:00:00"},
            ],
            "select * from torrenthistory where moviehistoryid": [],
        })
        d1_reports = self._committed_reports(session_id)
        db_path = str(tmp_path / "history.db")
        _make_sqlite_history(db_path, movies=[
            {"Href": "/v/xyz", "VideoCode": "XYZ-001", "ActorName": "DifferentActor",
             "DateTimeCreated": "2026-01-01 00:00:00"},
        ])
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_conn.row_factory = sqlite3.Row

        suspect = {"session_id": session_id, "provenance": "both"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=sqlite_conn,
        )
        assert result["verdict"] == "ESCALATE_LIVE_DIVERGENCE"
        sqlite_conn.close()

    def test_unexpected_no_sqlite(self, drift_mod):
        """D1 orphans exist but no SQLite connection → UNEXPECTED_PATTERN."""
        session_id = "s-nosqlite"
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [
                {"Seq": "seq1", "SessionId": session_id, "Href": "/v/test",
                 "ApplyState": "pending"},
            ],
            "select * from pendingtorrenthistorywrites": [],
        })
        d1_reports = self._committed_reports(session_id)
        suspect = {"session_id": session_id, "provenance": "sweep-only"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=None,
        )
        assert result["verdict"] == "UNEXPECTED_PATTERN"
        assert "note" in result

    def test_unexpected_sqlite_has_orphans(self, drift_mod, tmp_path):
        """SQLite side also has orphan pending rows → UNEXPECTED_PATTERN."""
        session_id = "s-sqliteorphan"
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [
                {"Seq": "seq1", "SessionId": session_id, "Href": "/v/foo",
                 "ApplyState": "pending"},
            ],
            "select * from pendingtorrenthistorywrites": [],
        })
        d1_reports = self._committed_reports(session_id)
        db_path = str(tmp_path / "history.db")
        _make_sqlite_history(
            db_path,
            movies=[{"Href": "/v/foo", "VideoCode": "FOO-001", "ActorName": "A",
                      "DateTimeCreated": "2026-01-01 00:00:00"}],
            pending_movies=[
                {"Seq": "seq2", "SessionId": session_id, "Href": "/v/foo",
                 "ApplyState": "pending"},
            ],
        )
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_conn.row_factory = sqlite3.Row

        suspect = {"session_id": session_id, "provenance": "verify-tagged"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=sqlite_conn,
        )
        assert result["verdict"] == "UNEXPECTED_PATTERN"
        sqlite_conn.close()

    def test_unexpected_non_committed_session(self, drift_mod):
        """Session status is not 'committed' → UNEXPECTED_PATTERN (ADR-009 D4)."""
        session_id = "s-in-progress"
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [
                {"Seq": "seq1", "SessionId": session_id, "Href": "/v/bar",
                 "ApplyState": "pending"},
            ],
            "select * from pendingtorrenthistorywrites": [],
        })
        d1_reports = FakeD1Connection({
            "select status from reportsessions": [{"Status": "in_progress"}],
        })
        suspect = {"session_id": session_id, "provenance": "verify-tagged"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=None,
        )
        assert result["verdict"] == "UNEXPECTED_PATTERN"
        assert "in_progress" in result["note"]
        assert "committed" in result["note"]
        # Orphan counts should stay at 0 since we short-circuit before checking
        assert result["d1_orphan_movie_count"] == 0

    def test_unexpected_failed_session(self, drift_mod):
        """Session status is 'failed' → UNEXPECTED_PATTERN."""
        session_id = "s-failed"
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [],
            "select * from pendingtorrenthistorywrites": [],
        })
        d1_reports = FakeD1Connection({
            "select status from reportsessions": [{"Status": "failed"}],
        })
        suspect = {"session_id": session_id, "provenance": "sweep-only"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=d1_reports, sqlite_conn=None,
        )
        assert result["verdict"] == "UNEXPECTED_PATTERN"
        assert "failed" in result["note"]

    def test_no_d1_reports_skips_status_check(self, drift_mod):
        """When d1_reports is None, status check is skipped (backward compat)."""
        d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [],
            "select * from pendingtorrenthistorywrites": [],
        })
        suspect = {"session_id": "s-no-reports", "provenance": "verify-tagged"}
        result = drift_mod.classify_verdict(
            suspect, d1_history, d1_reports=None, sqlite_conn=None,
        )
        # No orphans → CLEAN, regardless of missing d1_reports
        assert result["verdict"] == "CLEAN"


# ===========================================================================
# Test: Exit code computation
# ===========================================================================


class TestExitCode:
    def test_empty_suspects(self, drift_mod):
        assert drift_mod.compute_exit_code([]) == 0

    def test_all_clean(self, drift_mod):
        results = [{"verdict": "CLEAN"}, {"verdict": "CLEAN"}]
        assert drift_mod.compute_exit_code(results) == 0

    def test_safe_to_apply(self, drift_mod):
        results = [{"verdict": "CLEAN"}, {"verdict": "SAFE_TO_APPLY"}]
        assert drift_mod.compute_exit_code(results) == 1

    def test_escalate(self, drift_mod):
        results = [{"verdict": "SAFE_TO_APPLY"}, {"verdict": "ESCALATE_LIVE_DIVERGENCE"}]
        assert drift_mod.compute_exit_code(results) == 2

    def test_unexpected(self, drift_mod):
        results = [{"verdict": "UNEXPECTED_PATTERN"}]
        assert drift_mod.compute_exit_code(results) == 2


# ===========================================================================
# Test: JSON output format
# ===========================================================================


class TestJsonOutput:
    def test_format_output_json(self, drift_mod):
        results = [
            {
                "session_id": "s1",
                "provenance": "verify-tagged",
                "verdict": "CLEAN",
                "d1_orphan_movie_count": 0,
                "d1_orphan_torrent_count": 0,
            },
            {
                "session_id": "s2",
                "provenance": "sweep-only",
                "verdict": "SAFE_TO_APPLY",
                "d1_orphan_movie_count": 3,
                "d1_orphan_torrent_count": 1,
                "suggested_command": "python3 -m apps.cli.db.drift_diagnose --apply --session-id s2",
            },
        ]
        output = drift_mod.format_output(results, as_json=True)
        parsed = json.loads(output)
        assert "suspects" in parsed
        assert "max_verdict" in parsed
        assert len(parsed["suspects"]) == 2
        assert parsed["max_verdict"] == "SAFE_TO_APPLY"

    def test_format_output_text(self, drift_mod):
        results = [
            {
                "session_id": "s1",
                "provenance": "verify-tagged",
                "verdict": "CLEAN",
                "d1_orphan_movie_count": 0,
                "d1_orphan_torrent_count": 0,
            },
        ]
        output = drift_mod.format_output(results, as_json=False)
        assert "s1" in output
        assert "CLEAN" in output

    def test_format_output_empty(self, drift_mod):
        output = drift_mod.format_output([], as_json=True)
        parsed = json.loads(output)
        assert parsed["suspects"] == []
        assert parsed["max_verdict"] == "CLEAN"


# ===========================================================================
# Test: Full diagnose flow
# ===========================================================================


class TestDiagnoseFlow:
    def test_no_suspects_exit_0(self, drift_mod, tmp_path, monkeypatch):
        """Empty drift log + no D1 orphans → exit 0."""
        log_path = str(tmp_path / "D1" / "d1_drift.jsonl")
        _write_jsonl(log_path, [])

        # Patch D1 connection factory
        fake_d1_reports = FakeD1Connection({"select id": []})
        fake_d1_history = FakeD1Connection({})

        def fake_make_d1(name):
            if name == "reports":
                return fake_d1_reports
            return fake_d1_history

        monkeypatch.setattr(drift_mod, "make_d1_connection", fake_make_d1)

        results, exit_code = drift_mod.diagnose(
            drift_log_path=log_path,
            since_hours=24,
            sqlite_history_path=None,
        )
        assert exit_code == 0
        assert results == []

    def test_verify_suspect_clean_on_recheck(self, drift_mod, tmp_path, monkeypatch):
        """Verify log flags a suspect, but D1 recheck finds no orphans → CLEAN."""
        session_id = "20260517T121617.445400Z-ea87-0000"
        log_path = str(tmp_path / "D1" / "d1_drift.jsonl")
        _write_jsonl(log_path, [
            {
                "kind": "pending_session_verify",
                "ts": _hours_ago_iso(2),
                "session_id": session_id,
                "pending_residual_count": 3,
            },
        ])

        fake_d1_reports = FakeD1Connection({
            "select id": [],
            "select status from reportsessions": [{"Status": "committed"}],
        })
        fake_d1_history = FakeD1Connection({
            "select * from pendingmoviehistorywrites": [],
            "select * from pendingtorrenthistorywrites": [],
        })

        def fake_make_d1(name):
            if name == "reports":
                return fake_d1_reports
            return fake_d1_history

        monkeypatch.setattr(drift_mod, "make_d1_connection", fake_make_d1)

        results, exit_code = drift_mod.diagnose(
            drift_log_path=log_path,
            since_hours=24,
            sqlite_history_path=None,
        )
        assert exit_code == 0
        assert len(results) == 1
        assert results[0]["verdict"] == "CLEAN"

    def test_sweep_suspect_safe_to_apply(self, drift_mod, tmp_path, monkeypatch):
        """D1 sweep finds orphan, SQLite clean + live match → SAFE_TO_APPLY."""
        session_id = "20260518T090000.000000Z-1234-0000"
        log_path = str(tmp_path / "D1" / "d1_drift.jsonl")
        _write_jsonl(log_path, [])

        fake_d1_reports = FakeD1Connection({
            "select id": [{"Id": session_id, "Status": "committed",
                           "DateTimeCreated": _hours_ago_iso(3)}],
            "select status from reportsessions": [{"Status": "committed"}],
        })
        fake_d1_history = FakeD1Connection({
            "select count(*) as cnt from pendingmoviehistorywrite": [{"cnt": 1}],
            "select count(*) as cnt from pendingtorrenthistorywri": [{"cnt": 0}],
            "select * from pendingmoviehistorywrites": [
                {"Seq": "seq1", "SessionId": session_id, "Href": "/v/test1",
                 "ApplyState": "pending"},
            ],
            "select * from pendingtorrenthistorywrites": [],
            "select * from moviehistory where href": [
                {"Href": "/v/test1", "VideoCode": "TEST-001", "ActorName": "A",
                 "DateTimeCreated": "2026-01-01 00:00:00", "DateTimeUpdated": None},
            ],
            "select * from torrenthistory where moviehistoryid": [],
        })

        def fake_make_d1(name):
            if name == "reports":
                return fake_d1_reports
            return fake_d1_history

        monkeypatch.setattr(drift_mod, "make_d1_connection", fake_make_d1)

        # Set up local SQLite with matching data
        db_path = str(tmp_path / "history.db")
        _make_sqlite_history(db_path, movies=[
            {"Href": "/v/test1", "VideoCode": "TEST-001", "ActorName": "A",
             "DateTimeCreated": "2026-01-01 00:00:00"},
        ])

        results, exit_code = drift_mod.diagnose(
            drift_log_path=log_path,
            since_hours=24,
            sqlite_history_path=db_path,
        )
        assert exit_code == 1
        assert len(results) == 1
        assert results[0]["verdict"] == "SAFE_TO_APPLY"


# ===========================================================================
# Test: CLI main() argument parsing
# ===========================================================================


class TestMainCli:
    def test_main_help(self, drift_mod):
        """--help should exit with 0."""
        with pytest.raises(SystemExit) as exc_info:
            drift_mod.main(["--help"])
        assert exc_info.value.code == 0

    def test_main_no_drift_log(self, drift_mod, tmp_path, monkeypatch):
        """When drift log doesn't exist and D1 sweep finds nothing → exit 0."""
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        # Mock D1 connections to avoid real API calls
        fake_d1_reports = FakeD1Connection({"select id": []})
        fake_d1_history = FakeD1Connection({})

        def fake_make_d1(name):
            if name == "reports":
                return fake_d1_reports
            return fake_d1_history

        monkeypatch.setattr(drift_mod, "make_d1_connection", fake_make_d1)

        rc = drift_mod.main(["--since", "24"])
        assert rc == 0

    def test_main_apply_returns_2(self, drift_mod):
        """--apply is not yet implemented and should return exit code 2."""
        rc = drift_mod.main(["--apply"])
        assert rc == 2


# ===========================================================================
# Test: _read_jsonl with malformed lines
# ===========================================================================


class TestReadJsonlMalformed:
    def test_skips_malformed_lines(self, drift_mod, tmp_path):
        """Valid lines are returned; malformed lines are silently skipped."""
        log_path = str(tmp_path / "mixed.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write('{"a": 1}\n')
            f.write('NOT JSON\n')
            f.write('{"b": 2}\n')
            f.write('{bad json\n')
            f.write('{"c": 3}\n')
        records = drift_mod._read_jsonl(log_path)
        assert len(records) == 3
        assert records[0] == {"a": 1}
        assert records[1] == {"b": 2}
        assert records[2] == {"c": 3}
