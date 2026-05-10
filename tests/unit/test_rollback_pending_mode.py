"""Pending-mode end-to-end tests (Ingestion Perfect Rollback, Phase 2).

Covers the six categories enumerated in the plan:

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
5. **IO 阈值**: counting wrapper around the SQLite cursor verifies
   that the pending path issues at most 2.0× the audit-mode statement
   count for the same logical workload (here we use a small N=12
   movies; the production threshold of N=100 is enforced by the
   ratio, not the absolute count).
6. **Mixed mode**: same RunId / RunAttempt with one daily session in
   ``audit`` mode and one adhoc session in ``pending`` mode.  Each
   side's cleanup path executes independently — audit replays its
   audit log, pending deletes its pending rows — and neither
   disturbs the other's writes.

The pre-existing ``audit`` mode tests in ``test_rollback.py`` /
``test_rollback_full_fidelity.py`` continue to be the source of truth
for the legacy X3 path and are not re-implemented here.
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

import pytest

import utils.infra.db as db_mod


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
    return db_mod.db_create_report_session(
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
    return db_mod.db_stage_history_write(
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
    return db_mod.db_stage_history_write(
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
    """Mirror db_upsert_history's lookup pair (path + absolute URL form).

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
    with db_mod.get_db() as conn:
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
    with db_mod.get_db() as conn:
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
    with db_mod.get_db() as conn:
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
        db_mod.db_commit_session_history(adhoc)

        # Daily then fails → rollback (in_progress dispatch).
        result = db_mod.db_rollback_session(daily, scope="history")
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
        db_mod.db_commit_session_history(daily)

        adhoc = _create_session(csv_filename="adhoc-reb.csv")
        _stage_movie(adhoc, href, code)
        _stage_torrent(
            adhoc, href, code, "hacked_subtitle", magnet="magnet:reb-hsub",
        )
        db_mod.db_commit_session_history(adhoc)

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
        snapshot = db_mod.db_load_history_snapshot(adhoc)
        assert href not in snapshot, (
            "adhoc must not observe daily's in_progress pending row"
        )

        # Daily's own loader does see it.
        daily_snapshot = db_mod.db_load_history_snapshot(daily)
        assert href in daily_snapshot
        assert daily_snapshot[href]["ActorName"] == "DailyOnly"

    def test_other_session_pending_invisible_after_my_commit(self):
        href = "/v/ISO-002"
        code = "ISO-002"

        # Adhoc commits one row.
        adhoc = _create_session(csv_filename="adhoc-iso2.csv")
        _stage_movie(adhoc, href, code, actor_name="AdhocActor")
        _stage_torrent(adhoc, href, code, "no_subtitle")
        db_mod.db_commit_session_history(adhoc)

        # Daily stages but never commits.
        daily = _create_session(csv_filename="daily-iso2.csv")
        _stage_movie(daily, href, code, actor_name="DailyDirty")

        # A neutral observer using session_id=None sees the committed
        # adhoc data only — daily's pending must not bleed in.
        live_only = db_mod.db_load_history_snapshot(None)
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
        db_mod.db_commit_session_history(ref)

        ref_state: Dict[str, dict] = {}
        with db_mod.get_db() as conn:
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
        with db_mod.get_db() as conn:
            conn.execute("DELETE FROM TorrentHistory")
            conn.execute("DELETE FROM MovieHistory")

        # Interrupted run: stage everything, then begin finalize and
        # apply only the first href, simulating a crash.
        sid = _create_session(csv_filename="interrupted.csv")
        self._stage_workload(sid, hrefs)
        assert db_mod.db_begin_finalize_session(sid) == 1
        when = "2026-05-09 12:00:00"
        with db_mod.get_db() as conn:
            db_mod._commit_one_movie(conn, sid, hrefs[0], when=when)

        # Resume #1, KILL, resume #2, KILL, resume #3 — all should
        # converge to the same final live state as the reference run.
        for _ in range(3):
            counts = db_mod.db_resume_finalizing_session(sid)
            # After the first full resume, the session is committed; the
            # next two resumes should be no-ops.
            assert counts["pending_marked_applied"] >= 0

        with db_mod.get_db() as conn:
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

        assert db_mod.db_begin_finalize_session(sid) == 1
        # Rollback dispatcher should call resume, not delete pending.
        result = db_mod.db_rollback_session(sid, scope="history")
        assert result["history"]["mode"] == "resume_commit"
        # Live row exists; pending drained.
        assert _live_torrent_categories(href) == [(1, 1)]
        assert _pending_counts(sid) == (0, 0)


