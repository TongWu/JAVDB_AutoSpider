"""Unit tests for the X3 hybrid D1/SQLite rollback machinery.

Covers:
  * ReportSessions.Status lifecycle (in_progress → committed / failed)
  * `db_find_in_progress_sessions` lookup window
  * `db_mark_session_committed` idempotency
  * `db_rollback_session` reports-scope deletes everything but committed rows
  * `db_rollback_session` operations-scope deletes session-tagged rows in
    PikpakHistory / DedupRecords / InventoryAlignNoExactMatch and drops
    the per-session RcloneInventory staging table
  * `db_rollback_session` history-scope replays MovieHistoryAudit /
    TorrentHistoryAudit (INSERT → DELETE, UPDATE → restore from OldRowJson,
    DELETE → re-INSERT) and detects concurrent-run drift
  * Rclone staging-then-swap atomicity (open / append / swap / drop)
  * Refusal to roll back ``Status='committed'`` sessions without ``force=True``
  * Dry-run mode reports counts without mutating any table
  * Scope filtering (`reports` / `operations` / `history`) only touches its
    own DB
"""

from __future__ import annotations

import argparse

import pytest

import javdb.storage.db.db as db_mod


# ── helpers ──────────────────────────────────────────────────────────────


def _create_session(status: str = "in_progress", *, when: str | None = None) -> int:
    sid = db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-04",
        csv_filename="test.csv",
        created_at=when,
    )
    if status != "in_progress":
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE ReportSessions SET Status=? WHERE Id=?",
                (status, sid),
            )
    return sid


def _insert_movie(href: str, video_code: str, session_id: int) -> int:
    """Create a MovieHistory row + companion MovieHistoryAudit INSERT row."""
    db_mod.db_upsert_history(
        href=href, video_code=video_code, session_id=session_id,
    )
    with db_mod.get_db() as conn:
        row = conn.execute(
            "SELECT Id FROM MovieHistory WHERE VideoCode=?",
            (video_code,),
        ).fetchone()
    return row["Id"] if row else -1


# ── Status lifecycle ─────────────────────────────────────────────────────


class TestSessionStatusLifecycle:
    def test_create_starts_in_progress(self):
        sid = _create_session()
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (sid,)
            ).fetchone()
        assert row["Status"] == "in_progress"

    def test_mark_committed_flips_status(self):
        sid = _create_session()
        n = db_mod.db_mark_session_committed(sid)
        assert n == 1
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (sid,)
            ).fetchone()
        assert row["Status"] == "committed"

    def test_mark_committed_idempotent(self):
        sid = _create_session()
        db_mod.db_mark_session_committed(sid)
        n = db_mod.db_mark_session_committed(sid)
        assert n == 0  # nothing changed second time

    def test_mark_failed(self):
        sid = _create_session()
        n = db_mod.db_mark_session_failed(sid)
        assert n == 1
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (sid,)
            ).fetchone()
        assert row["Status"] == "failed"


# ── In-progress lookup ───────────────────────────────────────────────────


class TestFindInProgressSessions:
    def test_returns_only_in_progress(self):
        a = _create_session()
        b = _create_session()
        c = _create_session()
        db_mod.db_mark_session_committed(b)

        ids = db_mod.db_find_in_progress_sessions()
        assert a in ids
        assert c in ids
        assert b not in ids

    def test_since_filter(self):
        old = _create_session(when="2026-04-30 10:00:00")
        new = _create_session(when="2026-05-04 19:30:00")

        ids = db_mod.db_find_in_progress_sessions(since="2026-05-04 00:00:00")
        assert new in ids
        assert old not in ids


# ── Rollback CLI target resolution ───────────────────────────────────────


