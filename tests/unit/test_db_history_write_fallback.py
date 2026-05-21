"""Regression tests for history write category mapping."""

import javdb.storage.db.db as db_mod
from javdb.storage.repos.history_repo import HistoryRepo


def test_history_repo_stage_torrent_unknown_category_defaults_to_no_subtitle():
    repo = HistoryRepo()

    repo.stage_torrent(
        "sess-unknown-category",
        {
            "Href": "/v/UNKNOWN-CATEGORY",
            "VideoCode": "UNKNOWN-CATEGORY",
            "Category": "no_subtitle_censored",
            "MagnetUri": "magnet:?xt=urn:btih:unknown-category",
        },
    )

    with db_mod.get_db() as conn:
        row = conn.execute(
            "SELECT Category, SubtitleIndicator, CensorIndicator "
            "FROM PendingTorrentHistoryWrites WHERE SessionId=?",
            ("sess-unknown-category",),
        ).fetchone()

    assert row is not None
    assert row["Category"] == "no_subtitle_censored"
    assert (row["SubtitleIndicator"], row["CensorIndicator"]) == (0, 1)