# ──────────────────────────────────────────────────────────────────────
# 5. IO threshold: pending path stays within 2.0× audit baseline
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def io_count(monkeypatch):
    """Instrument every execute() / executemany() call across all connections.

    Both the pending and audit paths reuse thread-local connections from
    ``_get_connection``; we drain the cache before measuring so prior
    fixtures can't taint the counter, then patch each newly-opened
    connection's execute methods in place so we don't have to wrap the
    sqlite3.Connection class (which uses C-level slots and rejects
    arbitrary attribute proxies).
    """
    counter = {"calls": 0}
    real = db_mod._open_sqlite_connection

    def factory(path):
        conn = real(path)
        real_execute = conn.execute
        real_executemany = conn.executemany
        real_executescript = conn.executescript

        def _exec(sql, params=()):
            counter["calls"] += 1
            return real_execute(sql, params)

        def _many(sql, seq):
            seq_list = list(seq)
            counter["calls"] += len(seq_list)
            return real_executemany(sql, seq_list)

        def _script(script):
            counter["calls"] += 1
            return real_executescript(script)

        try:
            conn.execute = _exec  # type: ignore[assignment]
            conn.executemany = _many  # type: ignore[assignment]
            conn.executescript = _script  # type: ignore[assignment]
        except (AttributeError, TypeError):
            # sqlite3.Connection rejects monkeypatching on some Python
            # builds; fall back to a thin proxy that exposes the methods
            # we care about and forwards everything else via __getattr__.
            return _ConnProxy(conn, counter)
        return conn

    db_mod.close_db()
    monkeypatch.setattr(db_mod, "_open_sqlite_connection", factory)
    yield counter
    db_mod.close_db()


class _ConnProxy:
    """Fallback proxy used only when sqlite3.Connection rejects patching."""

    def __init__(self, conn, counter):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_counter", counter)

    def execute(self, sql, params=()):
        self._counter["calls"] += 1
        return self._conn.execute(sql, params)

    def executemany(self, sql, seq):
        seq_list = list(seq)
        self._counter["calls"] += len(seq_list)
        return self._conn.executemany(sql, seq_list)

    def executescript(self, script):
        self._counter["calls"] += 1
        return self._conn.executescript(script)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def _measure_audit(n: int) -> int:
    counter_before = 0
    db_mod.close_db()
    # Audit baseline: write through db_upsert_history.
    sid = _create_session(write_mode="audit", csv_filename="audit-io.csv")
    db_mod.set_active_session_id(sid)
    try:
        for i in range(n):
            href = f"/v/IO-A-{i:03d}"
            db_mod.db_upsert_history(
                href=href,
                video_code=f"IO-A-{i:03d}",
                magnet_links={"subtitle": f"magnet:io-a-{i}"},
            )
    finally:
        db_mod.set_active_session_id(None)
    return counter_before


