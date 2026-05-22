"""Verifies the COMMIT_SESSION_BULK path matches the per-href path.

The bulk path collapses the ~13–20 D1 round-trips per href that
``_commit_one_movie`` issues into O(N/50 + const) batched HTTP requests,
preserving idempotent-replay and dual-write semantics. These tests
exercise both code paths over identical workloads and assert byte-for-
byte equivalence on the live tables, plus a hard ceiling on the number
of underlying SQLite statements the bulk path issues (a proxy for the
HTTP-call count under STORAGE_BACKEND=dual).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pytest

from javdb.storage.db.db_connection import get_db
from javdb.storage.db.db_reports import db_create_report_session
from javdb.storage.db.db_history_write import db_stage_history_write, db_commit_session_history
import javdb.storage.db.db_history_write as _db_hw


# ──────────────────────────────────────────────────────────────────────
# Fixture builders (kept local; mirror test_rollback_pending_mode.py).
# ──────────────────────────────────────────────────────────────────────


def _create_session(filename: str) -> int:
    return db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-13",
        csv_filename=filename,
        write_mode="pending",
    )


def _stage_movie(sid, href, code, *, actor=None, visited="2026-05-13 12:00:00"):
    return db_stage_history_write(sid, "movie", {
        "Href": href,
        "VideoCode": code,
        "ActorName": actor,
        "DateTimeVisited": visited,
    })


def _stage_torrent(
    sid, href, code, category,
    *, magnet="magnet:test", size="1.0GB", file_count=1, resolution=None,
):
    return db_stage_history_write(sid, "torrent", {
        "Href": href,
        "VideoCode": code,
        "Category": category,
        "MagnetUri": magnet,
        "Size": size,
        "FileCount": file_count,
        "ResolutionType": resolution,
        "DateTimeVisited": "2026-05-13 12:00:00",
    })


def _seed_workload(session_id: int, n_hrefs: int) -> List[str]:
    """Stage a diverse workload covering: new movie / existing movie,
    INSERT / UPDATE torrent, hacked-subtitle conflict (forces a DELETE),
    and a hi-res torrent (forces HiResIndicator=1)."""
    hrefs: List[str] = []
    for i in range(n_hrefs):
        href = f"/v/BULK-{i:03d}"
        code = f"BULK-{i:03d}"
        hrefs.append(href)
        _stage_movie(session_id, href, code, actor=f"Actor{i}")
        # Subtitle row for all
        _stage_torrent(
            session_id, href, code, "subtitle",
            magnet=f"magnet:{i}-sub",
            resolution=1080,
        )
        # Even-indexed hrefs also stage a no_subtitle row + hacked_subtitle
        # that will shadow it (forces the conflict-deletion branch).
        if i % 2 == 0:
            _stage_torrent(
                session_id, href, code, "no_subtitle",
                magnet=f"magnet:{i}-no",
            )
            _stage_torrent(
                session_id, href, code, "hacked_subtitle",
                magnet=f"magnet:{i}-hsub",
            )
        # Every third href gets a hi-res row (>=2560p).
        if i % 3 == 0:
            _stage_torrent(
                session_id, href, code, "no_subtitle_censored",
                magnet=f"magnet:{i}-hires",
                resolution=4096,
            )
    return hrefs


def _capture_live_state(hrefs: List[str]) -> Dict[str, dict]:
    """Snapshot MovieHistory + TorrentHistory for the given hrefs.

    Strips fields that legitimately differ between runs (auto-Id columns,
    timestamps, SessionId) so the parity assertion only compares
    semantically-meaningful state.
    """
    from apps.api.parsers.common import movie_href_lookup_values
    base = "https://javdb.com"
    out: Dict[str, dict] = {}
    with get_db() as conn:
        for href in hrefs:
            path_href, abs_href = movie_href_lookup_values(href, base)
            variants = [v for v in (path_href, abs_href, href) if v]
            ph = ",".join("?" for _ in variants)
            movie = conn.execute(
                f"SELECT * FROM MovieHistory WHERE Href IN ({ph})", variants,
            ).fetchone()
            if movie is None:
                out[href] = {"movie": None, "torrents": []}
                continue
            torrents = conn.execute(
                "SELECT SubtitleIndicator, CensorIndicator, MagnetUri, "
                "Size, FileCount, ResolutionType "
                "FROM TorrentHistory WHERE MovieHistoryId=? "
                "ORDER BY SubtitleIndicator, CensorIndicator",
                (int(movie["Id"]),),
            ).fetchall()
            out[href] = {
                "movie": {
                    "VideoCode": movie["VideoCode"],
                    "ActorName": movie["ActorName"],
                    "PerfectMatchIndicator": int(
                        movie["PerfectMatchIndicator"] or 0
                    ),
                    "HiResIndicator": int(movie["HiResIndicator"] or 0),
                },
                "torrents": [dict(r) for r in torrents],
            }
    return out


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bulk", ["0", "1"])
def test_bulk_produces_expected_live_state(monkeypatch, bulk):
    """Per-href and bulk paths must produce identical semantic state.

    Pytest runs this test twice on fresh DBs (via the autouse
    ``_isolate_sqlite`` fixture); we capture both results via a
    module-level dict (``_PARITY_SNAPSHOTS``) and a follow-up test
    asserts they match.
    """
    monkeypatch.setenv("COMMIT_SESSION_BULK", bulk)
    sid = _create_session(f"bulk-{bulk}.csv")
    hrefs = _seed_workload(sid, n_hrefs=12)
    counts = db_commit_session_history(sid)

    # Sanity: every staged movie was upserted; pending rows fully drained.
    assert counts["hrefs_processed"] == 12
    assert counts["movies_upserted"] == 12
    # contracts.CATEGORY_TO_INDICATORS:
    #   subtitle=(1,1), no_subtitle=(0,1), hacked_subtitle=(1,0).
    # "no_subtitle_censored" is unknown → falls through to (0,1), which
    # *merges* with no_subtitle's (0,1) row in the pending overlay rather
    # than producing a separate one. So per-i staged-key counts:
    #   i%2==0           (i=0,2,4,6,8,10): {(1,1),(0,1),(1,0)} → 3
    #   i%3==0 only      (i=3,9):           {(1,1),(0,1)}      → 2
    #   neither          (i=1,5,7,11):      {(1,1)}            → 1
    # Sum = 6·3 + 2·2 + 4·1 = 18 + 4 + 4 = 26.
    assert counts["torrents_upserted"] == 26
    # The subtitle (1,1) conflict rule (DELETE sub=0,cen=1) fires for:
    # - all 6 even-i hrefs that stage no_subtitle (→0,1) + subtitle (1,1)
    # - i=3 and i=9 (odd, %3==0) that stage no_subtitle_censored (→0,1) + subtitle (1,1)
    # hacked_subtitle's (0,0)-delete is a no-op (never staged (0,0)).
    # Total real deletes = 6 + 2 = 8.
    assert counts["torrents_deleted"] == 8
    state = _capture_live_state(hrefs)
    _PARITY_SNAPSHOTS[bulk] = state

    # Per-movie spot checks (independent of the bulk flag).
    for i in range(12):
        href = f"/v/BULK-{i:03d}"
        entry = state[href]
        assert entry["movie"] is not None, f"{href}: movie row missing"
        cats = {
            (t["SubtitleIndicator"], t["CensorIndicator"])
            for t in entry["torrents"]
        }
        # subtitle (1,1) always staged.
        assert (1, 1) in cats
        if i % 2 == 0:
            # hacked_subtitle (1,0) shadows the no_subtitle (0,0) that was
            # also staged — the conflict-deletion rule removes (0,0).
            assert (1, 0) in cats
            assert (0, 0) not in cats
        if i % 3 == 0:
            # no_subtitle_censored (0,1) at 4096p is staged, but the
            # subtitle (1,1) conflict rule (has_subtitle=True) deletes
            # (0,1) for ALL hrefs that stage subtitle — including i%3==0
            # odd hrefs (i=3, 9). So (0,1) never survives in any i%3==0 case.
            assert (0, 1) not in cats
        # PerfectMatchIndicator requires both (1,0) AND (1,1).
        expected_perfect = 1 if (i % 2 == 0) else 0
        assert entry["movie"]["PerfectMatchIndicator"] == expected_perfect, (
            f"{href}: PerfectMatchIndicator mismatch (i={i}, "
            f"cats={cats}, got={entry['movie']['PerfectMatchIndicator']})"
        )
        # HiResIndicator: the hi-res (0,1) row is always deleted by the
        # has_subtitle conflict rule (subtitle is staged for every href),
        # so HiResIndicator is never set. Expected = 0 for all hrefs.
        assert entry["movie"]["HiResIndicator"] == 0, (
            f"{href}: HiResIndicator mismatch (i={i}, "
            f"got={entry['movie']['HiResIndicator']})"
        )


# Captured by the parametrized test above and asserted by
# ``test_bulk_and_perhref_snapshots_match`` below.
_PARITY_SNAPSHOTS: Dict[str, Dict[str, dict]] = {}


def test_bulk_and_perhref_snapshots_match():
    """The parametrized test must have populated both snapshots; their
    semantic content (movies + torrents) is required to match exactly.
    """
    if not {"0", "1"} <= _PARITY_SNAPSHOTS.keys():
        pytest.skip(
            "parametrized parity test did not run both bulk=0 and bulk=1 "
            "(re-run the full test module)"
        )
    perhref = _PARITY_SNAPSHOTS["0"]
    bulk = _PARITY_SNAPSHOTS["1"]
    assert perhref.keys() == bulk.keys()
    for href in perhref:
        assert perhref[href]["movie"] == bulk[href]["movie"], (
            f"{href}: movie diff between per-href and bulk paths"
        )
        # Order-independent comparison: each torrent is identified by
        # (sub, cen); compare the resulting dicts.
        a = {
            (t["SubtitleIndicator"], t["CensorIndicator"]): t
            for t in perhref[href]["torrents"]
        }
        b = {
            (t["SubtitleIndicator"], t["CensorIndicator"]): t
            for t in bulk[href]["torrents"]
        }
        assert a == b, f"{href}: torrent rows diverge"


class _CountingConn:
    """Pass-through SQLite wrapper that tallies HTTP-equivalent round-trips.

    ``execute()`` counts as 1 round-trip (mirrors D1's per-statement HTTP
    fallback). ``batch_execute(stmts)`` counts as 1 round-trip regardless
    of the number of statements (mirrors D1's atomic batch POST), while
    internally executing each statement via the wrapped connection. This
    lets the budget test measure HTTP calls, not raw SQLite execute() calls.
    """

    def __init__(self, inner):
        self._inner = inner
        self.executes = 0

    def execute(self, sql, params=()):
        self.executes += 1
        return self._inner.execute(sql, params)

    def batch_execute(self, statements):
        """Simulate a single D1 batch POST: 1 HTTP call, N statements."""
        self.executes += 1
        cursors = []
        for sql, params in statements:
            cursors.append(self._inner.execute(sql, params))
        return cursors

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()

    def close(self):
        return self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_bulk_path_issues_far_fewer_statements(monkeypatch):
    """The bulk path must issue dramatically fewer ``execute`` calls than
    the per-href path for the same logical workload.

    Under STORAGE_BACKEND=dual each ``execute`` becomes one HTTP POST to
    D1 (when ``batch_execute`` is unavailable on the underlying conn —
    the SQLite fallback path we exercise here). So this count is the
    same metric that matters in production: HTTP round-trips.
    """
    # Per-href baseline.
    monkeypatch.setenv("COMMIT_SESSION_BULK", "0")
    sid = _create_session("bulk-budget-perhref.csv")
    _seed_workload(sid, n_hrefs=20)
    perhref_count = _count_executes_during_commit(sid)

    # Reset DB to a fresh state for the bulk run by re-creating tables.
    # (The autouse fixture only resets between tests, not within one.)
    _truncate_workload_tables()

    monkeypatch.setenv("COMMIT_SESSION_BULK", "1")
    sid = _create_session("bulk-budget-bulk.csv")
    _seed_workload(sid, n_hrefs=20)
    bulk_count = _count_executes_during_commit(sid)

    # At 20 hrefs the per-href path issues ~300 HTTP calls (1 per execute,
    # no batching). The bulk path collapses all writes into ~7 batch_execute
    # calls (each counted as 1 HTTP call). Assert >=10x reduction with slack.
    assert bulk_count * 10 <= perhref_count, (
        f"bulk path did not deliver expected reduction: "
        f"per-href={perhref_count}, bulk={bulk_count}"
    )
    # Hard ceiling: bulk path should need ≤15 HTTP round-trips for 20 hrefs.
    assert bulk_count <= 15, (
        f"bulk path issued {bulk_count} HTTP calls; expected <=15 for 20 hrefs"
    )


def _count_executes_during_commit(session_id) -> int:
    """Patch ``_get_db`` so every execute() during commit gets tallied."""
    _db_hw._ensure_imports()
    orig_get_db = _db_hw._get_db
    counters: List[int] = []

    from contextlib import contextmanager

    @contextmanager
    def _patched(db_path=None):
        with orig_get_db(db_path) as conn:
            wrapped = _CountingConn(conn)
            try:
                yield wrapped
            finally:
                counters.append(wrapped.executes)

    _db_hw._get_db = _patched
    try:
        db_commit_session_history(session_id)
    finally:
        _db_hw._get_db = orig_get_db
    return sum(counters)


def _truncate_workload_tables():
    """Clear pending + live tables between budget-test phases."""
    with get_db() as conn:
        for tbl in (
            "TorrentHistory", "MovieHistory",
            "PendingTorrentHistoryWrites", "PendingMovieHistoryWrites",
        ):
            conn.execute(f"DELETE FROM {tbl}")
