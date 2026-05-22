"""Pending-mode end-to-end tests (Ingestion Perfect Rollback, Phase 2).

Covers the core pending-mode categories:

1. **Concurrent**: daily stages ``subtitle``, adhoc stages **and**
   commits ``hacked_subtitle``.  When daily later rolls back, live
   only carries the adhoc contribution and ``PerfectMatchIndicator``
   stays 0 (no "subtitle" row from daily that would have flipped it).
2. **Sequential rebase**: daily commits ``subtitle`` first, adhoc
   commits ``hacked_subtitle`` afterwards. Both torrents end up in
   live and ``PerfectMatchIndicator`` flips to 1.
3. **Dirty read**: ``db_load_history_snapshot(adhoc_session)`` is
   blind to daily's ``in_progress`` pending writes — the overlay
   only ever shows the caller's own session.
4. **finalizing 多次 resume**: simulate KILL after
   ``db_begin_finalize_session`` + a partial commit, resume; KILL
   again, resume; the live tables match a single uninterrupted
   ``db_commit_session_history`` byte-for-byte.
5. **Write-mode resolution**: pending is the only mode (ADR-005 PR-4
   retired audit mode); the resolver falls back gracefully.
6. **Spider write path**: ``save_parsed_movie_to_history`` stages into
   pending tables, never touching live until commit.
7. **Batch updates**: visit-timestamp and actor batch updates go through
   pending staging when an active pending session exists.
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

import pytest

from javdb.storage.db.db_connection import get_db
from javdb.storage.db.db_session import (
    set_active_session_id, set_active_run_identity, set_active_write_mode,
)
from javdb.storage.db.db_reports import (
    db_create_report_session, db_get_session_status, db_pending_session_stats,
    db_begin_finalize_session, db_finish_commit_session,
)
from javdb.storage.db.db_history_write import (
    db_stage_history_write, db_commit_session_history,
    db_batch_update_last_visited, db_batch_update_movie_actors,
    _commit_one_movie, db_resume_finalizing_session,
)
from javdb.storage.db.db_history_read import db_load_history_snapshot
from javdb.storage.db.db_rollback import db_rollback_session


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _create_session(
    *,
    write_mode: str = "pending",
    csv_filename: str = "pending-test.csv",
    run_id: str | None = None,
    run_attempt: int | None = None,
) -> int:
    return db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-09",
        csv_filename=csv_filename,
        write_mode=write_mode,
        run_id=run_id,
        run_attempt=run_attempt,
    )


def _stage_movie(
    session_id: int,
    href: str,
    video_code: str,
    *,
    actor_name: str | None = None,
) -> int:
    return db_stage_history_write(
        session_id,
        "movie",
        {
            "Href": href,
            "VideoCode": video_code,
            "ActorName": actor_name,
            "DateTimeVisited": "2026-05-09 12:00:00",
        },
    )


def _stage_torrent(
    session_id: int,
    href: str,
    video_code: str,
    category: str,
    *,
    magnet: str = "magnet:?xt=urn:btih:test",
    size: str = "1.0GB",
    file_count: int = 1,
    resolution_type: int | None = None,
) -> int:
    return db_stage_history_write(
        session_id,
        "torrent",
        {
            "Href": href,
            "VideoCode": video_code,
            "Category": category,
            "MagnetUri": magnet,
            "Size": size,
            "FileCount": file_count,
            "ResolutionType": resolution_type,
            "DateTimeVisited": "2026-05-09 12:00:00",
        },
    )


def _href_variants(href: str) -> List[str]:
    """Mirror the lookup pair (path + absolute URL form).

    The pending commit normalises Href to the absolute URL on INSERT so
    direct equality lookup by the raw path-style ``/v/...`` href misses
    every row.  Tests query by both variants to stay agnostic to whichever
    form the production code chose to persist.
    """
    from apps.api.parsers.common import movie_href_lookup_values
    base = "https://javdb.com"
    path_href, abs_href = movie_href_lookup_values(href, base)
    variants = [v for v in (path_href, abs_href, href) if v]
    # Preserve order, drop duplicates.
    seen = set()
    out: List[str] = []
    for v in variants:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _live_torrent_categories(href: str) -> List[Tuple[int, int]]:
    variants = _href_variants(href)
    placeholders = ",".join("?" for _ in variants)
    with get_db() as conn:
        movie = conn.execute(
            f"SELECT Id FROM MovieHistory WHERE Href IN ({placeholders})",
            variants,
        ).fetchone()
        if movie is None:
            return []
        rows = conn.execute(
            "SELECT SubtitleIndicator, CensorIndicator FROM TorrentHistory "
            "WHERE MovieHistoryId=? ORDER BY SubtitleIndicator, CensorIndicator",
            (movie["Id"],),
        ).fetchall()
    return [(r["SubtitleIndicator"], r["CensorIndicator"]) for r in rows]


def _live_movie_indicators(href: str) -> Tuple[int, int] | None:
    variants = _href_variants(href)
    placeholders = ",".join("?" for _ in variants)
    with get_db() as conn:
        row = conn.execute(
            "SELECT PerfectMatchIndicator, HiResIndicator "
            f"FROM MovieHistory WHERE Href IN ({placeholders})",
            variants,
        ).fetchone()
    if row is None:
        return None
    return (
        int(row["PerfectMatchIndicator"] or 0),
        int(row["HiResIndicator"] or 0),
    )


def _pending_counts(session_id: int) -> Tuple[int, int]:
    with get_db() as conn:
        m = conn.execute(
            "SELECT COUNT(*) AS n FROM PendingMovieHistoryWrites "
            "WHERE SessionId=?", (session_id,),
        ).fetchone()["n"]
        t = conn.execute(
            "SELECT COUNT(*) AS n FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=?", (session_id,),
        ).fetchone()["n"]
    return int(m), int(t)


# ──────────────────────────────────────────────────────────────────────
# 1. Concurrent: daily rollback after adhoc commit
# ──────────────────────────────────────────────────────────────────────


class TestConcurrentDailyRollbackAfterAdhocCommit:
    def test_daily_rollback_leaves_only_adhoc_contribution(self):
        href = "/v/CONC-001"
        code = "CONC-001"

        daily = _create_session(csv_filename="daily-conc.csv")
        adhoc = _create_session(csv_filename="adhoc-conc.csv")

        # Daily is mid-flight: stages the movie row + a 'subtitle' torrent
        # but never gets to commit.
        _stage_movie(daily, href, code, actor_name="DailyActor")
        _stage_torrent(daily, href, code, "subtitle", magnet="magnet:daily-sub")

        # Adhoc fully commits a 'hacked_subtitle' torrent.
        _stage_movie(adhoc, href, code, actor_name="AdhocActor")
        _stage_torrent(
            adhoc, href, code, "hacked_subtitle", magnet="magnet:adhoc-hsub",
        )
        db_commit_session_history(adhoc)

        # Daily then fails → rollback (in_progress dispatch).
        result = db_rollback_session(daily, scope="history")
        assert result["history"]["mode"] == "rollback_pending"
        assert result["history"]["PendingMovieHistoryWrites"] >= 1
        assert result["history"]["PendingTorrentHistoryWrites"] >= 1

        # Live should only carry the adhoc-contributed hacked_subtitle row.
        cats = _live_torrent_categories(href)
        assert (1, 0) in cats, (
            "adhoc-committed hacked_subtitle must be present after daily rollback"
        )
        assert (1, 1) not in cats, (
            "daily-staged subtitle row must NOT have leaked into live"
        )
        # PerfectMatchIndicator requires BOTH (1,0) and (1,1); the absent
        # subtitle row keeps it 0.
        assert _live_movie_indicators(href) == (0, 0)

        # No pending rows remain for daily; the adhoc session also has
        # nothing pending after commit drained them.
        assert _pending_counts(daily) == (0, 0)
        assert _pending_counts(adhoc) == (0, 0)


# ──────────────────────────────────────────────────────────────────────
# 2. Sequential rebase: daily commit → adhoc commit → both torrents land
# ──────────────────────────────────────────────────────────────────────


class TestSequentialRebase:
    def test_two_commits_recompute_perfect_match(self):
        href = "/v/REBA-001"
        code = "REBA-001"

        daily = _create_session(csv_filename="daily-reb.csv")
        _stage_movie(daily, href, code, actor_name="DailyActor")
        _stage_torrent(daily, href, code, "subtitle", magnet="magnet:reb-sub")
        db_commit_session_history(daily)

        adhoc = _create_session(csv_filename="adhoc-reb.csv")
        _stage_movie(adhoc, href, code)
        _stage_torrent(
            adhoc, href, code, "hacked_subtitle", magnet="magnet:reb-hsub",
        )
        db_commit_session_history(adhoc)

        cats = _live_torrent_categories(href)
        assert (1, 1) in cats
        assert (1, 0) in cats
        # Both subtitle + hacked_subtitle present → PerfectMatchIndicator=1.
        assert _live_movie_indicators(href) == (1, 0)


# ──────────────────────────────────────────────────────────────────────
# 3. Dirty read: pending overlay never crosses sessions
# ──────────────────────────────────────────────────────────────────────


class TestDirtyReadIsolation:
    def test_adhoc_loader_does_not_see_daily_pending(self):
        href = "/v/ISO-001"
        code = "ISO-001"

        daily = _create_session(csv_filename="daily-iso.csv")
        _stage_movie(daily, href, code, actor_name="DailyOnly")
        _stage_torrent(daily, href, code, "subtitle")

        adhoc = _create_session(csv_filename="adhoc-iso.csv")
        snapshot = db_load_history_snapshot(adhoc)
        assert href not in snapshot, (
            "adhoc must not observe daily's in_progress pending row"
        )

        # Daily's own loader does see it.
        daily_snapshot = db_load_history_snapshot(daily)
        assert href in daily_snapshot
        assert daily_snapshot[href]["ActorName"] == "DailyOnly"

    def test_other_session_pending_invisible_after_my_commit(self):
        href = "/v/ISO-002"
        code = "ISO-002"

        # Adhoc commits one row.
        adhoc = _create_session(csv_filename="adhoc-iso2.csv")
        _stage_movie(adhoc, href, code, actor_name="AdhocActor")
        _stage_torrent(adhoc, href, code, "no_subtitle")
        db_commit_session_history(adhoc)

        # Daily stages but never commits.
        daily = _create_session(csv_filename="daily-iso2.csv")
        _stage_movie(daily, href, code, actor_name="DailyDirty")

        # A neutral observer using session_id=None sees the committed
        # adhoc data only — daily's pending must not bleed in.
        live_only = db_load_history_snapshot(None)
        assert live_only[href]["ActorName"] == "AdhocActor"


# ──────────────────────────────────────────────────────────────────────
# 4. Finalizing — multiple resume cycles converge
# ──────────────────────────────────────────────────────────────────────


class TestFinalizingResumeIdempotency:
    def _stage_workload(self, sid: int, hrefs: List[str]) -> None:
        for i, href in enumerate(hrefs):
            code = f"FIN-{i:03d}"
            _stage_movie(sid, href, code, actor_name=f"Actor{i}")
            _stage_torrent(sid, href, code, "subtitle", magnet=f"magnet:{i}-sub")
            _stage_torrent(
                sid, href, code, "hacked_subtitle", magnet=f"magnet:{i}-hsub",
            )

    def test_finalizing_resume_three_times_matches_single_pass(self):
        hrefs = [f"/v/FIN-{i:03d}" for i in range(4)]

        # Reference run: a single uninterrupted commit.
        ref = _create_session(csv_filename="ref.csv")
        self._stage_workload(ref, hrefs)
        db_commit_session_history(ref)

        ref_state: Dict[str, dict] = {}
        with get_db() as conn:
            for href in hrefs:
                variants = _href_variants(href)
                placeholders = ",".join("?" for _ in variants)
                m = conn.execute(
                    f"SELECT * FROM MovieHistory WHERE Href IN ({placeholders})",
                    variants,
                ).fetchone()
                assert m is not None, f"reference run did not write {href}"
                ts = conn.execute(
                    "SELECT * FROM TorrentHistory WHERE MovieHistoryId=? "
                    "ORDER BY SubtitleIndicator, CensorIndicator",
                    (m["Id"],),
                ).fetchall()
                ref_state[href] = {
                    "movie": dict(m),
                    "torrents": [dict(t) for t in ts],
                }

        # Reset live tables so we can retry from scratch with the
        # interrupted session.
        with get_db() as conn:
            conn.execute("DELETE FROM TorrentHistory")
            conn.execute("DELETE FROM MovieHistory")

        # Interrupted run: stage everything, then begin finalize and
        # apply only the first href, simulating a crash.
        sid = _create_session(csv_filename="interrupted.csv")
        self._stage_workload(sid, hrefs)
        assert db_begin_finalize_session(sid) == 1
        when = "2026-05-09 12:00:00"
        with get_db() as conn:
            _commit_one_movie(conn, sid, hrefs[0], when=when)

        # Resume #1, KILL, resume #2, KILL, resume #3 — all should
        # converge to the same final live state as the reference run.
        for _ in range(3):
            counts = db_resume_finalizing_session(sid)
            # After the first full resume, the session is committed; the
            # next two resumes should be no-ops.
            assert counts["pending_marked_applied"] >= 0

        with get_db() as conn:
            for href in hrefs:
                variants = _href_variants(href)
                placeholders = ",".join("?" for _ in variants)
                m = conn.execute(
                    f"SELECT * FROM MovieHistory WHERE Href IN ({placeholders})",
                    variants,
                ).fetchone()
                assert m is not None
                ts = conn.execute(
                    "SELECT * FROM TorrentHistory WHERE MovieHistoryId=? "
                    "ORDER BY SubtitleIndicator, CensorIndicator",
                    (m["Id"],),
                ).fetchall()
                ref_movie = dict(ref_state[href]["movie"])
                live_movie = dict(m)
                # SessionId / DateTimeUpdated values differ between runs;
                # ignore those when comparing.  Everything else must be
                # byte-for-byte identical to the single-pass reference.
                for col in ("Id", "SessionId", "DateTimeCreated",
                            "DateTimeUpdated"):
                    ref_movie.pop(col, None)
                    live_movie.pop(col, None)
                assert live_movie == ref_movie

                # Same torrent rows, byte-for-byte (ignoring volatile
                # bookkeeping columns).
                ref_torrents = [dict(t) for t in ref_state[href]["torrents"]]
                live_torrents = [dict(t) for t in ts]
                for collection in (ref_torrents, live_torrents):
                    for r in collection:
                        for col in (
                            "Id", "MovieHistoryId", "SessionId",
                            "DateTimeCreated", "DateTimeUpdated",
                        ):
                            r.pop(col, None)
                assert live_torrents == ref_torrents

        # Pending tables must be drained for the interrupted session.
        assert _pending_counts(sid) == (0, 0)

    def test_resume_via_rollback_dispatch(self):
        sid = _create_session(csv_filename="dispatched.csv")
        href = "/v/DISP-001"
        _stage_movie(sid, href, "DISP-001")
        _stage_torrent(sid, href, "DISP-001", "subtitle")

        assert db_begin_finalize_session(sid) == 1
        # Rollback dispatcher should call resume, not delete pending.
        result = db_rollback_session(sid, scope="history")
        assert result["history"]["mode"] == "resume_commit"
        # Live row exists; pending drained.
        assert _live_torrent_categories(href) == [(1, 1)]
        assert _pending_counts(sid) == (0, 0)

    def test_commit_atomicity_crash_after_status_flip_recovers(self):
        """C1 regression — crash between Status='committed' flip and the
        final DELETE of applied pending rows must be recoverable.

        The fix in ``db_commit_session_history`` reordered the two steps
        so the flip lands FIRST.  A crash *between* them now leaves the
        monitoring-friendly state ``Status='committed'`` + residual
        ``ApplyState='applied'`` pending rows (the reverse order used
        to leave ``Status='finalizing'`` with zero pending rows — any
        "stuck session" alert misreads that as a hung commit).
        Resume must clean up the residual rows without re-running
        ``_commit_one_movie`` (which would risk regressing live data
        that another session updated in the meantime).
        """
        href = "/v/ATOM-001"
        sid = _create_session(csv_filename="atomicity-crash.csv")
        _stage_movie(sid, href, "ATOM-001", actor_name="AtomActor")
        _stage_torrent(sid, href, "ATOM-001", "subtitle", magnet="magnet:atom-sub")

        # Simulate the "crash between flip and DELETE" footprint by hand:
        #   1. Move into finalizing
        #   2. Apply each movie (marks rows ApplyState='applied')
        #   3. Flip Status to 'committed'  ← reordered step
        #   4. *Skip* the DELETE          ← simulated crash
        assert db_begin_finalize_session(sid) == 1
        when = "2026-05-09 12:00:00"
        with get_db() as conn:
            _commit_one_movie(conn, sid, href, when=when)
        db_finish_commit_session(sid)

        state = db_get_session_status(sid)
        assert state is not None and state[1] == "committed"

        stats_before = db_pending_session_stats(sid)
        assert stats_before["pending_applied_count"] > 0, (
            "expected leftover applied rows to simulate crash-after-flip"
        )
        assert stats_before["pending_residual_count"] == 0

        # Resume must clean up the residual applied rows without
        # re-running _commit_one_movie (live tables already correct).
        counts = db_resume_finalizing_session(sid)
        assert counts["pending_deleted"] >= 1

        stats_after = db_pending_session_stats(sid)
        assert stats_after["pending_applied_count"] == 0
        assert stats_after["pending_residual_count"] == 0
        assert _live_torrent_categories(href) == [(1, 1)]


# ──────────────────────────────────────────────────────────────────────
# 5. IO threshold: pending path stays within 2.0× audit baseline
# ──────────────────────────────────────────────────────────────────────





# ──────────────────────────────────────────────────────────────────────
# Sanity — verify the existing audit suite still applies (smoke test)
# ──────────────────────────────────────────────────────────────────────


class TestWriteModeResolution:
    """Trivial guard against future refactors silently flipping the
    resolved WriteMode. ADR-006 made 'pending' the default; audit
    mode is still reachable via the env var / explicit override."""

    def test_default_session_is_pending_mode(self, monkeypatch):
        # ADR-006: pending is the default per _resolve_write_mode().
        monkeypatch.delenv("JAVDB_HISTORY_WRITE_MODE", raising=False)
        set_active_write_mode(None)
        sid = db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="default-mode.csv",
        )
        state = db_get_session_status(sid)
        assert state == ("pending", "in_progress")

    def test_env_var_audit_falls_back_to_pending(self, monkeypatch):
        monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "audit")
        sid = db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="env-audit.csv",
        )
        state = db_get_session_status(sid)
        assert state == ("pending", "in_progress")

    def test_invalid_write_mode_raises(self):
        with pytest.raises(ValueError, match="WriteMode"):
            db_create_report_session(
                report_type="DailyReport",
                report_date="2026-05-09",
                csv_filename="bad-mode.csv",
                write_mode="banana",
            )


# ──────────────────────────────────────────────────────────────────────
# 7. Phase 2 wiring — spider write path actually goes pending
# ──────────────────────────────────────────────────────────────────────


class TestSpiderWritePathRoutesToPending:
    """Once ``set_active_write_mode('pending')`` is set,
    ``save_parsed_movie_to_history`` must stage rows into the pending
    tables and leave the live tables untouched.
    """

    def _live_counts(
        self, href: str,
    ) -> Tuple[int, int]:
        variants = _href_variants(href)
        placeholders = ",".join("?" for _ in variants)
        with get_db() as conn:
            mh = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            th = conn.execute(
                f"SELECT COUNT(*) AS n FROM TorrentHistory th "
                f"JOIN MovieHistory mh ON mh.Id=th.MovieHistoryId "
                f"WHERE mh.Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
        return mh, th

    def test_pending_active_mode_stages_into_pending_tables(
        self, monkeypatch,
    ):
        from javdb.storage.history_manager import (
            save_parsed_movie_to_history,
        )
        set_active_session_id(None)
        set_active_run_identity(None, None)
        set_active_write_mode(None)
        sid = db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="wire-pending.csv",
            write_mode="pending",
        )
        set_active_session_id(sid)
        set_active_run_identity("rid-wire", 1)
        set_active_write_mode("pending")
        try:
            save_parsed_movie_to_history(
                history_file=None,
                href="/v/WIRE-001",
                phase=1,
                video_code="WIRE-001",
                magnet_links={
                    "subtitle": "magnet:?xt=urn:btih:wire-sub",
                    "no_subtitle": "magnet:?xt=urn:btih:wire-nosub",
                },
                size_links={"subtitle": "1.0GB", "no_subtitle": "0.9GB"},
                file_count_links={"subtitle": 1, "no_subtitle": 1},
                resolution_links={"subtitle": None, "no_subtitle": None},
                actor_name="Wire Actor",
                actor_gender="female",
                actor_link="/actors/wire",
                supporting_actors=None,
            )
        finally:
            set_active_session_id(None)
            set_active_run_identity(None, None)
            set_active_write_mode(None)

        # Pending tables hold the writes; live tables are pristine.
        movie_pending, torrent_pending = _pending_counts(sid)
        assert movie_pending == 1
        assert torrent_pending >= 1
        mh, th = self._live_counts("/v/WIRE-001")
        assert (mh, th) == (0, 0)




# ──────────────────────────────────────────────────────────────────────
# 9. commit_session CLI drains pending session before flipping Status
# ──────────────────────────────────────────────────────────────────────


class TestCommitSessionCLIDrainsPending:
    """The CLI must call ``db_commit_session_history`` for pending
    sessions before ``db_mark_session_committed`` flips the Status
    flag, otherwise live tables miss the staged rows and the
    PendingMovie/TorrentHistoryWrites accumulate forever.
    """

    def test_commit_session_promotes_pending_into_live(
        self, capsys, monkeypatch, tmp_path,
    ):
        # Redirect REPORTS_DIR so the CLI's _emit_pending_verify writes
        # the test's pending_session_verify record into the tmp dir
        # rather than the git-tracked reports/D1/d1_drift.jsonl.
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
        from apps.cli.db import commit_session as cs_mod

        sid = db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="commit-cli.csv",
            write_mode="pending",
        )
        db_stage_history_write(
            sid,
            "movie",
            {
                "Href": "/v/CLI-001",
                "VideoCode": "CLI-001",
                "DateTimeVisited": "2026-05-09 12:00:00",
            },
        )
        db_stage_history_write(
            sid,
            "torrent",
            {
                "Href": "/v/CLI-001",
                "VideoCode": "CLI-001",
                "Category": "subtitle",
                "MagnetUri": "magnet:cli-sub",
                "Size": "1.0GB",
                "FileCount": 1,
                "DateTimeVisited": "2026-05-09 12:00:00",
            },
        )

        rc = cs_mod.main([
            "--session-id", str(sid),
            "--no-claim-commit",
            "--log-level", "WARNING",
        ])
        assert rc == 0, capsys.readouterr().err

        state = db_get_session_status(sid)
        assert state == ("pending", "committed")

        from tests.unit.test_rollback_pending_mode import _href_variants
        variants = _href_variants("/v/CLI-001")
        placeholders = ",".join("?" for _ in variants)
        with get_db() as conn:
            n_live = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
        assert n_live == 1, "commit_session did not promote pending into live"

        # All applied pending rows must be drained at the end.
        movie_pending, torrent_pending = _pending_counts(sid)
        assert (movie_pending, torrent_pending) == (0, 0)


# ──────────────────────────────────────────────────────────────────────
# 10. Batch-update side channels (visit timestamp + actors) go pending
# ──────────────────────────────────────────────────────────────────────


class TestBatchUpdatesRouteToPending:
    """``db_batch_update_last_visited`` and ``db_batch_update_movie_actors``
    are the other two history write paths the spider hits at the end of
    each phase.  Phase 2 must keep them off the live MovieHistory table
    when the active session is in pending mode, otherwise the dual-write
    log shows ``UPDATE MovieHistory SET DateTimeVisited=...`` mid-run
    and the "MovieHistory ever only holds committed state" invariant
    breaks.  Sparse stages from the same href must merge field-wise so
    a visit-only stage doesn't clobber the earlier actor stage's
    columns at commit time.
    """

    def _setup_pending_session(self) -> int:
        set_active_session_id(None)
        set_active_run_identity(None, None)
        set_active_write_mode(None)
        sid = db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="batch-pending.csv",
            write_mode="pending",
        )
        set_active_session_id(sid)
        set_active_run_identity("rid-batch", 1)
        set_active_write_mode("pending")
        return sid

    def _teardown(self):
        set_active_session_id(None)
        set_active_run_identity(None, None)
        set_active_write_mode(None)

    def test_visit_batch_stages_pending_only(self):
        sid = self._setup_pending_session()
        try:
            db_stage_history_write(
                sid,
                "movie",
                {
                    "Href": "/v/BAT-001",
                    "VideoCode": "BAT-001",
                    "ActorName": "Bat Actor",
                    "ActorGender": "female",
                    "ActorLink": "/actors/bat",
                    "DateTimeVisited": "2026-05-09 12:00:00",
                },
            )
            n = db_batch_update_last_visited(["/v/BAT-001"])
            assert n == 1
        finally:
            self._teardown()

        # Live tables must be untouched.
        variants = _href_variants("/v/BAT-001")
        placeholders = ",".join("?" for _ in variants)
        with get_db() as conn:
            n_live = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
        assert n_live == 0

        # Two pending movie rows: the actor stage + the visit stage.
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM PendingMovieHistoryWrites "
                "WHERE SessionId=? ORDER BY Seq ASC",
                (sid,),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["ActorName"] == "Bat Actor"
        assert rows[0]["DateTimeVisited"] == "2026-05-09 12:00:00"
        assert rows[1]["ActorName"] is None
        assert rows[1]["DateTimeVisited"] is not None

        # Commit must carry the actor field forward (sparse merge) and
        # the latest DateTimeVisited.
        db_commit_session_history(sid)
        with get_db() as conn:
            live = conn.execute(
                f"SELECT ActorName, DateTimeVisited FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()
            pending_left = conn.execute(
                "SELECT COUNT(*) AS n FROM PendingMovieHistoryWrites "
                "WHERE SessionId=? AND ApplyState='pending'",
                (sid,),
            ).fetchone()["n"]
        assert live is not None
        assert live["ActorName"] == "Bat Actor"
        assert live["DateTimeVisited"] == rows[1]["DateTimeVisited"]
        assert pending_left == 0, (
            "sparse-stage rows must all be drained on commit"
        )

    def test_actor_batch_stages_pending_only(self):
        sid = self._setup_pending_session()
        try:
            n = db_batch_update_movie_actors([
                ("/v/ACT-001", "Act Actor", "female", "/actors/act", None),
            ])
            assert n == 1
        finally:
            self._teardown()

        variants = _href_variants("/v/ACT-001")
        placeholders = ",".join("?" for _ in variants)
        with get_db() as conn:
            n_live = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            n_pending = conn.execute(
                "SELECT COUNT(*) AS n FROM PendingMovieHistoryWrites "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n_live == 0
        assert n_pending == 1

    def test_history_repo_actor_batch_preserves_pending_staging(self):
        """Repo caller migration must preserve db.py facade pending semantics."""
        from javdb.storage.repos.history_repo import HistoryRepo

        sid = self._setup_pending_session()
        try:
            n = HistoryRepo().batch_update_movie_actors([
                ("/v/R-ACT-001", "Repo Actor", "female", "/actors/repo", None),
            ])
            assert n == 1
        finally:
            self._teardown()

        variants = _href_variants("/v/R-ACT-001")
        placeholders = ",".join("?" for _ in variants)
        with get_db() as conn:
            n_live = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            n_pending = conn.execute(
                "SELECT COUNT(*) AS n FROM PendingMovieHistoryWrites "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n_live == 0
        assert n_pending == 1