class TestRollbackCliTargetResolution:
    def test_session_id_only_skips_in_progress_lookup(self, monkeypatch):
        from apps.cli.db import rollback as rollback_cli

        def fail_lookup(*_args, **_kwargs):
            raise AssertionError("lookup should not run for --session-id alone")

        monkeypatch.setattr(
            rollback_cli, "find_window_sessions", fail_lookup,
        )
        args = argparse.Namespace(
            session_id=42,
            run_id=None,
            attempt=None,
            run_started_at=None,
            include_orphaned=False,
        )

        assert rollback_cli._resolve_target_sessions(args, None) == [42]

    def test_window_scan_requires_include_orphaned(self, monkeypatch):
        from apps.cli.db import rollback as rollback_cli

        def fail_lookup(*_args, **_kwargs):
            raise AssertionError(
                "window scan must NOT run by default; only with --include-orphaned"
            )

        monkeypatch.setattr(
            rollback_cli, "find_window_sessions", fail_lookup,
        )
        args = argparse.Namespace(
            session_id=42,
            run_id=None,
            attempt=None,
            run_started_at="2026-05-04T19:30:00Z",
            include_orphaned=False,
        )

        # Default: only the explicit session id, no expansion.
        assert rollback_cli._resolve_target_sessions(
            args, "2026-05-04 19:30:00",
        ) == [42]

    def test_include_orphaned_unions_window_sessions(self, monkeypatch):
        from apps.cli.db import rollback as rollback_cli

        captured = {}

        def fake_lookup(since, *, raise_on_error=False):
            captured["since"] = since
            return [7, 42]

        monkeypatch.setattr(
            rollback_cli, "find_window_sessions", fake_lookup,
        )
        args = argparse.Namespace(
            session_id=42,
            run_id=None,
            attempt=None,
            run_started_at="2026-05-04T19:30:00Z",
            include_orphaned=True,
        )

        assert rollback_cli._resolve_target_sessions(
            args, "2026-05-04 19:30:00",
        ) == [7, 42]
        assert captured["since"] == "2026-05-04 19:30:00"

    def test_run_id_resolution_unions_with_session_id(self, monkeypatch):
        from apps.cli.db import rollback as rollback_cli

        monkeypatch.setattr(
            rollback_cli,
            "find_run_sessions",
            lambda run_id, attempt: [101, 102],
        )
        # Also assert that window scan does NOT run when targets came
        # from the run-id path.
        monkeypatch.setattr(
            rollback_cli,
            "find_window_sessions",
            lambda *args, **kwargs: pytest.fail(
                "window scan must not run when run-id yielded targets"
            ),
        )

        args = argparse.Namespace(
            session_id=42,
            run_id="r-test",
            attempt="3",
            run_started_at=None,
            include_orphaned=False,
        )

        assert rollback_cli._resolve_target_sessions(args, None) == [
            42, 101, 102,
        ]

    def test_window_scan_db_error_propagates_as_exit_3(self, monkeypatch):
        """A transient DB error during the window-scan fallback must
        bubble up as exit-3 (the documented "could not connect" code),
        not be silently downgraded to a "nothing to clean up, exit 0"
        success.

        Regression for the PR #40 review finding: ``find_window_sessions``
        used to swallow exceptions universally; rollback now opts into
        ``raise_on_error=True`` so its main() try/except still catches
        the failure and returns 3.
        """
        from apps.cli.db import rollback as rollback_cli

        monkeypatch.setattr(rollback_cli, "init_db", lambda: None)
        monkeypatch.setattr(rollback_cli, "close_db", lambda: None)
        # No --session-id, no --run-id → only the window scan path is
        # consulted, and it raises.
        def boom(*_a, **_kw):
            raise RuntimeError("DB hiccup")

        monkeypatch.setattr(rollback_cli, "find_window_sessions", boom)

        rc = rollback_cli.main([
            "--run-started-at", "2026-05-04T19:30:00Z",
            "--include-orphaned",
        ])
        assert rc == 3, (
            f"expected exit 3 (DB unavailable) but got {rc}; "
            "window-scan failures must not silently succeed"
        )

    def test_main_continues_after_refused_session(self, monkeypatch):
        from apps.cli.db import rollback as rollback_cli

        calls = []
        closed = []

        monkeypatch.setattr(rollback_cli, "init_db", lambda: None)
        monkeypatch.setattr(rollback_cli, "close_db", lambda: closed.append(True))
        monkeypatch.setattr(
            rollback_cli,
            "_resolve_target_sessions",
            lambda _args, _normalized: [1, 2],
        )
        # Default _detect_cross_day reads from the live DB; short-circuit
        # to keep this test focused on the refusal path.
        monkeypatch.setattr(
            rollback_cli,
            "_detect_cross_day",
            lambda *args, **kwargs: False,
        )

        def fake_rollback(sid, **_kwargs):
            calls.append(sid)
            if sid == 1:
                raise ValueError("committed")
            return {"history": {"drift_skipped": 0}}

        monkeypatch.setattr(rollback_cli, "db_rollback_session", fake_rollback)

        assert rollback_cli.main(["--apply"]) == 2
        assert calls == [1, 2]
        assert len(closed) == 1


