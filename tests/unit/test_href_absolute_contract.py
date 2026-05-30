"""Contract: public writers must persist absolute JavDB hrefs (BFR-010).

This drives the two production write paths with *deliberately site-relative*
input and asserts that nothing site-relative survives in any href-bearing
column of MovieHistory / MovieMetadata. If a future change drops the
absolutization in the stage layer or MetadataRepo, this test fails — locking
the BFR-010 invariant in place.
"""

import json
import pathlib
import sqlite3

import javdb.storage.db as _dbpkg
from javdb.storage.db import (
    db_create_report_session,
    db_stage_history_write,
    db_commit_session_history,
    get_db,
)
import javdb.storage.db._db_session as _db_session
from javdb.storage.repos.metadata_repo import MetadataRepo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_METADATA_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql"
).read_text(encoding="utf-8")


def _is_site_relative(value) -> bool:
    """True for a site-relative JavDB path like ``/v/x`` or ``/actors/y``.

    Absolute ``https://..`` URLs and external/empty values are fine.
    """
    return isinstance(value, str) and value.startswith('/')


class _Link:
    def __init__(self, name: str, href: str):
        self.name = name
        self.href = href


def _relative_links_in_json(payload):
    """Yield offending inner link/href values from a JSON object or array."""
    if not payload:
        return
    obj = json.loads(payload)
    items = obj if isinstance(obj, list) else [obj]
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ('link', 'href'):
            if _is_site_relative(item.get(key)):
                yield item[key]


def test_public_writers_never_persist_relative_hrefs():
    # --- MovieHistory: daily stage -> commit, with relative inputs ---
    sid = db_create_report_session(
        report_type="DailyReport",
        report_date="2026-01-01",
        csv_filename="contract.csv",
    )
    _db_session.set_active_session_id(sid)
    try:
        db_stage_history_write(
            sid,
            "movie",
            {
                "Href": "/v/rel1",
                "VideoCode": "REL-1",
                "ActorName": "Lead",
                "ActorGender": "female",
                "ActorLink": "/actors/rel-lead",
                "SupportingActors": json.dumps(
                    [{"name": "Sup", "gender": "male", "link": "/actors/rel-sup"}]
                ),
                "DateTimeVisited": "2026-01-01 00:00:00",
            },
        )
        db_commit_session_history(sid)
    finally:
        _db_session.set_active_session_id(None)

    # --- MovieMetadata: upsert with relative href + embedded links ---
    # MetadataRepo captures HISTORY_DB_PATH at import time, so target the
    # conftest-isolated DB explicitly (and create the ADR-022 table on it)
    # rather than touching the real reports/history.db.
    hist_path = _dbpkg.HISTORY_DB_PATH
    _c = sqlite3.connect(hist_path)
    _c.executescript(_METADATA_DDL)
    _c.commit()
    _c.close()
    MetadataRepo(db_path=hist_path).upsert(
        "/v/rel1",
        {
            'title': 'T', 'video_code': 'REL-1', 'release_date': '',
            'duration': '', 'rate': '', 'comment_count': '',
            'review_count': 0, 'want_count': 0, 'watched_count': 0,
            'maker': _Link('M', '/makers/rel'),
            'publisher': _Link('P', '/publishers/rel'),
            'series': _Link('S', '/series/rel'),
            'directors': [_Link('D', '/directors/rel')],
            'tags': [_Link('Tag', '/tags?c=1')],
            'poster_url': 'https://img.example/p.jpg',
            'fanart_urls': [], 'trailer_url': None,
        },
    )

    offenders = []
    with get_db() as conn:
        for r in conn.execute(
            "SELECT Href, ActorLink, SupportingActors FROM MovieHistory"
        ).fetchall():
            if _is_site_relative(r[0]):
                offenders.append(("MovieHistory.Href", r[0]))
            if _is_site_relative(r[1]):
                offenders.append(("MovieHistory.ActorLink", r[1]))
            for bad in _relative_links_in_json(r[2]):
                offenders.append(("MovieHistory.SupportingActors", bad))

        for r in conn.execute(
            "SELECT href, maker, publisher, series, directors, categories "
            "FROM MovieMetadata"
        ).fetchall():
            if _is_site_relative(r[0]):
                offenders.append(("MovieMetadata.href", r[0]))
            for col_idx, col_name in (
                (1, "maker"), (2, "publisher"), (3, "series"),
                (4, "directors"), (5, "categories"),
            ):
                for bad in _relative_links_in_json(r[col_idx]):
                    offenders.append((f"MovieMetadata.{col_name}", bad))

    assert not offenders, f"site-relative hrefs were persisted: {offenders}"
