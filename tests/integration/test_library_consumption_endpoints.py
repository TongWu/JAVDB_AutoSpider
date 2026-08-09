# tests/integration/test_library_consumption_endpoints.py
"""Integration tests for library consumption endpoints (ADR-034 FE-3)."""

import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db import init_db
    init_db()


@pytest.fixture
def seeded_consumption(_isolate_sqlite):
    """Seed ConsumptionSignal + UnresolvedMediaItem rows."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO ConsumptionSignal
                (video_code, source_type, instance, library_id, library_name,
                 watched, progress_pct, play_count, rating, watched_at,
                 resolved_confidence, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                # watched=1, has rating
                ("AAA-001", "emby", "emby-home", "lib-1", "Movies",
                 1, 100, 2, 8.5, "2026-06-01T20:00:00.000000Z", "high",
                 "2026-06-02T00:00:00.000000Z"),
                # watched=0
                ("BBB-002", "emby", "emby-home", "lib-1", "Movies",
                 0, 30, 0, None, None, "high",
                 "2026-06-03T00:00:00.000000Z"),
                # watched=None (unknown), different instance
                ("CCC-003", "plex", "plex-main", "lib-2", "Shows",
                 None, None, None, None, None, "low",
                 "2026-06-04T00:00:00.000000Z"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO UnresolvedMediaItem
                (instance, source_type, library_id, library_name,
                 item_id, raw_title, file_path, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("emby-home", "emby", "lib-1", "Movies",
                 "item-x", "Unknown.Title.2024.mkv", "/lib/Unknown.Title.2024.mkv",
                 "2026-06-05T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    return db_path


def test_summary_counts(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_signals"] == 3
    assert body["watched_count"] == 1
    assert body["unwatched_count"] == 1
    assert body["unique_titles"] == 3
    assert body["instance_count"] == 2
    assert body["unresolved_count"] == 1
    # avg_rating: only AAA-001 has rating=8.5 → AVG = 8.5
    assert body["avg_rating"] == pytest.approx(8.5)


def test_summary_avg_rating_null_when_no_ratings(admin_client, _isolate_sqlite):
    # Empty table → AVG(rating) = NULL → avg_rating field is None
    r = admin_client.get("/api/library/consumption/summary")
    assert r.status_code == 200
    assert r.json()["avg_rating"] is None


def test_summary_empty_table_zeros(admin_client, _isolate_sqlite):
    r = admin_client.get("/api/library/consumption/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_signals"] == 0
    assert body["unresolved_count"] == 0


def test_recent_returns_rows_newest_first(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 3
    assert items[0]["video_code"] == "CCC-003"  # newest observed_at
    assert set(items[0].keys()) == {
        "video_code", "source_type", "instance", "library_id", "library_name",
        "watched", "progress_pct", "play_count", "rating", "watched_at",
        "resolved_confidence", "observed_at",
    }


def test_recent_watched_true_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"watched": "true"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["watched"] is True for i in items)
    assert len(items) == 1


def test_recent_watched_false_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"watched": "false"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["watched"] is False for i in items)
    assert len(items) == 1


def test_recent_rejects_bad_watched(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"watched": "maybe"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "library.invalid_watched"


def test_recent_instance_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"instance": "plex-main"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["instance"] == "plex-main" for i in items)


def test_trend_groups_by_watched_at_day(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/trend", params={"period": "90d"})
    assert r.status_code == 200
    points = r.json()
    by_date = {p["date"]: p for p in points}
    # AAA-001 watched_at 2026-06-01 → 1 watched on that day
    assert by_date["2026-06-01"]["watched"] == 1
    assert by_date["2026-06-01"]["total_signals"] == 1
    # rows without watched_at (BBB-002, CCC-003) are excluded
    assert len(points) == 1


def test_trend_rejects_bad_period(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/trend", params={"period": "5h"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "library.invalid_period"


def test_unresolved_returns_items(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/unresolved")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["item_id"] == "item-x"
    assert set(items[0].keys()) == {
        "instance", "source_type", "library_id", "library_name",
        "item_id", "raw_title", "file_path", "observed_at",
    }


def test_unresolved_instance_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/unresolved", params={"instance": "plex-main"})
    assert r.status_code == 200
    assert r.json() == []  # no unresolved items for plex-main


def test_endpoints_require_auth(anon_client):
    for path in (
        "/api/library/consumption/summary",
        "/api/library/consumption/recent",
        "/api/library/consumption/trend",
        "/api/library/consumption/unresolved",
    ):
        assert anon_client.get(path).status_code in (401, 403)
