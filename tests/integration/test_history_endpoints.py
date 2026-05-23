"""Integration tests for history search and export endpoints (Phase 2, Task 1).

GET /api/history/movies        — search with pagination
GET /api/history/movies/export — stream CSV
GET /api/history/torrents      — search with pagination
GET /api/history/torrents/export — stream CSV
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db.db_migrations import init_db
    init_db()


@pytest.fixture
def seeded_history(_isolate_sqlite):
    """Seed MovieHistory + TorrentHistory rows and return db_path."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        m1 = conn.execute(
            """
            INSERT INTO MovieHistory
                (VideoCode, Href, ActorName, DateTimeCreated, PerfectMatchIndicator, HiResIndicator)
            VALUES ('TST-001', '/v/tst001', 'TestActor', '2026-01-01 00:00:00', 1, 0)
            """,
        ).lastrowid
        m2 = conn.execute(
            """
            INSERT INTO MovieHistory
                (VideoCode, Href, ActorName, DateTimeCreated, PerfectMatchIndicator, HiResIndicator)
            VALUES ('TST-002', '/v/tst002', 'OtherActor', '2026-01-02 00:00:00', 0, 1)
            """,
        ).lastrowid
        conn.execute(
            """
            INSERT INTO TorrentHistory
                (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator, ResolutionType,
                 Size, FileCount, DateTimeCreated)
            VALUES (?, 'magnet:?xt=urn:btih:test001', 1, 1, 2, '2.0GB', 1, '2026-01-01 00:00:00')
            """,
            (m1,),
        )
        conn.execute(
            """
            INSERT INTO TorrentHistory
                (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator, ResolutionType,
                 Size, FileCount, DateTimeCreated)
            VALUES (?, 'magnet:?xt=urn:btih:test002', 0, 1, 3, '4.0GB', 1, '2026-01-02 00:00:00')
            """,
            (m2,),
        )
    return db_path


# ── GET /api/history/movies ───────────────────────────────────────────────────


def test_movies_returns_200_with_items_and_cursor(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert "total_estimate" in body
    assert isinstance(body["items"], list)


def test_movies_requires_auth(anon_client):
    r = anon_client.get("/api/history/movies")
    assert r.status_code in (401, 403)


def test_movies_pagination_limit(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies", params={"limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is not None


def test_movies_cursor_advances(admin_client, seeded_history):
    r1 = admin_client.get("/api/history/movies", params={"limit": 1})
    assert r1.status_code == 200
    cursor = r1.json()["next_cursor"]
    assert cursor is not None

    r2 = admin_client.get("/api/history/movies", params={"limit": 1, "cursor": cursor})
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None  # last page


def test_movies_filter_by_q(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies", params={"q": "TST-001"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["video_code"] == "TST-001"


def test_movies_items_have_expected_fields(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies")
    assert r.status_code == 200
    item = r.json()["items"][0]
    for field in ("id", "video_code", "href", "perfect_match", "hi_res",
                  "datetime_created", "torrent_count"):
        assert field in item, f"missing field: {field}"


# ── GET /api/history/movies/export ───────────────────────────────────────────


def test_movies_export_returns_200_csv(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies/export")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "text/csv" in ct


def test_movies_export_has_content_disposition(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies/export")
    cd = r.headers.get("content-disposition", "")
    assert "movies.csv" in cd


def test_movies_export_contains_header(admin_client, seeded_history):
    r = admin_client.get("/api/history/movies/export")
    assert r.status_code == 200
    text = r.text
    assert "VideoCode" in text


def test_movies_export_requires_auth(anon_client):
    r = anon_client.get("/api/history/movies/export")
    assert r.status_code in (401, 403)


# ── GET /api/history/torrents ─────────────────────────────────────────────────


def test_torrents_returns_200_with_items_and_cursor(admin_client, seeded_history):
    r = admin_client.get("/api/history/torrents")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert "total_estimate" in body
    assert isinstance(body["items"], list)


def test_torrents_requires_auth(anon_client):
    r = anon_client.get("/api/history/torrents")
    assert r.status_code in (401, 403)


def test_torrents_items_have_expected_fields(admin_client, seeded_history):
    r = admin_client.get("/api/history/torrents")
    assert r.status_code == 200
    item = r.json()["items"][0]
    for field in ("id", "movie_video_code", "movie_href", "magnet_uri",
                  "subtitle_indicator", "censor_indicator", "resolution_type",
                  "file_count", "datetime_created"):
        assert field in item, f"missing field: {field}"


def test_torrents_filter_by_subtitle(admin_client, seeded_history):
    r = admin_client.get("/api/history/torrents", params={"has_subtitle": True})
    assert r.status_code == 200
    body = r.json()
    for item in body["items"]:
        assert item["subtitle_indicator"] == 1


# ── GET /api/history/torrents/export ─────────────────────────────────────────


def test_torrents_export_returns_200_csv(admin_client, seeded_history):
    r = admin_client.get("/api/history/torrents/export")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "text/csv" in ct


def test_torrents_export_has_content_disposition(admin_client, seeded_history):
    r = admin_client.get("/api/history/torrents/export")
    cd = r.headers.get("content-disposition", "")
    assert "torrents.csv" in cd


def test_torrents_export_contains_header(admin_client, seeded_history):
    r = admin_client.get("/api/history/torrents/export")
    assert r.status_code == 200
    text = r.text
    assert "MagnetUri" in text


def test_torrents_export_requires_auth(anon_client):
    r = anon_client.get("/api/history/torrents/export")
    assert r.status_code in (401, 403)


# ── 400 bad-cursor / bad-date error paths ────────────────────────────────────


def test_movies_bad_cursor_returns_400(admin_client):
    r = admin_client.get("/api/history/movies", params={"cursor": "!!!not-base64!!!"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"]["code"] == "history.invalid_cursor"


def test_movies_bad_date_from_returns_400(admin_client):
    r = admin_client.get("/api/history/movies", params={"date_from": "not-a-date"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"]["code"] == "history.invalid_date"


def test_torrents_bad_cursor_returns_400(admin_client):
    r = admin_client.get("/api/history/torrents", params={"cursor": "garbage!!!"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"]["code"] == "history.invalid_cursor"


def test_torrents_bad_date_to_returns_400(admin_client):
    r = admin_client.get("/api/history/torrents", params={"date_to": "2026/01/01"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"]["code"] == "history.invalid_date"