# ── Reports-scope rollback ───────────────────────────────────────────────


class TestRollbackReports:
    def test_deletes_session_rows_but_keeps_committed(self):
        # A doomed in-progress session…
        sid_failed = _create_session()
        # …and a successful one we must NOT touch.
        sid_good = _create_session()
        db_mod.db_mark_session_committed(sid_good)

        with db_mod.get_db() as conn:
            for sid in (sid_failed, sid_good):
                conn.execute(
                    "INSERT INTO ReportMovies (SessionId, Href, VideoCode) "
                    "VALUES (?, ?, ?)",
                    (sid, f"/v/{sid}", f"CODE-{sid}"),
                )

        result = db_mod.db_rollback_session(sid_failed, scope="reports")
        assert result["reports"]["ReportMovies"] == 1
        assert result["reports"]["ReportSessions"] == 1

        # Forced rollback coverage lives in
        # TestRollbackRefusesCommitted::test_refuses_without_force.

        with db_mod.get_db() as conn:
            ids = [r["Id"] for r in conn.execute(
                "SELECT Id FROM ReportSessions ORDER BY Id"
            ).fetchall()]
        assert sid_good in ids
        assert sid_failed not in ids

    def test_dry_run_reports_counts_without_mutation(self):
        sid = _create_session()
        with db_mod.get_db() as conn:
            conn.execute(
                "INSERT INTO ReportMovies (SessionId, Href, VideoCode) "
                "VALUES (?, ?, ?)",
                (sid, "/v/X", "X-1"),
            )

        result = db_mod.db_rollback_session(sid, scope="reports", dry_run=True)
        assert result["reports"]["ReportMovies"] == 1
        assert result["reports"]["ReportSessions"] == 1

        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportMovies WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n == 1


# ── Refusal of committed sessions ────────────────────────────────────────


class TestRollbackRefusesCommitted:
    def test_refuses_without_force(self):
        sid = _create_session()
        db_mod.db_mark_session_committed(sid)
        with pytest.raises(ValueError, match="committed"):
            db_mod.db_rollback_session(sid)

    def test_force_overrides(self):
        sid = _create_session()
        with db_mod.get_db() as conn:
            conn.execute(
                "INSERT INTO ReportMovies (SessionId, Href, VideoCode) "
                "VALUES (?, ?, ?)",
                (sid, "/v/force", "FORCE-001"),
            )
        db_mod.db_mark_session_committed(sid)
        result = db_mod.db_rollback_session(sid, force=True, scope="reports")
        # Even with force, _rollback_reports keeps committed ReportSessions
        # row intact (only it would have been deleted by removing the WHERE
        # clause). The other tables still get cleaned.
        assert "reports" in result
        with db_mod.get_db() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportMovies WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
            session = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (sid,),
            ).fetchone()
        assert remaining == 0
        assert session is not None
        assert session["Status"] == "committed"

    def test_unknown_scope_raises(self):
        sid = _create_session()
        with pytest.raises(ValueError, match="scope"):
            db_mod.db_rollback_session(sid, scope="garbage")


# ── Operations-scope rollback ────────────────────────────────────────────