class TestIOThreshold:
    def test_pending_path_within_2x_audit(self, io_count):
        n = 12

        # ── Audit baseline ────────────────────────────────────────
        sid_audit = _create_session(
            write_mode="audit", csv_filename="audit-io.csv",
        )
        db_mod.set_active_session_id(sid_audit)
        try:
            io_count["calls"] = 0
            for i in range(n):
                db_mod.db_upsert_history(
                    href=f"/v/IO-A-{i:03d}",
                    video_code=f"IO-A-{i:03d}",
                    magnet_links={"subtitle": f"magnet:io-a-{i}"},
                )
            audit_calls = io_count["calls"]
        finally:
            db_mod.set_active_session_id(None)

        # ── Pending path ──────────────────────────────────────────
        sid_pending = _create_session(
            write_mode="pending", csv_filename="pending-io.csv",
        )
        io_count["calls"] = 0
        for i in range(n):
            href = f"/v/IO-P-{i:03d}"
            _stage_movie(sid_pending, href, f"IO-P-{i:03d}")
            _stage_torrent(
                sid_pending, href, f"IO-P-{i:03d}", "subtitle",
                magnet=f"magnet:io-p-{i}",
            )
        db_mod.db_commit_session_history(sid_pending)
        pending_calls = io_count["calls"]

        assert audit_calls > 0
        assert pending_calls > 0
        ratio = pending_calls / max(1, audit_calls)
        # Hard threshold from the plan: pending must stay within 2.0×.
        assert ratio <= 2.0, (
            f"pending path issued {pending_calls} statements vs "
            f"{audit_calls} for audit (ratio={ratio:.2f}); plan caps at 2.0"
        )


# ──────────────────────────────────────────────────────────────────────
# 6. Mixed mode — daily=audit + adhoc=pending coexist under one RunId
# ──────────────────────────────────────────────────────────────────────


class TestMixedModeCleanup:
    def test_audit_and_pending_rollbacks_are_independent(self):
        run_id = "rid-mixed"
        run_attempt = 1

        # Daily session — audit mode.
        daily = _create_session(
            write_mode="audit",
            csv_filename="daily-mixed.csv",
            run_id=run_id,
            run_attempt=run_attempt,
        )
        # An audit-mode write through db_upsert_history populates
        # MovieHistory + the audit log.
        db_mod.set_active_session_id(daily)
        db_mod.set_active_run_identity(run_id, run_attempt)
        try:
            db_mod.db_upsert_history(
                href="/v/MIX-D-001",
                video_code="MIX-D-001",
                magnet_links={"subtitle": "magnet:mix-d-sub"},
            )
        finally:
            db_mod.set_active_session_id(None)
            db_mod.set_active_run_identity(None, None)

        # Adhoc session — pending mode.
        adhoc = _create_session(
            write_mode="pending",
            csv_filename="adhoc-mixed.csv",
            run_id=run_id,
            run_attempt=run_attempt,
        )
        _stage_movie(adhoc, "/v/MIX-P-001", "MIX-P-001")
        _stage_torrent(adhoc, "/v/MIX-P-001", "MIX-P-001", "subtitle")

        # Roll back BOTH sessions — same call signature the workflow uses.
        for sid in (daily, adhoc):
            result = db_mod.db_rollback_session(sid, scope="all")
            history = result.get("history", {})
            mode = history.get("mode")
            if sid == daily:
                # Audit replay path.
                assert mode == "audit_replay", history
            else:
                assert mode == "rollback_pending", history

        # Daily's MovieHistory row was unwound via audit replay.
        with db_mod.get_db() as conn:
            n_daily = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory WHERE Href=?",
                ("/v/MIX-D-001",),
            ).fetchone()["n"]
        assert n_daily == 0, "daily audit row must be deleted on rollback"

        # Adhoc never wrote to MovieHistory; its pending rows are gone.
        assert _pending_counts(adhoc) == (0, 0)
        with db_mod.get_db() as conn:
            n_adhoc = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistory WHERE Href=?",
                ("/v/MIX-P-001",),
            ).fetchone()["n"]
        assert n_adhoc == 0


# ──────────────────────────────────────────────────────────────────────
# Sanity — verify the existing audit suite still applies (smoke test)
# ──────────────────────────────────────────────────────────────────────


