"""Regression tests for the pending-torrent overlay merge (P0-4).

Before the 2026-05 hardening pass, ``_pending_torrent_overlay`` kept
only the latest ``Seq`` per ``(Href, SubtitleIndicator, CensorIndicator)``
key, so when retries or re-fetches staged the same torrent twice
``db_commit_session_history`` would mark only the newest row as
``applied`` and leave the older rows stuck in ``pending``. The
Phase 3 health aggregator then misread that residue as a "critical
pending alert" and the auto-fallback step disabled pending mode for
the next 24h — a false positive triggered purely by retry duplication.

These tests pin the corrected behaviour:

* ``_pending_torrent_overlay`` now attaches a ``_merged_seqs`` list
  carrying *every* contributing pending Seq for that (href, sub, cen)
  triple, mirroring ``_pending_movie_overlay``.
* ``_commit_one_movie`` consumes the full list when transitioning
  ``ApplyState='pending' → 'applied'``.
"""

from __future__ import annotations

import pytest

import utils.infra.db as db_mod


@pytest.fixture
def staged_duplicate_torrent_session():
    """Stage two PendingTorrentHistoryWrites for the same (href, sub, cen).

    Returns ``(session_id, href, video_code, [seq_first, seq_second])``.
    """
    db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-09",
        csv_filename="overlay-merge-test.csv",
        write_mode="pending",
    )
    session_id = 777_001_001
    href = "/v/duplicate-stage-test"
    video_code = "DUPSTG-001"

    # First staging — initial best-effort scrape.
    seq_first = db_mod.db_stage_history_write(
        session_id, "torrent",
        {
            "Href": href,
            "VideoCode": video_code,
            "Category": "subtitle",
            "MagnetUri": "magnet:?xt=urn:btih:first",
            "Size": "1.0GB",
            "FileCount": 1,
            "DateTimeVisited": "2026-05-09 12:00:00",
        },
    )
    # Second staging — re-fetch / retry path lands a second row that
    # overlaps on (href, sub=0, cen=0) but carries a richer payload.
    seq_second = db_mod.db_stage_history_write(
        session_id, "torrent",
        {
            "Href": href,
            "VideoCode": video_code,
            "Category": "subtitle",
            "MagnetUri": "magnet:?xt=urn:btih:second",
            "Size": "1.5GB",
            "FileCount": 2,
            "DateTimeVisited": "2026-05-09 12:05:00",
        },
    )
    assert seq_second > seq_first
    return session_id, href, video_code, [seq_first, seq_second]


def test_pending_torrent_overlay_collects_merged_seqs(
    staged_duplicate_torrent_session,
):
    """Both Seqs land in ``_merged_seqs`` for the merged row."""
    session_id, href, _vc, seqs = staged_duplicate_torrent_session

    with db_mod.get_db() as conn:
        overlay = db_mod._pending_torrent_overlay(
            conn, session_id, href=href,
        )

    assert len(overlay) == 1, (
        "two rows that share (href, sub, cen) must merge into one overlay key"
    )
    payload = next(iter(overlay.values()))
    assert "_merged_seqs" in payload, (
        "merged overlay row missing _merged_seqs — P0-4 regression"
    )
    merged = sorted(payload["_merged_seqs"])
    assert merged == sorted(seqs), (
        f"_merged_seqs={merged!r} must contain all staged Seqs={seqs!r}"
    )
    # The richer second payload's non-NULL values shadow the first.
    assert payload["MagnetUri"] == "magnet:?xt=urn:btih:second"
    assert payload["FileCount"] == 2


def test_commit_one_movie_clears_all_merged_pending_rows(
    staged_duplicate_torrent_session,
):
    """``ApplyState='applied'`` lands on EVERY contributing Seq."""
    session_id, href, _vc, _seqs = staged_duplicate_torrent_session

    # First stage a movie row for the same href so _commit_one_movie has
    # somewhere to attach the torrent. Without this, the commit path
    # short-circuits because there's no live MovieHistory row to upsert
    # against.
    db_mod.db_stage_history_write(
        session_id, "movie",
        {
            "Href": href,
            "VideoCode": "DUPSTG-001",
            "DateTimeVisited": "2026-05-09 12:00:00",
        },
    )

    with db_mod.get_db() as conn:
        db_mod._commit_one_movie(
            conn, session_id, href, when="2026-05-09 12:30:00",
        )

    with db_mod.get_db() as conn:
        residual_pending = conn.execute(
            "SELECT COUNT(*) AS n FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState='pending'",
            (session_id,),
        ).fetchone()["n"]
        applied_count = conn.execute(
            "SELECT COUNT(*) AS n FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState='applied'",
            (session_id,),
        ).fetchone()["n"]

    # P0-4 regression check: without merged_seqs handling, residual was 1.
    assert residual_pending == 0, (
        f"P0-4 regression: {residual_pending} pending rows left after "
        "commit; _commit_one_movie must consume the full _merged_seqs list."
    )
    assert applied_count == 2, (
        f"expected both staged rows in applied state, got {applied_count}"
    )