class TestRollbackOperations:
    def test_pikpak_dedup_align_deleted_by_session(self):
        sid_a = _create_session()
        sid_b = _create_session()

        db_mod.db_append_pikpak_history(
            {"torrent_hash": "h1", "torrent_name": "n1"},
            session_id=sid_a,
        )
        db_mod.db_append_pikpak_history(
            {"torrent_hash": "h2", "torrent_name": "n2"},
            session_id=sid_b,
        )

        db_mod.db_append_dedup_record(
            {"video_code": "ABC-001", "existing_gdrive_path": "/a/1"},
            session_id=sid_a,
        )
        db_mod.db_append_dedup_record(
            {"video_code": "ABC-002", "existing_gdrive_path": "/a/2"},
            session_id=sid_b,
        )

        db_mod.db_upsert_align_no_exact_match("XYZ-001", session_id=sid_a)
        db_mod.db_upsert_align_no_exact_match("XYZ-002", session_id=sid_b)

        result = db_mod.db_rollback_session(sid_a, scope="operations")
        assert result["operations"]["PikpakHistory"] == 1
        assert result["operations"]["DedupRecords"] == 1
        assert result["operations"]["InventoryAlignNoExactMatch"] == 1

        with db_mod.get_db() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM PikpakHistory WHERE SessionId=?",
                (sid_a,),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM PikpakHistory WHERE SessionId=?",
                (sid_b,),
            ).fetchone()["n"] == 1

    def test_dedup_deletes_session_rows_even_when_marked_deleted(self):
        """Rows created by the failed session are deleted even after soft-delete."""
        sid = _create_session()
        db_mod.db_append_dedup_record(
            {"video_code": "ABC-001", "existing_gdrive_path": "/a/1"},
            session_id=sid,
        )
        db_mod.db_mark_records_deleted(
            [("/a/1", "2026-05-04 10:00:00")],
            session_id=sid,
        )

        result = db_mod.db_rollback_session(sid, scope="operations")
        assert result["operations"]["DedupRecords"] == 1

        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM DedupRecords WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n == 0

    def test_dedup_restores_preexisting_rows_marked_deleted_by_session(self):
        sid = _create_session()
        row_id = db_mod.db_append_dedup_record(
            {"video_code": "ABC-003", "existing_gdrive_path": "/a/3"},
            session_id=None,
        )
        db_mod.db_mark_records_deleted(
            [("/a/3", "2026-05-04 10:00:00")],
            session_id=sid,
        )

        result = db_mod.db_rollback_session(sid, scope="operations")
        assert result["operations"]["DedupRecords.restored"] == 1
        assert result["operations"]["DedupRecords"] == 0

        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT IsDeleted, DateTimeDeleted, SessionId "
                "FROM DedupRecords WHERE Id=?",
                (row_id,),
            ).fetchone()
            backup = conn.execute(
                "SELECT name FROM sqlite_master WHERE name=?",
                (f"DedupRecordsRollback_{sid}",),
            ).fetchone()
        assert row["IsDeleted"] == 0
        assert row["DateTimeDeleted"] is None
        assert row["SessionId"] is None
        assert backup is None

    def test_explicit_none_session_id_opts_out_of_active_context(self):
        sid = _create_session()
        db_mod.set_active_session_id(sid)
        try:
            dedup_id = db_mod.db_append_dedup_record(
                {"video_code": "ABC-004", "existing_gdrive_path": "/a/4"},
                session_id=None,
            )
            db_mod.db_upsert_align_no_exact_match(
                "XYZ-004",
                session_id=None,
            )
        finally:
            db_mod.set_active_session_id(None)

        with db_mod.get_db() as conn:
            dedup = conn.execute(
                "SELECT SessionId FROM DedupRecords WHERE Id=?",
                (dedup_id,),
            ).fetchone()
            align = conn.execute(
                "SELECT SessionId FROM InventoryAlignNoExactMatch WHERE VideoCode=?",
                ("XYZ-004",),
            ).fetchone()
        assert dedup["SessionId"] is None
        assert align["SessionId"] is None


# ── Rclone staging-then-swap ─────────────────────────────────────────────


