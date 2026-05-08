"""Smoke tests for ``scripts.sync_d1_to_sqlite``.

The full happy-path test requires a live D1 database, so this file
focuses on the dry-run safety guarantees (no writes), the
``STORAGE_BACKEND`` refusal, and CLI argument plumbing.
"""

from __future__ import annotations

import sys
import sqlite3
import types

import pytest

import scripts.sync_d1_to_sqlite as sync_mod


# ── Argument parsing ────────────────────────────────────────────────────


class TestArgs:
    def test_default_is_dry_run(self):
        args = sync_mod._parse_args([])
        assert args.dry_run is True

    def test_apply_disables_dry_run(self):
        args = sync_mod._parse_args(["--apply"])
        assert args.dry_run is False

    def test_logical_names_csv_parsed(self):
        args = sync_mod._parse_args(["--logical-names", "history,reports"])
        assert "history,reports" in args.logical_names


# ── STORAGE_BACKEND refusal ─────────────────────────────────────────────


class TestStorageBackendRefusal:
    def test_dual_backend_aborts(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "dual")
        with pytest.raises(SystemExit) as exc:
            sync_mod._refuse_when_dual_or_d1()
        assert exc.value.code == 1

    def test_d1_backend_aborts(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "d1")
        with pytest.raises(SystemExit) as exc:
            sync_mod._refuse_when_dual_or_d1()
        assert exc.value.code == 1

    def test_sqlite_backend_proceeds(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        # Should NOT raise.
        sync_mod._refuse_when_dual_or_d1()


# ── Dry-run does not modify sqlite ──────────────────────────────────────


class _FakeD1Cursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.lastrowid = None
        self.rowcount = 0

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeD1Connection:
    """Returns a tiny fake schema with one MovieHistory row."""

    def __init__(self):
        self.rows = [{"Id": 1, "VideoCode": "ABC-001"}]

    def execute(self, sql, params=()):  # noqa: ARG002
        s = sql.upper()
        if "FROM SQLITE_MASTER" in s:
            return _FakeD1Cursor([{"name": "MovieHistory"}])
        if "PRAGMA TABLE_INFO" in s:
            return _FakeD1Cursor([
                {"name": "Id"},
                {"name": "VideoCode"},
            ])
        if "SELECT COUNT(*)" in s:
            return _FakeD1Cursor([{"n": len(self.rows)}])
        if s.startswith("SELECT"):
            return _FakeD1Cursor(self.rows)
        return _FakeD1Cursor([])

    def close(self):
        pass


class _FakeRunIdentityD1Connection:
    """D1 schema is newer than the local sqlite mirror."""

    columns = [
        "Id", "ReportType", "ReportDate", "UrlType", "DisplayName",
        "Url", "StartPage", "EndPage", "CsvFilename", "DateTimeCreated",
        "Status", "RunId", "RunAttempt", "FailureReason",
    ]

    rows = [{
        "Id": 1,
        "ReportType": "DailyReport",
        "ReportDate": "2026-05-08",
        "UrlType": "",
        "DisplayName": "",
        "Url": "",
        "StartPage": 1,
        "EndPage": 1,
        "CsvFilename": "run.csv",
        "DateTimeCreated": "2026-05-08T00:00:00Z",
        "Status": "committed",
        "RunId": "25549335675",
        "RunAttempt": 1,
        "FailureReason": None,
    }]

    def execute(self, sql, params=()):
        s = sql.upper()
        if "FROM SQLITE_MASTER" in s:
            return _FakeD1Cursor([{"name": "ReportSessions"}])
        if "PRAGMA TABLE_INFO" in s:
            return _FakeD1Cursor([
                {"name": name} for name in self.columns
            ])
        if "SELECT COUNT(*)" in s:
            return _FakeD1Cursor([{"n": len(self.rows)}])
        if s.startswith("SELECT"):
            offset = int(params[1]) if len(params) > 1 else 0
            return _FakeD1Cursor(self.rows[offset:])
        return _FakeD1Cursor([])

    def close(self):
        pass


class TestDryRunNoOp:
    def test_dry_run_does_not_write_to_sqlite(
        self, monkeypatch, tmp_path,
    ):
        # Build a real local sqlite mirror with one MovieHistory row that
        # we'll verify is preserved across the dry-run.
        import utils.infra.db as db_mod
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="dryrun.csv",
        )
        db_mod.db_upsert_history(
            href="/v/SYNC-001",
            video_code="SYNC-001",
            session_id=sid,
        )

        # Stub out D1 so the script can pretend D1 has a different row.
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        monkeypatch.setattr(
            sync_mod, "D1Connection",
            lambda *args, **kwargs: _FakeD1Connection(),
        )
        monkeypatch.setattr(
            sync_mod, "get_d1_account_id", lambda: "fake",
        )
        monkeypatch.setattr(
            sync_mod, "get_d1_database_id", lambda name: "fake",
        )
        monkeypatch.setattr(
            sync_mod, "get_d1_api_token", lambda: "fake",
        )
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = sync_mod.main([
            "--logical-names", "history",
            "--dry-run",
        ])
        # Dry-run on a tiny fake schema should finish OK.
        assert rc in (0, 4)

        # The original row must still exist (dry-run is non-destructive).
        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory WHERE VideoCode='SYNC-001'"
            ).fetchone()["n"]
        assert n == 1


class TestApplySchemaCompatibility:
    def test_apply_adds_run_identity_columns_before_insert(
        self, monkeypatch, tmp_path,
    ):
        sqlite_path = tmp_path / "reports.db"
        conn = sqlite3.connect(sqlite_path)
        conn.execute(
            """CREATE TABLE ReportSessions (
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
                Status TEXT DEFAULT 'in_progress'
            )"""
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            sync_mod, "D1Connection",
            lambda *args, **kwargs: _FakeRunIdentityD1Connection(),
        )
        monkeypatch.setattr(
            sync_mod, "get_d1_account_id", lambda: "fake",
        )
        monkeypatch.setattr(
            sync_mod, "get_d1_database_id", lambda name: "fake",
        )
        monkeypatch.setattr(
            sync_mod, "get_d1_api_token", lambda: "fake",
        )

        result = sync_mod._sync_one_logical(
            "reports", str(sqlite_path), page_size=500, dry_run=False,
        )

        assert result["tables"][0]["consistent_after"] is True
        conn = sqlite3.connect(sqlite_path)
        try:
            cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(ReportSessions)"
                ).fetchall()
            }
            row = conn.execute(
                "SELECT RunId, RunAttempt FROM ReportSessions"
            ).fetchone()
        finally:
            conn.close()

        assert {"RunId", "RunAttempt", "FailureReason"}.issubset(cols)
        assert row == ("25549335675", 1)
