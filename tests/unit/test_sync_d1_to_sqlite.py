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

import apps.cli.db.sync_d1_to_sqlite as sync_mod


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

    def test_prune_local_only_defaults_off(self):
        """P0-8: destructive prune must be off unless explicitly requested."""
        args = sync_mod._parse_args([])
        assert args.prune_local_only is False

    def test_allow_local_prune_on_drift_defaults_off(self):
        args = sync_mod._parse_args([])
        assert args.allow_local_prune_on_drift is False

    def test_prune_local_only_explicit_opt_in(self):
        args = sync_mod._parse_args(["--apply", "--prune-local-only"])
        assert args.prune_local_only is True


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


class _FakeMovieHistoryD1Connection:
    """Returns a PRAGMA pk=1 on Id so prune/upsert tests can identify the PK."""

    def __init__(self, rows):
        self.rows = list(rows)

    def execute(self, sql, params=()):
        s = sql.upper()
        if "FROM SQLITE_MASTER" in s:
            return _FakeD1Cursor([{"name": "MovieHistory"}])
        if "PRAGMA TABLE_INFO" in s:
            # cid, name, type, notnull, dflt_value, pk — pk=1 on Id only.
            return _FakeD1Cursor([
                {"name": "Id", "pk": 1},
                {"name": "VideoCode", "pk": 0},
            ])
        if "SELECT COUNT(*)" in s:
            return _FakeD1Cursor([{"n": len(self.rows)}])
        if s.startswith("SELECT"):
            offset = int(params[1]) if len(params) > 1 else 0
            return _FakeD1Cursor(self.rows[offset:])
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
        import javdb.storage.db.db as db_mod
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

        # P0-8: upsert-only mode preserves SQLite-only rows; the
        # consistency-after assertion now only fires under
        # ``prune_local_only=True``. We still confirm the row landed.
        assert result["tables"][0]["mode"] == "upsert-only"
        assert result["tables"][0]["rows_streamed"] == 1
        assert result["tables"][0]["sqlite_count_after"] == 1
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


# ── P0-8 upsert / prune safety guarantees ───────────────────────────────


class TestUpsertPreservesSqliteOnlyRows:
    """P0-8: ``--apply`` without ``--prune-local-only`` must NOT delete
    SQLite rows that exist locally but not on D1. The legacy DELETE+REINSERT
    behaviour silently destroyed those rows whenever D1 was behind by even
    one asymmetric write — exactly the 2026-05 ``ReportSessions`` /
    ``SpiderStats`` -1 incident.
    """

    def _seed_local_movie(
        self, sqlite_path, video_code: str, *, local_id: int = 9999,
    ):
        conn = sqlite3.connect(sqlite_path)
        conn.execute(
            "CREATE TABLE MovieHistory ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, VideoCode TEXT)"
        )
        # Explicit Id so the test can use a PK that doesn't collide with
        # whatever the fake D1 source returns. The legacy DELETE+REINSERT
        # path destroyed THIS row even when no PK collision existed.
        conn.execute(
            "INSERT INTO MovieHistory (Id, VideoCode) VALUES (?, ?)",
            (local_id, video_code),
        )
        conn.commit()
        conn.close()

    def test_default_apply_preserves_sqlite_only_rows(
        self, monkeypatch, tmp_path,
    ):
        sqlite_path = tmp_path / "history.db"
        self._seed_local_movie(sqlite_path, "LOCAL-ONLY-001")

        # D1 has a different row.
        d1 = _FakeMovieHistoryD1Connection(
            [{"Id": 1, "VideoCode": "D1-ONLY-001"}]
        )
        monkeypatch.setattr(sync_mod, "D1Connection", lambda *a, **k: d1)
        monkeypatch.setattr(sync_mod, "get_d1_account_id", lambda: "fake")
        monkeypatch.setattr(
            sync_mod, "get_d1_database_id", lambda name: "fake",
        )
        monkeypatch.setattr(sync_mod, "get_d1_api_token", lambda: "fake")

        result = sync_mod._sync_one_logical(
            "history", str(sqlite_path),
            page_size=500, dry_run=False,
            prune_local_only=False,
        )
        assert result["tables"][0]["mode"] == "upsert-only"

        conn = sqlite3.connect(sqlite_path)
        try:
            codes = sorted(
                row[0] for row in conn.execute(
                    "SELECT VideoCode FROM MovieHistory"
                ).fetchall()
            )
        finally:
            conn.close()
        # Both rows present: SQLite-only row was NOT pruned.
        assert codes == ["D1-ONLY-001", "LOCAL-ONLY-001"], (
            f"P0-8 regression: SQLite-only row was deleted; codes={codes!r}"
        )

    def test_prune_refuses_when_dry_run_showed_local_ahead(
        self, monkeypatch, tmp_path,
    ):
        """Prune must self-block when SQLite has rows D1 lacks (delta < 0).

        ``--allow-local-prune-on-drift`` is the explicit override; without
        it the script keeps the SQLite-only rows so the operator can
        reconcile by hand.
        """
        sqlite_path = tmp_path / "history.db"
        self._seed_local_movie(sqlite_path, "LOCAL-ONLY-002")

        # D1 is empty — SQLite is one row ahead.
        d1 = _FakeMovieHistoryD1Connection([])
        monkeypatch.setattr(sync_mod, "D1Connection", lambda *a, **k: d1)
        monkeypatch.setattr(sync_mod, "get_d1_account_id", lambda: "fake")
        monkeypatch.setattr(
            sync_mod, "get_d1_database_id", lambda name: "fake",
        )
        monkeypatch.setattr(sync_mod, "get_d1_api_token", lambda: "fake")

        result = sync_mod._sync_one_logical(
            "history", str(sqlite_path),
            page_size=500, dry_run=False,
            prune_local_only=True,
            allow_local_prune_on_drift=False,
        )
        tbl = result["tables"][0]
        assert tbl["prune_blocked_reason"] is not None, (
            "prune must be blocked when delta_d1_minus_sqlite_before < 0"
        )
        assert "refusing to prune" in tbl["prune_blocked_reason"]

        # Local row is preserved.
        conn = sqlite3.connect(sqlite_path)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM MovieHistory WHERE VideoCode='LOCAL-ONLY-002'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 1, "P0-8 regression: blocked prune still deleted the row"

    def test_prune_with_override_actually_deletes(
        self, monkeypatch, tmp_path,
    ):
        """``--allow-local-prune-on-drift`` unlocks the destructive path."""
        sqlite_path = tmp_path / "history.db"
        self._seed_local_movie(sqlite_path, "LOCAL-ONLY-003")

        d1 = _FakeMovieHistoryD1Connection([])
        monkeypatch.setattr(sync_mod, "D1Connection", lambda *a, **k: d1)
        monkeypatch.setattr(sync_mod, "get_d1_account_id", lambda: "fake")
        monkeypatch.setattr(
            sync_mod, "get_d1_database_id", lambda name: "fake",
        )
        monkeypatch.setattr(sync_mod, "get_d1_api_token", lambda: "fake")

        result = sync_mod._sync_one_logical(
            "history", str(sqlite_path),
            page_size=500, dry_run=False,
            prune_local_only=True,
            allow_local_prune_on_drift=True,
        )
        tbl = result["tables"][0]
        assert tbl["prune_blocked_reason"] is None
        assert tbl["pruned_rows"] == 1

        conn = sqlite3.connect(sqlite_path)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM MovieHistory"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 0