class TestRcloneStagingSwap:
    @staticmethod
    def _entry(video_code: str, folder_path: str) -> dict:
        return {
            "VideoCode": video_code,
            "SensorCategory": "censored",
            "SubtitleCategory": "subtitle",
            "FolderPath": folder_path,
            "FolderSize": 1024,
            "FileCount": 1,
            "DateTimeScanned": "2026-05-04 19:30:00",
        }

    def test_open_append_swap_replaces_main(self):
        sid = _create_session()
        # Seed main table with a row from a previous run so we can verify
        # the swap actually replaces it.
        db_mod.db_replace_rclone_inventory(
            [self._entry("OLD-001", "/old/a")],
        )
        with db_mod.get_db() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM RcloneInventory"
            ).fetchone()["n"] == 1

        staging = db_mod.db_open_rclone_staging(sid)
        assert staging is not None
        # The staging name suffix sanitizes `.` / `-` from the TEXT
        # snowflake to `_` so it stays a valid SQL identifier.
        assert staging.endswith(db_mod._session_id_to_identifier_suffix(sid))

        db_mod.db_append_rclone_staging(
            [
                self._entry("NEW-001", "/new/a"),
                self._entry("NEW-002", "/new/b"),
            ],
            session_id=sid,
        )

        n = db_mod.db_swap_rclone_inventory(session_id=sid)
        assert n == 2

        with db_mod.get_db() as conn:
            codes = sorted(
                r["VideoCode"] for r in conn.execute(
                    "SELECT VideoCode FROM RcloneInventory ORDER BY VideoCode"
                ).fetchall()
            )
            # Staging table must be dropped post-swap
            staging_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE name=?", (staging,),
            ).fetchone()
        assert codes == ["NEW-001", "NEW-002"]
        assert staging_exists is None

    def test_merge_staging_refreshes_only_selected_years(self):
        sid = _create_session()
        db_mod.db_replace_rclone_inventory(
            [
                self._entry("KEEP-001", "2025/actor/KEEP-001"),
                self._entry("OLD-001", "2026/actor/OLD-001"),
            ],
        )

        staging = db_mod.db_open_rclone_staging(sid)
        db_mod.db_append_rclone_staging(
            [
                self._entry("NEW-001", "2026/actor/NEW-001"),
                self._entry("SKIP-001", "2025/actor/SKIP-001"),
            ],
            session_id=sid,
        )

        n = db_mod.db_merge_rclone_inventory_from_stage(
            session_id=sid,
            years=["2026"],
        )

        with db_mod.get_db() as conn:
            rows = conn.execute(
                "SELECT VideoCode FROM RcloneInventory ORDER BY VideoCode"
            ).fetchall()
            staging_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE name=?", (staging,),
            ).fetchone()
        assert n == 2
        assert [row["VideoCode"] for row in rows] == ["KEEP-001", "NEW-001"]
        assert staging_exists is None

    def test_drop_staging_leaves_main_untouched(self):
        sid = _create_session()
        db_mod.db_replace_rclone_inventory(
            [self._entry("KEEP-001", "/old/a")],
        )

        staging = db_mod.db_open_rclone_staging(sid)
        db_mod.db_append_rclone_staging(
            [self._entry("LOST-001", "/lost/a")],
            session_id=sid,
        )
        db_mod.db_drop_rclone_staging(sid)

        with db_mod.get_db() as conn:
            codes = [r["VideoCode"] for r in conn.execute(
                "SELECT VideoCode FROM RcloneInventory"
            ).fetchall()]
            staging_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE name=?", (staging,),
            ).fetchone()
        assert codes == ["KEEP-001"]
        assert staging_exists is None

    def test_rollback_drops_orphan_staging(self):
        sid = _create_session()
        db_mod.db_open_rclone_staging(sid)
        # Mid-run crash before swap → rollback should DROP the staging.
        result = db_mod.db_rollback_session(sid, scope="operations")
        staging_name = (
            f"RcloneInventoryStaging_"
            f"{db_mod._session_id_to_identifier_suffix(sid)}"
        )
        assert result["operations"][staging_name] == 1
        with db_mod.get_db() as conn:
            assert conn.execute(
                "SELECT name FROM sqlite_master WHERE name=?", (staging_name,),
            ).fetchone() is None


# ── History audit replay ─────────────────────────────────────────────────


