"""BFR-010 regression: actor links must be absolute in MovieHistory.

The daily path stages parser output (site-relative ``/actors/..`` paths) into
PendingMovieHistoryWrites and the commit copies it verbatim. Staging now
absolutizes ActorLink / SupportingActors so committed rows match the absolute
Href the commit path produces.
"""

import json

from javdb.storage.db import (
    db_create_report_session,
    db_stage_history_write,
    db_commit_session_history,
    get_db,
)
import javdb.storage.db._db_session as _db_session


def test_committed_actor_links_are_absolute():
    sid = db_create_report_session(
        report_type="DailyReport",
        report_date="2026-01-01",
        csv_filename="t.csv",
    )
    _db_session.set_active_session_id(sid)
    try:
        db_stage_history_write(
            sid,
            "movie",
            {
                "Href": "/v/abc",
                "VideoCode": "ABC-001",
                "ActorName": "Foo",
                "ActorGender": "female",
                "ActorLink": "/actors/xyz",
                "SupportingActors": json.dumps(
                    [{"name": "Bar", "gender": "male", "link": "/actors/qqq"}]
                ),
                "DateTimeVisited": "2026-01-01 00:00:00",
            },
        )
        db_commit_session_history(sid)
    finally:
        _db_session.set_active_session_id(None)

    with get_db() as conn:
        row = conn.execute(
            "SELECT Href, ActorLink, SupportingActors FROM MovieHistory "
            "WHERE VideoCode=?",
            ("ABC-001",),
        ).fetchone()

    assert row is not None
    href, actor_link, supporting = row[0], row[1], row[2]
    assert actor_link == "https://javdb.com/actors/xyz"
    assert json.loads(supporting)[0]["link"] == "https://javdb.com/actors/qqq"
    # Href absolutization (commit path) — guarded alongside the actor links.
    assert href == "https://javdb.com/v/abc"


def test_committed_absolute_actor_link_is_idempotent():
    """An already-absolute ActorLink must pass through unchanged."""
    sid = db_create_report_session(
        report_type="DailyReport",
        report_date="2026-01-01",
        csv_filename="t2.csv",
    )
    _db_session.set_active_session_id(sid)
    try:
        db_stage_history_write(
            sid,
            "movie",
            {
                "Href": "https://javdb.com/v/def",
                "VideoCode": "DEF-002",
                "ActorLink": "https://javdb.com/actors/keep",
                "DateTimeVisited": "2026-01-01 00:00:00",
            },
        )
        db_commit_session_history(sid)
    finally:
        _db_session.set_active_session_id(None)

    with get_db() as conn:
        row = conn.execute(
            "SELECT ActorLink FROM MovieHistory WHERE VideoCode=?", ("DEF-002",),
        ).fetchone()
    assert row is not None
    assert row[0] == "https://javdb.com/actors/keep"