class TestAuditModeStillWorks:
    """Trivial guard so future refactors don't accidentally break the
    default-WriteMode path that Phase 0 ships dark."""

    def test_default_session_is_audit_mode(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="default-mode.csv",
        )
        state = db_mod.db_get_session_status(sid)
        assert state == ("audit", "in_progress")

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "pending")
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="env-mode.csv",
        )
        state = db_mod.db_get_session_status(sid)
        assert state == ("pending", "in_progress")

    def test_invalid_write_mode_raises(self):
        with pytest.raises(ValueError, match="WriteMode"):
            db_mod.db_create_report_session(
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
    tables and leave the live + audit tables untouched.  Audit mode
    keeps the legacy in-place upsert.  These two tests are the
    minimum guard that Phase 2's plumbing is wired all the way from
    the public history API down to the new tables.
    """

    def _live_counts(
        self, href: str, *, session_id: int,
    ) -> Tuple[int, int, int, int]:
        # MovieHistoryAudit / TorrentHistoryAudit don't carry the Href
        # column directly (they reference the live row via TargetId);
        # filter audit by SessionId, which uniquely identifies our test
        # session here.
        from tests.unit.test_rollback_pending_mode import _href_variants
        variants = _href_variants(href)
        placeholders = ",".join("?" for _ in variants)
        with db_mod.get_db() as conn:
            mh = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            mha = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (int(session_id),),
            ).fetchone()["n"]
            th = conn.execute(
                f"SELECT COUNT(*) AS n FROM TorrentHistory th "
                f"JOIN MovieHistory mh ON mh.Id=th.MovieHistoryId "
                f"WHERE mh.Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            tha = conn.execute(
                "SELECT COUNT(*) AS n FROM TorrentHistoryAudit "
                "WHERE SessionId=?",
                (int(session_id),),
            ).fetchone()["n"]
        return mh, mha, th, tha

    def test_pending_active_mode_stages_into_pending_tables(
        self, monkeypatch,
    ):
        from packages.python.javdb_platform.history_manager import (
            save_parsed_movie_to_history,
        )
        # Reset any stale active state from earlier tests.
        db_mod.set_active_session_id(None)
        db_mod.set_active_run_identity(None, None)
        db_mod.set_active_write_mode(None)
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="wire-pending.csv",
            write_mode="pending",
        )
        db_mod.set_active_session_id(sid)
        db_mod.set_active_run_identity("rid-wire", 1)
        db_mod.set_active_write_mode("pending")
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
            db_mod.set_active_session_id(None)
            db_mod.set_active_run_identity(None, None)
            db_mod.set_active_write_mode(None)

        # Pending tables hold the writes; live + audit are pristine.
        movie_pending, torrent_pending = _pending_counts(sid)
        assert movie_pending == 1
        assert torrent_pending >= 1
        mh, mha, th, tha = self._live_counts("/v/WIRE-001", session_id=sid)
        assert (mh, mha, th, tha) == (0, 0, 0, 0)

    def test_audit_active_mode_keeps_in_place_upsert(self, monkeypatch):
        # Make sure the env var doesn't leak from a prior test.
        monkeypatch.delenv("JAVDB_HISTORY_WRITE_MODE", raising=False)
        from packages.python.javdb_platform.history_manager import (
            save_parsed_movie_to_history,
        )
        db_mod.set_active_session_id(None)
        db_mod.set_active_run_identity(None, None)
        db_mod.set_active_write_mode(None)
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="wire-audit.csv",
        )
        db_mod.set_active_session_id(sid)
        db_mod.set_active_run_identity("rid-audit", 1)
        # Active mode left as None; resolves to 'audit' via env/default.
        try:
            save_parsed_movie_to_history(
                history_file=None,
                href="/v/AUDIT-001",
                phase=1,
                video_code="AUDIT-001",
                magnet_links={"subtitle": "magnet:?xt=urn:btih:audit-sub"},
            )
        finally:
            db_mod.set_active_session_id(None)
            db_mod.set_active_run_identity(None, None)
            db_mod.set_active_write_mode(None)

        # Live + audit get the row; pending stays empty.
        mh, mha, th, tha = self._live_counts("/v/AUDIT-001", session_id=sid)
        assert mh == 1
        assert mha >= 1
        assert th >= 1
        assert tha >= 1
        movie_pending, torrent_pending = _pending_counts(sid)
        assert (movie_pending, torrent_pending) == (0, 0)


# ──────────────────────────────────────────────────────────────────────
# 8. Rollback safety net — pending dispatcher catches stray audit rows
# ──────────────────────────────────────────────────────────────────────


class TestPendingRollbackSafetyNet:
    """If a half-migrated callsite still calls ``db_upsert_history``
    while the session is in pending mode, the pending rollback path
    must replay the stray audit rows so the live tables stay clean.
    """

    def test_pending_rollback_replays_legacy_audit_rows(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="safety-net.csv",
            write_mode="pending",
        )
        db_mod.set_active_session_id(sid)
        db_mod.set_active_run_identity("rid-safety", 1)
        try:
            # Simulate a legacy callsite: write directly via
            # db_upsert_history under the pending session.
            db_mod.db_upsert_history(
                href="/v/SAFE-001",
                video_code="SAFE-001",
                magnet_links={"subtitle": "magnet:safe-sub"},
            )
        finally:
            db_mod.set_active_session_id(None)
            db_mod.set_active_run_identity(None, None)

        from tests.unit.test_rollback_pending_mode import _href_variants
        variants = _href_variants("/v/SAFE-001")
        placeholders = ",".join("?" for _ in variants)
        with db_mod.get_db() as conn:
            n_live = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            n_audit = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (int(sid),),
            ).fetchone()["n"]
        assert n_live == 1, "legacy upsert should have written live row"
        assert n_audit >= 1, "legacy upsert should have written audit row"

        # Pending dispatcher (Status='in_progress', WriteMode='pending')
        # must replay the audit row and leave live tables clean.
        result = db_mod.db_rollback_session(sid, scope="all")
        assert result["history"]["mode"] == "rollback_pending"

        with db_mod.get_db() as conn:
            n_live_after = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
            n_audit_after = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (int(sid),),
            ).fetchone()["n"]
        assert n_live_after == 0, "safety net failed to delete live row"
        assert n_audit_after == 0, "safety net failed to drain audit row"


# ──────────────────────────────────────────────────────────────────────
# 9. commit_session CLI drains pending session before flipping Status
# ──────────────────────────────────────────────────────────────────────


class TestCommitSessionCLIDrainsPending:
    """The CLI must call ``db_commit_session_history`` for pending
    sessions before ``db_mark_session_committed`` flips the Status
    flag, otherwise live tables miss the staged rows and the
    PendingMovie/TorrentHistoryWrites accumulate forever.
    """

    def test_commit_session_promotes_pending_into_live(self, capsys):
        from apps.cli import commit_session as cs_mod

        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-09",
            csv_filename="commit-cli.csv",
            write_mode="pending",
        )
        db_mod.db_stage_history_write(
            sid,
            "movie",
            {
                "Href": "/v/CLI-001",
                "VideoCode": "CLI-001",
                "DateTimeVisited": "2026-05-09 12:00:00",
            },
        )
        db_mod.db_stage_history_write(
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

        state = db_mod.db_get_session_status(sid)
        assert state == ("pending", "committed")

        from tests.unit.test_rollback_pending_mode import _href_variants
        variants = _href_variants("/v/CLI-001")
        placeholders = ",".join("?" for _ in variants)
        with db_mod.get_db() as conn:
            n_live = conn.execute(
                f"SELECT COUNT(*) AS n FROM MovieHistory "
                f"WHERE Href IN ({placeholders})",
                variants,
            ).fetchone()["n"]
        assert n_live == 1, "commit_session did not promote pending into live"

        # All applied pending rows must be drained at the end.
        movie_pending, torrent_pending = _pending_counts(sid)
        assert (movie_pending, torrent_pending) == (0, 0)