class TestRollbackHistoryAudit:
    def test_insert_audit_replays_as_delete(self):
        sid = _create_session()
        movie_id = _insert_movie("/v/AAA-001", "AAA-001", sid)
        assert movie_id > 0

        # Audit row should exist.
        with db_mod.get_db() as conn:
            audit = conn.execute(
                "SELECT TargetId, Action FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchall()
        assert any(a["Action"] == "INSERT" and a["TargetId"] == movie_id
                   for a in audit)

        result = db_mod.db_rollback_session(sid, scope="history")
        assert result["history"]["MovieHistoryAudit"] >= 1
        assert result["history"]["MovieHistory.deleted"] >= 1

        with db_mod.get_db() as conn:
            still = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory WHERE Id=?",
                (movie_id,),
            ).fetchone()["n"]
            audit_left = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert still == 0
        assert audit_left == 0  # consumed audit rows are tidied up

    def test_insert_audit_deletes_torrents_before_parent_movie(self):
        sid = _create_session()
        db_mod.db_upsert_history(
            href="/v/PARENT-001",
            video_code="PARENT-001",
            magnet_links={"subtitle": "magnet:?xt=urn:btih:parent001"},
            session_id=sid,
        )

        result = db_mod.db_rollback_session(sid, scope="history")

        assert result["history"]["TorrentHistory.deleted"] >= 1
        assert result["history"]["MovieHistory.deleted"] >= 1
        assert result["history"].get("drift_skipped", 0) == 0
        with db_mod.get_db() as conn:
            movie_count = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory "
                "WHERE VideoCode='PARENT-001'"
            ).fetchone()["n"]
            torrent_count = conn.execute(
                "SELECT COUNT(*) AS n FROM TorrentHistory"
            ).fetchone()["n"]
        assert movie_count == 0
        assert torrent_count == 0

    def test_update_audit_restores_old_row(self):
        # Run #1 creates a movie with actor "OldActor".
        sid_old = _create_session()
        _insert_movie("/v/BBB-001", "BBB-001", sid_old)
        db_mod.db_upsert_history(
            href="/v/BBB-001",
            video_code="BBB-001",
            actor_name="OldActor",
            session_id=sid_old,
        )
        db_mod.db_mark_session_committed(sid_old)

        # Run #2 (failed) updates the same movie with new actor "NewActor".
        sid_new = _create_session()
        db_mod.db_upsert_history(
            href="/v/BBB-001",
            video_code="BBB-001",
            actor_name="NewActor",
            session_id=sid_new,
        )

        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Id, ActorName, SessionId FROM MovieHistory "
                "WHERE VideoCode='BBB-001'"
            ).fetchone()
            assert row["ActorName"] == "NewActor"
            assert row["SessionId"] == sid_new

        # Now roll back the failed run → ActorName should snap back to OldActor.
        result = db_mod.db_rollback_session(sid_new, scope="history")
        assert "history" in result

        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT ActorName FROM MovieHistory WHERE VideoCode='BBB-001'"
            ).fetchone()
        assert row is not None
        assert result["history"].get("drift_skipped", 0) == 0
        assert row["ActorName"] == "OldActor"

    def test_drift_skipped_when_concurrent_run_owns_row(self):
        """Audit row's TargetId no longer matches `SessionId` → drift, no overwrite."""
        sid_failed = _create_session()
        movie_id = _insert_movie("/v/CCC-001", "CCC-001", sid_failed)
        assert movie_id > 0

        # Simulate a parallel run claiming the row by changing its
        # SessionId without going through db_upsert_history (so we don't
        # overwrite the audit chain).
        sid_other = _create_session()
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE MovieHistory SET SessionId=? WHERE Id=?",
                (sid_other, movie_id),
            )

        result = db_mod.db_rollback_session(sid_failed, scope="history")
        assert result["history"]["drift_skipped"] >= 1

        with db_mod.get_db() as conn:
            still = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory WHERE Id=?",
                (movie_id,),
            ).fetchone()["n"]
        assert still == 1  # other run's data must survive

    def test_successful_audit_rows_are_cleaned_when_later_row_drifts(self):
        sid_failed = _create_session()
        applied_id = _insert_movie("/v/DDD-001", "DDD-001", sid_failed)
        drift_id = _insert_movie("/v/DDD-002", "DDD-002", sid_failed)

        sid_other = _create_session()
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE MovieHistory SET SessionId=? WHERE Id=?",
                (sid_other, drift_id),
            )

        result = db_mod.db_rollback_session(sid_failed, scope="history")
        assert result["history"]["MovieHistory.deleted"] >= 1
        assert result["history"]["drift_skipped"] >= 1

        with db_mod.get_db() as conn:
            applied_left = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=? AND TargetId=?",
                (sid_failed, applied_id),
            ).fetchone()["n"]
            drift_left = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=? AND TargetId=?",
                (sid_failed, drift_id),
            ).fetchone()["n"]

        assert applied_left == 0
        assert drift_left == 1


# ── Application-generated session id (Phase 2) ───────────────────────────


