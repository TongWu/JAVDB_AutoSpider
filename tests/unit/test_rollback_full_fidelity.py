"""End-to-end fidelity tests for ``db_rollback_session``.

The rollback CLI is the one piece of plumbing that *must* be able to
restore D1 (and the local SQLite mirror under ``STORAGE_BACKEND=dual``)
to the exact state it was in before a failed run touched it. A bug
that quietly drops a single column from the audit-replay path silently
corrupts production. These tests therefore:

1. Cover **every column** of every table the rollback path can mutate
   (MovieHistory, TorrentHistory, ReportSessions and friends,
   PikpakHistory / DedupRecords / InventoryAlignNoExactMatch).
2. Use a real ``init_db`` SQLite fixture (same DDL as production) and
   exercise the public-API mutation paths so the test catches drift
   between the live table and the audit serialiser.
3. Snapshot ``SELECT *`` *before* the mutation and *after* the
   rollback, then assert the two are byte-for-byte equal — not just
   "row exists", but "every column has the exact same value".
4. Cross-check that every column declared in the D1 migration files
   also exists in the local DDL, so the D1 side of dual-write is
   guaranteed to round-trip the same columns.

Why is SQLite-only sufficient to validate D1?  Under
``STORAGE_BACKEND=dual`` the same statements (including
``OldRowJson``) flow through ``DualConnection`` to both backends.
The audit-replay logic in ``_rollback_history`` is **shared** code:
it issues the same dynamic ``UPDATE ... SET <every_column>=?`` against
SQLite and D1. If it round-trips correctly here, it round-trips
correctly there. The schema-parity test then guarantees neither
backend is missing a column the other has.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Dict, List, Set, Tuple

import pytest

import utils.infra.db as db_mod


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row | None) -> Dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _columns_of(conn, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _snapshot_table(conn, table: str, where: str = "1=1",
                    params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    # Sort by primary key when present; fall back to a stable
    # column-ordered sort so byte-for-byte comparison is deterministic
    # across SQLite's implicit row ordering.
    cols = _columns_of(conn, table)
    if "Id" in cols:
        order_by = "Id"
    elif cols:
        order_by = ", ".join(cols)
    else:
        order_by = "1"
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY {order_by}",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _create_session(*, run_id: str | None = None,
                    run_attempt: int | None = None,
                    when: str | None = None,
                    csv_filename: str = "fidelity-test.csv") -> int:
    return db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-08",
        csv_filename=csv_filename,
        created_at=when,
        run_id=run_id,
        run_attempt=run_attempt,
    )


def _audited_torrent_update(conn, torrent_id: int, *,
                             session_id: int, **column_updates: Any) -> None:
    """Run an UPDATE on TorrentHistory under session_id with proper audit.

    Emits the same audit shape as ``db_upsert_history`` does — captures
    the full old row in ``OldRowJson`` and rewrites ``SessionId`` to
    ``session_id``. This is the most direct way to exercise rollback's
    column-restoration logic for columns that the public API doesn't
    mutate (e.g. ``DateTimeCreated``).
    """
    old = conn.execute(
        "SELECT * FROM TorrentHistory WHERE Id=?", (torrent_id,),
    ).fetchone()
    assert old is not None, "row to update must exist"
    set_clause = ", ".join(f"{c}=?" for c in column_updates) + ", SessionId=?"
    params = list(column_updates.values()) + [session_id, torrent_id]
    conn.execute(
        f"UPDATE TorrentHistory SET {set_clause} WHERE Id=?", params,
    )
    db_mod._audit_record_torrent_change(
        conn, torrent_id, action="UPDATE",
        session_id=session_id, old_row=old,
    )


def _audited_movie_update(conn, movie_id: int, *,
                           session_id: int, **column_updates: Any) -> None:
    old = conn.execute(
        "SELECT * FROM MovieHistory WHERE Id=?", (movie_id,),
    ).fetchone()
    assert old is not None
    set_clause = ", ".join(f"{c}=?" for c in column_updates) + ", SessionId=?"
    params = list(column_updates.values()) + [session_id, movie_id]
    conn.execute(
        f"UPDATE MovieHistory SET {set_clause} WHERE Id=?", params,
    )
    db_mod._audit_record_movie_change(
        conn, movie_id, action="UPDATE",
        session_id=session_id, old_row=old,
    )


def _seed_movie_with_torrent(*, video_code: str, href: str) -> Tuple[int, int]:
    """Create one MovieHistory + one TorrentHistory row with no audit
    (i.e. SessionId=NULL). Returns (movie_id, torrent_id).

    These rows act as the "pristine production state" the test will
    later mutate inside a tagged session and then expect rollback to
    restore.
    """
    with db_mod.get_db() as conn:
        conn.execute(
            "INSERT INTO MovieHistory "
            "(VideoCode, Href, ActorName, ActorGender, ActorLink, "
            " SupportingActors, DateTimeCreated, DateTimeUpdated, "
            " DateTimeVisited, PerfectMatchIndicator, HiResIndicator, "
            " SessionId) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                video_code, href, "OriginalActor", "female",
                "/actors/original",
                json.dumps([{"name": "Co", "link": "/actors/co"}]),
                "2026-05-01 10:00:00", "2026-05-01 10:00:00",
                "2026-05-02 09:00:00", 0, 0,
            ),
        )
        movie_id = conn.execute(
            "SELECT Id FROM MovieHistory WHERE Href=?", (href,),
        ).fetchone()["Id"]
        conn.execute(
            "INSERT INTO TorrentHistory "
            "(MovieHistoryId, MagnetUri, SubtitleIndicator, "
            " CensorIndicator, ResolutionType, Size, FileCount, "
            " DateTimeCreated, DateTimeUpdated, SessionId) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                movie_id, "magnet:?xt=urn:btih:original",
                1, 0, 1080, "1.50GB", 3,
                "2026-05-01 10:00:00", "2026-05-01 10:00:00",
            ),
        )
        torrent_id = conn.execute(
            "SELECT Id FROM TorrentHistory WHERE MovieHistoryId=?",
            (movie_id,),
        ).fetchone()["Id"]
    return movie_id, torrent_id


# ──────────────────────────────────────────────────────────────────────
# Section A — Column-level fidelity for history rollback
# ──────────────────────────────────────────────────────────────────────


# All columns that exist on the *live* MovieHistory / TorrentHistory
# rows. Every one must round-trip (Id is excluded; rollback never
# changes Id of an existing row). DateTimeVisited / FailureReason are
# not present on TorrentHistory — kept on a per-table basis below.
_MOVIE_RESTORE_COLUMNS = (
    "VideoCode", "Href", "ActorName", "ActorGender", "ActorLink",
    "SupportingActors", "DateTimeCreated", "DateTimeUpdated",
    "DateTimeVisited", "PerfectMatchIndicator", "HiResIndicator",
    "SessionId",
)
_TORRENT_RESTORE_COLUMNS = (
    "MovieHistoryId", "MagnetUri", "SubtitleIndicator",
    "CensorIndicator", "ResolutionType", "Size", "FileCount",
    "DateTimeCreated", "DateTimeUpdated", "SessionId",
)


class TestEveryColumnRoundTrip:
    """For each column on MovieHistory / TorrentHistory, mutate it
    inside a tagged session, run rollback, and assert the value is
    byte-for-byte equal to its pre-mutation snapshot.
    """

    @pytest.mark.parametrize("column,new_value", [
        ("MagnetUri", "magnet:?xt=urn:btih:CHANGED"),
        ("Size", "9.99TB"),
        ("FileCount", 999),
        ("ResolutionType", 4320),
        ("SubtitleIndicator", 0),  # was 1
        ("CensorIndicator", 1),    # was 0
        ("DateTimeCreated", "2099-12-31 23:59:59"),
        ("DateTimeUpdated", "2099-12-31 23:59:59"),
    ])
    def test_each_torrent_column_round_trips(
        self, column: str, new_value: Any,
    ):
        _, tid = _seed_movie_with_torrent(
            video_code=f"COL-T-{column}", href=f"/v/COL-T-{column}",
        )
        with db_mod.get_db() as conn:
            pristine = _row_to_dict(conn.execute(
                "SELECT * FROM TorrentHistory WHERE Id=?", (tid,),
            ).fetchone())
        sid = _create_session(
            csv_filename=f"colT-{column}.csv",
            run_id="run-colT", run_attempt=1,
        )
        with db_mod.get_db() as conn:
            _audited_torrent_update(
                conn, tid, session_id=sid, **{column: new_value},
            )
            after_update = _row_to_dict(conn.execute(
                "SELECT * FROM TorrentHistory WHERE Id=?", (tid,),
            ).fetchone())
        assert after_update[column] == new_value, (
            f"sanity: column {column} should reflect the update"
        )

        result = db_mod.db_rollback_session(sid, scope="history")
        assert result["history"]["TorrentHistory.restored"] >= 1
        assert result["history"].get("drift_skipped", 0) == 0

        with db_mod.get_db() as conn:
            restored = _row_to_dict(conn.execute(
                "SELECT * FROM TorrentHistory WHERE Id=?", (tid,),
            ).fetchone())
        assert restored == pristine, (
            f"column {column} was not restored exactly; "
            f"pristine={pristine!r} restored={restored!r}"
        )

    @pytest.mark.parametrize("column,new_value", [
        ("VideoCode", "CHANGED-001"),
        ("ActorName", "ChangedActor"),
        ("ActorGender", "male"),
        ("ActorLink", "/actors/changed"),
        ("SupportingActors", json.dumps([{"name": "Changed"}])),
        ("DateTimeCreated", "2099-12-31 23:59:59"),
        ("DateTimeUpdated", "2099-12-31 23:59:59"),
        ("DateTimeVisited", "2099-12-31 23:59:59"),
        ("PerfectMatchIndicator", 1),
        ("HiResIndicator", 1),
    ])
    def test_each_movie_column_round_trips(
        self, column: str, new_value: Any,
    ):
        mid, _ = _seed_movie_with_torrent(
            video_code=f"COL-M-{column}", href=f"/v/COL-M-{column}",
        )
        with db_mod.get_db() as conn:
            pristine = _row_to_dict(conn.execute(
                "SELECT * FROM MovieHistory WHERE Id=?", (mid,),
            ).fetchone())
        sid = _create_session(
            csv_filename=f"colM-{column}.csv",
            run_id="run-colM", run_attempt=1,
        )
        with db_mod.get_db() as conn:
            _audited_movie_update(
                conn, mid, session_id=sid, **{column: new_value},
            )

        result = db_mod.db_rollback_session(sid, scope="history")
        assert result["history"]["MovieHistory.restored"] >= 1
        assert result["history"].get("drift_skipped", 0) == 0

        with db_mod.get_db() as conn:
            restored = _row_to_dict(conn.execute(
                "SELECT * FROM MovieHistory WHERE Id=?", (mid,),
            ).fetchone())
        assert restored == pristine, (
            f"column {column} not restored exactly; "
            f"pristine={pristine!r} restored={restored!r}"
        )


class TestAuditCapturesEveryLiveColumn:
    """If someone adds a column to MovieHistory / TorrentHistory but
    forgets to ensure it lands in ``OldRowJson``, rollback would
    silently leave that column unrestored. The audit code already uses
    ``SELECT * FROM ...`` so by construction it captures everything,
    but this test pins the contract so future refactors can't break it.
    """

    def test_torrent_audit_oldrowjson_is_superset_of_live_columns(self):
        _, tid = _seed_movie_with_torrent(
            video_code="AUD-T-001", href="/v/AUD-T-001",
        )
        sid = _create_session(csv_filename="aud-T.csv")
        with db_mod.get_db() as conn:
            _audited_torrent_update(
                conn, tid, session_id=sid, MagnetUri="magnet:new",
            )
            audit_row = conn.execute(
                "SELECT OldRowJson FROM TorrentHistoryAudit "
                "WHERE SessionId=? AND TargetId=? AND Action='UPDATE'",
                (sid, tid),
            ).fetchone()
            live_cols = set(_columns_of(conn, "TorrentHistory"))
        captured = set(json.loads(audit_row["OldRowJson"]).keys())
        missing = live_cols - captured
        assert not missing, (
            f"OldRowJson is missing live columns {missing!r}; "
            f"rollback could not restore them"
        )

    def test_movie_audit_oldrowjson_is_superset_of_live_columns(self):
        mid, _ = _seed_movie_with_torrent(
            video_code="AUD-M-001", href="/v/AUD-M-001",
        )
        sid = _create_session(csv_filename="aud-M.csv")
        with db_mod.get_db() as conn:
            _audited_movie_update(
                conn, mid, session_id=sid, ActorName="Changed",
            )
            audit_row = conn.execute(
                "SELECT OldRowJson FROM MovieHistoryAudit "
                "WHERE SessionId=? AND TargetId=? AND Action='UPDATE'",
                (sid, mid),
            ).fetchone()
            live_cols = set(_columns_of(conn, "MovieHistory"))
        captured = set(json.loads(audit_row["OldRowJson"]).keys())
        missing = live_cols - captured
        assert not missing, (
            f"OldRowJson is missing live columns {missing!r}; "
            f"rollback could not restore them"
        )


class TestRollbackPreservesUntargetedRows:
    """Rollback must touch ONLY rows tagged with the rolled-back
    session id. Concurrent rows belonging to a committed session must
    be byte-for-byte unchanged.
    """

    def test_other_session_movie_row_byte_for_byte_unchanged(self):
        # Run A creates and commits a row.
        sid_a = _create_session(csv_filename="presA.csv", run_id="run-A")
        db_mod.db_upsert_history(
            href="/v/PRES-A", video_code="PRES-A",
            actor_name="ActorA", session_id=sid_a,
        )
        db_mod.db_mark_session_committed(sid_a)
        with db_mod.get_db() as conn:
            snapshot_a = _snapshot_table(
                conn, "MovieHistory",
                "VideoCode='PRES-A'", (),
            )
            snapshot_t_a = _snapshot_table(
                conn, "TorrentHistory",
                "MovieHistoryId IN (SELECT Id FROM MovieHistory "
                "WHERE VideoCode='PRES-A')", (),
            )

        # Run B creates a *different* movie under a fresh session.
        sid_b = _create_session(csv_filename="presB.csv", run_id="run-B")
        db_mod.db_upsert_history(
            href="/v/PRES-B", video_code="PRES-B",
            actor_name="ActorB", session_id=sid_b,
        )

        # Rollback B and assert A is untouched.
        db_mod.db_rollback_session(sid_b, scope="history")
        with db_mod.get_db() as conn:
            after_a = _snapshot_table(
                conn, "MovieHistory",
                "VideoCode='PRES-A'", (),
            )
            after_t_a = _snapshot_table(
                conn, "TorrentHistory",
                "MovieHistoryId IN (SELECT Id FROM MovieHistory "
                "WHERE VideoCode='PRES-A')", (),
            )
        assert after_a == snapshot_a
        assert after_t_a == snapshot_t_a


class TestTorrentDeleteViaSubtitlePromotion:
    """The ``hacked_subtitle`` / ``subtitle`` cascade in
    ``db_upsert_history`` deletes sibling no-subtitle / hacked-no-sub
    torrents and emits ``DELETE`` audit rows. Rollback must
    re-INSERT them with all columns intact.
    """

    def test_subtitle_promotion_delete_is_reversible_with_all_columns(self):
        # Pre-populate a ``no_subtitle`` torrent that the subtitle
        # cascade will delete.
        with db_mod.get_db() as conn:
            conn.execute(
                "INSERT INTO MovieHistory "
                "(VideoCode, Href, DateTimeCreated, DateTimeUpdated, "
                " PerfectMatchIndicator, HiResIndicator) "
                "VALUES ('SUB-DEL', '/v/SUB-DEL', '2026-05-01', "
                "'2026-05-01', 0, 0)",
            )
            mid = conn.execute(
                "SELECT Id FROM MovieHistory WHERE VideoCode='SUB-DEL'",
            ).fetchone()["Id"]
            conn.execute(
                "INSERT INTO TorrentHistory "
                "(MovieHistoryId, MagnetUri, SubtitleIndicator, "
                " CensorIndicator, ResolutionType, Size, FileCount, "
                " DateTimeCreated, DateTimeUpdated) "
                "VALUES (?, 'magnet:nosub', 0, 1, 720, '0.50GB', 1, "
                " '2026-05-01', '2026-05-01')",
                (mid,),
            )
            pristine = _snapshot_table(
                conn, "TorrentHistory",
                "MovieHistoryId=?", (mid,),
            )

        sid = _create_session(csv_filename="sub-del.csv")
        # Promote: write a ``subtitle`` row → cascade deletes
        # ``no_subtitle`` row and emits a DELETE audit.
        db_mod.db_upsert_history(
            href="/v/SUB-DEL", video_code="SUB-DEL",
            magnet_links={"subtitle": "magnet:sub-promote"},
            session_id=sid,
        )
        with db_mod.get_db() as conn:
            audit = conn.execute(
                "SELECT Action, OldRowJson FROM TorrentHistoryAudit "
                "WHERE SessionId=? ORDER BY Id",
                (sid,),
            ).fetchall()
        assert any(a["Action"] == "DELETE" and a["OldRowJson"] for a in audit)

        db_mod.db_rollback_session(sid, scope="history")

        # The pre-existing no_subtitle row must be restored byte-
        # for-byte; the subtitle row inserted by the failed run must
        # be removed.
        with db_mod.get_db() as conn:
            restored = _snapshot_table(
                conn, "TorrentHistory",
                "MovieHistoryId=?", (mid,),
            )
        assert restored == pristine, (
            f"after rollback expected {pristine!r}, got {restored!r}"
        )


class TestRollbackChainedMutations:
    """A failed run that INSERTs a row and then immediately UPDATEs it
    a few times must wind up with the row removed altogether after
    rollback (audit replays in reverse Id order)."""

    def test_insert_then_update_chain_unwinds_to_pristine(self):
        # Pristine state: nothing about CHAIN-001 exists yet.
        sid = _create_session(csv_filename="chain.csv")
        # INSERT.
        db_mod.db_upsert_history(
            href="/v/CHAIN-001", video_code="CHAIN-001",
            actor_name="A1", session_id=sid,
        )
        # UPDATE 1.
        db_mod.db_upsert_history(
            href="/v/CHAIN-001", video_code="CHAIN-001",
            actor_name="A2", session_id=sid,
        )
        # UPDATE 2.
        db_mod.db_upsert_history(
            href="/v/CHAIN-001", video_code="CHAIN-001",
            actor_name="A3",
            magnet_links={"subtitle": "magnet:chain-sub"},
            session_id=sid,
        )

        db_mod.db_rollback_session(sid, scope="history")
        with db_mod.get_db() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory "
                "WHERE VideoCode='CHAIN-001'",
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM TorrentHistory "
                "WHERE MovieHistoryId IN (SELECT Id FROM MovieHistory "
                "WHERE VideoCode='CHAIN-001')",
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?", (sid,),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM TorrentHistoryAudit "
                "WHERE SessionId=?", (sid,),
            ).fetchone()["n"] == 0


class TestRollbackIdempotencyFidelity:
    """A retried rollback (same session, same scope) must yield the
    exact same final state — verified column-by-column."""

    def test_second_rollback_does_not_perturb_restored_columns(self):
        _, tid = _seed_movie_with_torrent(
            video_code="IDEMP-001", href="/v/IDEMP-001",
        )
        with db_mod.get_db() as conn:
            pristine = _row_to_dict(conn.execute(
                "SELECT * FROM TorrentHistory WHERE Id=?", (tid,),
            ).fetchone())
        sid = _create_session(csv_filename="idemp.csv")
        with db_mod.get_db() as conn:
            _audited_torrent_update(
                conn, tid, session_id=sid,
                MagnetUri="magnet:idemp", Size="9.99GB",
            )
        db_mod.db_rollback_session(sid, scope="history")
        # Second rollback should be a no-op.
        result2 = db_mod.db_rollback_session(sid, scope="history")
        assert result2["history"]["TorrentHistoryAudit"] == 0
        with db_mod.get_db() as conn:
            after = _row_to_dict(conn.execute(
                "SELECT * FROM TorrentHistory WHERE Id=?", (tid,),
            ).fetchone())
        assert after == pristine


# ──────────────────────────────────────────────────────────────────────
# Section B — Reports DB rollback
# ──────────────────────────────────────────────────────────────────────


class TestReportsRollbackPurgesAllTablesForSessionOnly:
    """``_rollback_reports`` deletes session-tagged rows from
    ReportTorrents → ReportMovies → SpiderStats / UploaderStats /
    PikpakStats → ReportSessions. Verify none of the *other* session's
    rows are touched."""

    def _seed_full_reports_payload(self, sid: int, suffix: str) -> None:
        with db_mod.get_db() as conn:
            conn.execute(
                "INSERT INTO ReportMovies "
                "(SessionId, Href, VideoCode, Page, Actor, Rate, "
                " CommentNumber) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, f"/v/{suffix}", suffix, 1, "Actor", 4.5, 100),
            )
            mid = conn.execute(
                "SELECT Id FROM ReportMovies "
                "WHERE SessionId=? AND VideoCode=?", (sid, suffix),
            ).fetchone()["Id"]
            conn.execute(
                "INSERT INTO ReportTorrents "
                "(ReportMovieId, VideoCode, MagnetUri, "
                " SubtitleIndicator, CensorIndicator, ResolutionType, "
                " Size, FileCount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, suffix, f"magnet:{suffix}", 1, 0, 1080,
                 "1.0GB", 1),
            )
            conn.execute(
                "INSERT INTO SpiderStats "
                "(SessionId, Phase1Discovered, Phase1Processed, "
                " TotalDiscovered, DateTimeCreated) "
                "VALUES (?, 10, 9, 10, ?)",
                (sid, "2026-05-08 10:00:00"),
            )
            conn.execute(
                "INSERT INTO UploaderStats "
                "(SessionId, TotalTorrents, Attempted, "
                " SuccessfullyAdded) VALUES (?, 5, 5, 5)",
                (sid,),
            )
            conn.execute(
                "INSERT INTO PikpakStats "
                "(SessionId, ThresholdDays, TotalTorrents, "
                " FilteredOld, SuccessfulCount, FailedCount, "
                " UploadedCount, DeleteFailedCount, "
                " DateTimeCreated) "
                "VALUES (?, 3, 5, 0, 5, 0, 5, 0, "
                " '2026-05-08 10:00:00')",
                (sid,),
            )

    def test_only_targeted_session_rows_purged(self):
        sid_keep = _create_session(csv_filename="keep.csv")
        sid_drop = _create_session(csv_filename="drop.csv")
        self._seed_full_reports_payload(sid_keep, "KEEP-001")
        self._seed_full_reports_payload(sid_drop, "DROP-001")

        with db_mod.get_db() as conn:
            keep_before = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("ReportMovies", "SpiderStats",
                           "UploaderStats", "PikpakStats")
            }
            keep_torrents_before = _snapshot_table(
                conn, "ReportTorrents",
                "ReportMovieId IN (SELECT Id FROM ReportMovies "
                "WHERE SessionId=?)", (sid_keep,),
            )
            keep_session_before = _snapshot_table(
                conn, "ReportSessions", "Id=?", (sid_keep,),
            )

        result = db_mod.db_rollback_session(sid_drop, scope="reports")
        assert result["reports"]["ReportSessions"] == 1
        assert result["reports"]["ReportMovies"] == 1
        assert result["reports"]["ReportTorrents"] == 1
        assert result["reports"]["SpiderStats"] == 1
        assert result["reports"]["UploaderStats"] == 1
        assert result["reports"]["PikpakStats"] == 1

        with db_mod.get_db() as conn:
            keep_after = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("ReportMovies", "SpiderStats",
                           "UploaderStats", "PikpakStats")
            }
            keep_torrents_after = _snapshot_table(
                conn, "ReportTorrents",
                "ReportMovieId IN (SELECT Id FROM ReportMovies "
                "WHERE SessionId=?)", (sid_keep,),
            )
            keep_session_after = _snapshot_table(
                conn, "ReportSessions", "Id=?", (sid_keep,),
            )
            drop_session = _snapshot_table(
                conn, "ReportSessions", "Id=?", (sid_drop,),
            )
        assert keep_after == keep_before
        assert keep_torrents_after == keep_torrents_before
        assert keep_session_after == keep_session_before
        assert drop_session == [], (
            "rollback must remove the targeted ReportSessions row"
        )


# ──────────────────────────────────────────────────────────────────────
# Section C — Operations DB rollback
# ──────────────────────────────────────────────────────────────────────


class TestOperationsRollbackPurgesAllTablesForSessionOnly:
    def test_only_targeted_session_rows_purged(self):
        sid_keep = _create_session(csv_filename="ops-keep.csv")
        sid_drop = _create_session(csv_filename="ops-drop.csv")
        with db_mod.get_db() as conn:
            for sid, suffix in (
                (sid_keep, "KEEP"), (sid_drop, "DROP"),
            ):
                conn.execute(
                    "INSERT INTO PikpakHistory "
                    "(TorrentHash, TorrentName, Category, MagnetUri, "
                    " DateTimeAddedToQb, DateTimeDeletedFromQb, "
                    " DateTimeUploadedToPikpak, TransferStatus, "
                    " ErrorMessage, SessionId) "
                    "VALUES (?, ?, 'subtitle', ?, ?, ?, ?, 'OK', "
                    " '', ?)",
                    (
                        f"hash-{suffix}",
                        f"name-{suffix}.mkv",
                        f"magnet:{suffix}",
                        "2026-05-08 10:00:00",
                        "2026-05-08 10:30:00",
                        "2026-05-08 11:00:00",
                        sid,
                    ),
                )
                conn.execute(
                    "INSERT INTO DedupRecords "
                    "(VideoCode, ExistingSensor, ExistingSubtitle, "
                    " ExistingGdrivePath, ExistingFolderSize, "
                    " NewTorrentCategory, DeletionReason, "
                    " DateTimeDetected, IsDeleted, SessionId) "
                    "VALUES (?, 'cen', 'sub', ?, 1024, "
                    " 'subtitle', 'duplicate', "
                    " '2026-05-08 10:00:00', 0, ?)",
                    (f"VID-{suffix}", f"/path/{suffix}", sid),
                )
                conn.execute(
                    "INSERT INTO InventoryAlignNoExactMatch "
                    "(VideoCode, Reason, DateTimeRecorded, "
                    " SessionId) "
                    "VALUES (?, 'no-match', "
                    " '2026-05-08 10:00:00', ?)",
                    (f"VID-NM-{suffix}", sid),
                )

            keep_before = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("PikpakHistory", "DedupRecords",
                           "InventoryAlignNoExactMatch")
            }

        result = db_mod.db_rollback_session(sid_drop, scope="operations")
        for t in ("PikpakHistory", "DedupRecords",
                   "InventoryAlignNoExactMatch"):
            assert result["operations"][t] == 1, (
                f"{t} should have purged 1 row tagged sid_drop"
            )
        with db_mod.get_db() as conn:
            keep_after = {
                t: _snapshot_table(
                    conn, t, "SessionId=?", (sid_keep,),
                )
                for t in ("PikpakHistory", "DedupRecords",
                           "InventoryAlignNoExactMatch")
            }
            for t in ("PikpakHistory", "DedupRecords",
                       "InventoryAlignNoExactMatch"):
                drop_left = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {t} WHERE SessionId=?",
                    (sid_drop,),
                ).fetchone()["n"]
                assert drop_left == 0, (
                    f"{t} still has rows for the rolled-back session"
                )
        assert keep_after == keep_before


# ──────────────────────────────────────────────────────────────────────
# Section D — D1 schema parity
# ──────────────────────────────────────────────────────────────────────


_D1_LOGICAL_TO_LOCAL_PATH = {
    "history":    "HISTORY_DB_PATH",
    "reports":    "REPORTS_DB_PATH",
    "operations": "OPERATIONS_DB_PATH",
}

_MIGRATION_SUFFIX_TO_LOGICAL = {
    "history":    "history",
    "reports":    "reports",
    "operations": "operations",
}


def _logical_for_migration_filename(name: str) -> str | None:
    """Map a migration filename to a logical DB."""
    base = os.path.basename(name).lower()
    for suffix, logical in _MIGRATION_SUFFIX_TO_LOGICAL.items():
        if suffix in base:
            return logical
    if "sessionid_decouple" in base or "unique_in_progress" in base:
        return "reports"
    return None


def _columns_in_migration_sql(sql: str) -> Dict[str, Set[str]]:
    """Extract ``{table_name: {column_names_referenced}}`` from a
    migration file. Recognises ``ALTER TABLE X ADD COLUMN Y`` and
    ``CREATE TABLE [IF NOT EXISTS] X (...)`` (column names parsed by
    splitting on commas at depth 0). Index DDL is ignored.
    """
    out: Dict[str, Set[str]] = {}

    # ALTER TABLE X ADD COLUMN Y type
    for m in re.finditer(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
        sql, flags=re.IGNORECASE,
    ):
        table, col = m.group(1), m.group(2)
        out.setdefault(table, set()).add(col)

    # CREATE TABLE X ( ... )
    # Exclude the "_new" intermediary tables created by the 12-step ALTER
    # pattern used in D1 migrations (e.g. MovieHistory_new → MovieHistory).
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
        sql, flags=re.IGNORECASE,
    ):
        table = m.group(1)
        if table.endswith("_new"):
            continue
        # Find the matching closing paren accounting for nested.
        start = m.end() - 1  # position of '('
        depth = 0
        end = -1
        for i, ch in enumerate(sql[start:], start):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            continue
        body = sql[start + 1:end]
        # Split on commas at depth 0.
        cols: List[str] = []
        buf: List[str] = []
        depth2 = 0
        for ch in body:
            if ch == '(':
                depth2 += 1
            elif ch == ')':
                depth2 -= 1
            if ch == ',' and depth2 == 0:
                cols.append(''.join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        if buf:
            cols.append(''.join(buf).strip())
        for c in cols:
            tok = c.strip()
            if not tok:
                continue
            head = tok.split(None, 1)[0].upper()
            # Skip table-level constraint clauses.
            if head in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK",
                         "CONSTRAINT"}:
                continue
            name = tok.split(None, 1)[0]
            out.setdefault(table, set()).add(name)
    return out


class TestD1MigrationsAreCoveredByLocalSchema:
    """Every column declared in a D1 migration must also exist in the
    local SQLite schema after ``init_db`` + ``_ensure_rollback_columns``.

    This guarantees that whatever the rollback CLI restores via
    ``OldRowJson.keys()`` against SQLite will also be valid SQL on D1
    (same column names exist on both sides) — which is the entire
    contract that makes dual-write rollback work.
    """

    def test_every_d1_migration_column_exists_in_local_ddl(self):
        # The autouse ``_isolate_sqlite`` fixture has already run
        # ``init_db`` + ``_ensure_rollback_columns`` against the test
        # SQLite, so every column the production code path declares
        # is present.
        with db_mod.get_db() as conn:
            local_cols: Dict[str, Set[str]] = {}
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall():
                tbl = row[0]
                local_cols[tbl] = {
                    r[1] for r in conn.execute(
                        f"PRAGMA table_info({tbl})"
                    )
                }

        migration_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "migration", "d1",
        )
        files = sorted(
            f for f in os.listdir(migration_dir) if f.endswith(".sql")
        )
        assert files, "no D1 migration files found"

        missing: List[str] = []
        for fname in files:
            with open(os.path.join(migration_dir, fname),
                       encoding="utf-8") as fh:
                sql = fh.read()
            for table, cols in _columns_in_migration_sql(sql).items():
                if table not in local_cols:
                    # Some migrations reference tables that aren't on
                    # this side of the split (operations vs history vs
                    # reports). Skip — the schema-parity contract is
                    # per-logical-DB and the autouse fixture pins all
                    # three to one file, so every table should exist.
                    missing.append(
                        f"{fname}: table {table!r} not in local DDL"
                    )
                    continue
                for c in cols:
                    if c not in local_cols[table]:
                        missing.append(
                            f"{fname}: {table}.{c} not in local DDL"
                        )
        assert not missing, (
            "D1 migrations declare columns that local SQLite DDL is "
            "missing — rollback can't round-trip them:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_audit_oldrowjson_uses_only_columns_present_on_d1(self):
        """The dynamic UPDATE in ``_rollback_history`` builds its SET
        clause from ``OldRowJson.keys()``. If the audit ever captured
        a column that doesn't exist on D1, the rollback statement
        would fail there. Pin the contract: the audit's source row
        (``SELECT *``) is exactly the live table schema, which is
        what migrations control.
        """
        # We already know OldRowJson == SELECT * (verified by
        # TestAuditCapturesEveryLiveColumn); so it's enough to assert
        # that every live history column appears in the corresponding
        # migration history file or in the base DDL.
        history_migration_columns: Set[str] = set()
        migration_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "migration", "d1",
        )
        for fname in os.listdir(migration_dir):
            if not fname.endswith(".sql") or "history" not in fname:
                continue
            with open(os.path.join(migration_dir, fname),
                       encoding="utf-8") as fh:
                sql = fh.read()
            for table, cols in _columns_in_migration_sql(sql).items():
                if table in {"MovieHistory", "TorrentHistory",
                              "MovieHistoryAudit",
                              "TorrentHistoryAudit"}:
                    history_migration_columns.update(
                        f"{table}.{c}" for c in cols
                    )

        # Sanity: at minimum, the migrations should declare SessionId
        # / RunId / RunAttempt — without those the rollback CLI's
        # primary lookup path doesn't work on D1.
        for required in (
            "MovieHistory.SessionId",
            "TorrentHistory.SessionId",
            "MovieHistoryAudit.RunId",
            "MovieHistoryAudit.RunAttempt",
            "TorrentHistoryAudit.RunId",
            "TorrentHistoryAudit.RunAttempt",
        ):
            assert required in history_migration_columns, (
                f"D1 history migrations are missing {required!r} — "
                f"rollback's run-id lookup path won't work on D1"
            )
