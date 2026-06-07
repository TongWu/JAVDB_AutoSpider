import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db import init_db
    init_db()


@pytest.fixture
def seeded_outcomes(_isolate_sqlite):
    """Seed AcquisitionOutcome rows; return the patched operations db_path."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO AcquisitionOutcome
                (qb_hash, href, video_code, category, state, queued_at, completed_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("h1", "/v/a", "AAA-001", "subtitle", "queued",
                 "2026-06-01T00:00:00.000000Z", None, "2026-06-01T00:00:00.000000Z"),
                ("h2", "/v/b", "BBB-002", "no_subtitle", "downloading",
                 "2026-06-02T00:00:00.000000Z", None, "2026-06-02T00:00:00.000000Z"),
                ("h3", "/v/c", "CCC-003", "subtitle", "completed",
                 "2026-06-03T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z"),
                ("h4", "/v/d", "DDD-004", "subtitle", "stalled",
                 "2026-05-30T00:00:00.000000Z", None, "2026-06-05T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    return db_path


def test_summary_counts_by_state(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/summary")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "queued": 1, "downloading": 1, "completed": 1,
        "stalled": 1, "failed": 0, "total": 4,
    }


@pytest.mark.usefixtures("_isolate_sqlite")
def test_summary_empty_table_is_all_zero(admin_client):
    r = admin_client.get("/api/library/acquisition/summary")
    assert r.status_code == 200
    assert r.json() == {
        "queued": 0, "downloading": 0, "completed": 0,
        "stalled": 0, "failed": 0, "total": 0,
    }


def test_recent_returns_rows_newest_first(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/recent")
    assert r.status_code == 200
    items = r.json()
    assert [i["qb_hash"] for i in items] == ["h3", "h2", "h1", "h4"]
    assert set(items[0].keys()) == {
        "qb_hash", "video_code", "href", "category", "state",
        "queued_at", "completed_at", "last_seen_at",
    }


def test_recent_state_filter(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/recent", params={"state": "completed"})
    assert r.status_code == 200
    items = r.json()
    assert [i["qb_hash"] for i in items] == ["h3"]


def test_recent_rejects_unknown_state(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/recent", params={"state": "bogus"})
    assert r.status_code == 400


def test_recent_accepts_in_library_state(admin_client, seeded_outcomes):
    # in_library is Phase-2-gated but a valid enum value — must not 400.
    r = admin_client.get("/api/library/acquisition/recent", params={"state": "in_library"})
    assert r.status_code == 200
    assert r.json() == []


def test_trend_groups_terminal_states_by_day(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/trend", params={"period": "90d"})
    assert r.status_code == 200
    points = r.json()
    by_date = {p["date"]: p for p in points}
    assert by_date["2026-06-04"]["completed"] == 1
    assert by_date["2026-06-05"]["stalled"] == 1
    assert "2026-06-02" not in by_date


def test_trend_rejects_bad_period(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/trend", params={"period": "5h"})
    assert r.status_code == 400


def test_endpoints_require_auth(anon_client):
    for path in (
        "/api/library/acquisition/summary",
        "/api/library/acquisition/recent",
        "/api/library/acquisition/trend",
    ):
        assert anon_client.get(path).status_code in (401, 403)


def test_summary_in_library_counts_toward_total_only(admin_client, _isolate_sqlite):
    # in_library is Phase-2-gated: rows count toward total (COUNT(*)) but appear
    # in no named KPI bucket. Pin this intentional divergence.
    with sqlite3.connect(_isolate_sqlite) as conn:
        conn.executemany(
            """
            INSERT INTO AcquisitionOutcome
                (qb_hash, href, video_code, category, state, queued_at, completed_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("c1", "/v/c1", "CCC-1", "subtitle", "completed",
                 "2026-06-03T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z"),
                ("il1", "/v/il1", "ILL-1", "subtitle", "in_library",
                 "2026-06-03T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    r = admin_client.get("/api/library/acquisition/summary")
    assert r.status_code == 200
    body = r.json()
    named_sum = (
        body["queued"] + body["downloading"] + body["completed"]
        + body["stalled"] + body["failed"]
    )
    assert body["completed"] == 1
    assert named_sum == 1
    assert body["total"] == 2  # in_library counts toward total, not a named bucket