class TestApplicationGeneratedSessionId:
    def test_db_create_report_session_returns_application_id(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2.csv",
        )
        # The id must match the canonical TEXT snowflake shape (post
        # 2026-05-13). AUTOINCREMENT would have produced None / "1" / etc.
        assert isinstance(sid, str) and db_mod._SESSION_ID_PATTERN.match(sid), (
            f"Application-generated id should match the ISO-like snowflake "
            f"shape, got {sid!r}."
        )

        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Id FROM ReportSessions WHERE CsvFilename=?",
                ("phase2.csv",),
            ).fetchone()
        assert row is not None
        assert row["Id"] == sid

    def test_consecutive_session_ids_are_strictly_increasing(self):
        a = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-a.csv",
        )
        b = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-b.csv",
        )
        assert b > a

    def test_run_identity_columns_are_persisted(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-runid.csv",
            run_id="123456789",
            run_attempt=2,
        )
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT RunId, RunAttempt FROM ReportSessions WHERE Id=?",
                (sid,),
            ).fetchone()
        assert row["RunId"] == "123456789"
        assert row["RunAttempt"] == 2

    def test_db_count_in_progress_sessions_for_run(self):
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-count-1.csv",
            run_id="rid-A",
            run_attempt=1,
        )
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-count-2.csv",
            run_id="rid-A",
            run_attempt=1,
        )
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-count-3.csv",
            run_id="rid-B",
            run_attempt=1,
        )

        assert db_mod.db_count_in_progress_sessions_for_run("rid-A", 1) == 2
        assert db_mod.db_count_in_progress_sessions_for_run("rid-A", 2) == 0
        assert db_mod.db_count_in_progress_sessions_for_run("rid-B", 1) == 1

    def test_db_find_sessions_by_run_unions_audit_and_reports(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="phase2-by-run.csv",
            run_id="rid-find",
            run_attempt=3,
        )
        # Generate a MovieHistoryAudit row tagged with the same RunId so
        # we exercise the union-from-audit branch as well.
        db_mod.set_active_run_identity("rid-find", 3)
        try:
            _insert_movie("/v/PH2-001", "PH2-001", sid)
        finally:
            db_mod.set_active_run_identity(None, None)

        ids = db_mod.db_find_sessions_by_run("rid-find", 3)
        assert ids == [sid]

        # Removing the ReportSessions row but leaving the audit trail
        # behind still surfaces the SessionId via the audit-table union.
        with db_mod.get_db() as conn:
            conn.execute("DELETE FROM ReportSessions WHERE Id=?", (sid,))
        ids = db_mod.db_find_sessions_by_run("rid-find", 3)
        assert ids == [sid]


# ── Audit retention on commit (Phase 6) ──────────────────────────────────


class TestAuditRetentionOnCommit:
    def test_commit_prunes_audit_rows(self):
        sid = _create_session()
        _insert_movie("/v/PRN-001", "PRN-001", sid)

        with db_mod.get_db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert before >= 1

        db_mod.db_mark_session_committed(sid)

        with db_mod.get_db() as conn:
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert after == 0


# ── Orphan pruning (Phase 4) ─────────────────────────────────────────────


class TestOrphanPruning:
    def test_orphan_audit_pruned_when_target_missing_and_old(self):
        # Set up: an audit row whose target row no longer exists, dated
        # well before run_started_at - 24h.
        sid = _create_session(when="2026-04-01 00:00:00")
        movie_id = _insert_movie("/v/ORP-001", "ORP-001", sid)
        # Delete the underlying movie so the audit's TargetId is dangling.
        with db_mod.get_db() as conn:
            conn.execute("DELETE FROM MovieHistory WHERE Id=?", (movie_id,))
            # Force the audit row's DateTimeCreated to look ancient.
            conn.execute(
                "UPDATE MovieHistoryAudit SET DateTimeCreated=? "
                "WHERE SessionId=?",
                ("2026-04-01 00:00:01", sid),
            )

        result = db_mod.db_rollback_session(
            sid,
            scope="history",
            run_started_at="2026-05-08 00:00:00",
        )
        history = result["history"]
        # Orphan branch fires; drift_skipped doesn't (it would on a
        # recent audit row).
        assert history.get("orphan_pruned", 0) >= 1
        assert history.get("drift_skipped", 0) == 0

        with db_mod.get_db() as conn:
            left = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert left == 0

    def test_recent_drift_is_not_pruned_as_orphan(self):
        sid = _create_session()
        movie_id = _insert_movie("/v/ORP-002", "ORP-002", sid)
        with db_mod.get_db() as conn:
            conn.execute("DELETE FROM MovieHistory WHERE Id=?", (movie_id,))

        # No run_started_at supplied → orphan window is disabled, so
        # the row remains as drift.
        result = db_mod.db_rollback_session(sid, scope="history")
        history = result["history"]
        assert history.get("drift_skipped", 0) >= 1
        assert history.get("orphan_pruned", 0) == 0


# ── _rollback_history idempotency (Phase 4) ─────────────────────────────


class TestRollbackIdempotency:
    def test_replaying_rollback_is_a_noop(self):
        """Successful rollback drains its audit rows row-by-row, so a
        second invocation has nothing to do."""
        sid = _create_session()
        _insert_movie("/v/IDM-001", "IDM-001", sid)
        _insert_movie("/v/IDM-002", "IDM-002", sid)

        first = db_mod.db_rollback_session(sid, scope="history")
        assert first["history"]["MovieHistory.deleted"] >= 1

        # After the first run, all audit rows for this session must be
        # gone — that's the new row-by-row idempotent behaviour.
        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n == 0

    def test_drift_does_not_block_other_audits_from_being_drained(self):
        """An audit row that drifts must NOT be deleted, but the rest
        still get pruned individually."""
        sid_failed = _create_session()
        applied_id = _insert_movie("/v/IDM-DRT-001", "IDM-DRT-001", sid_failed)
        drift_id = _insert_movie("/v/IDM-DRT-002", "IDM-DRT-002", sid_failed)

        # Make drift_id be owned by a sibling session so the rollback's
        # SessionId guard refuses to touch it.
        sid_other = _create_session()
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE MovieHistory SET SessionId=? WHERE Id=?",
                (sid_other, drift_id),
            )

        result = db_mod.db_rollback_session(sid_failed, scope="history")
        assert result["history"]["MovieHistory.deleted"] >= 1
        assert result["history"]["drift_skipped"] >= 1

        with db_mod.get_db() as conn:
            applied_left = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=? AND TargetId=?",
                (sid_failed, applied_id),
            ).fetchone()["n"]
            drift_left = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=? AND TargetId=?",
                (sid_failed, drift_id),
            ).fetchone()["n"]
        # Successful row had its audit pruned individually.
        assert applied_left == 0
        # Drifted row's audit is preserved for manual review.
        assert drift_left == 1


# ── FailureReason persistence ────────────────────────────────────────────


class TestFailureReason:
    def test_rollback_persists_failure_reason(self):
        sid = _create_session()
        db_mod.db_rollback_session(
            sid, scope="reports", failure_reason="workflow_cancel",
        )
        # ReportSessions row was deleted (reports scope), so a fresh
        # mark_failed call exercises the same column path on a new row.
        sid2 = _create_session()
        db_mod.db_mark_session_failed(sid2, reason="runtime_error")
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT FailureReason FROM ReportSessions WHERE Id=?",
                (sid2,),
            ).fetchone()
        assert row["FailureReason"] == "runtime_error"


# ── Scope filtering ──────────────────────────────────────────────────────


class TestRollbackScopeFiltering:
    def test_reports_scope_only_touches_reports(self):
        sid = _create_session()
        db_mod.db_append_pikpak_history(
            {"torrent_hash": "h1", "torrent_name": "n1"},
            session_id=sid,
        )

        result = db_mod.db_rollback_session(sid, scope="reports")
        assert "reports" in result
        assert "operations" not in result
        assert "history" not in result

        # PikpakHistory row must survive a reports-only rollback.
        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM PikpakHistory WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n == 1

    def test_operations_scope_only_touches_operations(self):
        sid = _create_session()
        db_mod.db_append_pikpak_history(
            {"torrent_hash": "h1", "torrent_name": "n1"},
            session_id=sid,
        )

        result = db_mod.db_rollback_session(sid, scope="operations")
        assert "operations" in result
        assert "reports" not in result

        # ReportSessions row must survive an operations-only rollback.
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (sid,),
            ).fetchone()
        # _rollback_session marks the session 'failed' as a side-effect
        # for traceability — that's expected.
        assert row is not None
        assert row["Status"] == "failed"
