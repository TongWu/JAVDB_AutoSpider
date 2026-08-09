# tests/integration/test_library_ownership_endpoints.py
"""Integration tests for library ownership endpoints (ADR-034 FE-2)."""

import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db import init_db
    init_db()


@pytest.fixture
def seeded_ownership(_isolate_sqlite):
    """Seed OwnershipLedger rows; return the patched db path."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO OwnershipLedger
                (video_code, source, category, path, size, present, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("AAA-001", "qb",     "subtitle",    "/dl/AAA-001.mkv", 2_147_483_648, 1, "2026-06-01T00:00:00.000000Z"),
                ("BBB-002", "qb",     "no_subtitle", "/dl/BBB-002.mkv", 1_073_741_824, 1, "2026-06-02T00:00:00.000000Z"),
                ("CCC-003", "gdrive", "subtitle",    "/gd/CCC-003.mkv", 4_294_967_296, 1, "2026-06-03T00:00:00.000000Z"),
                # present=0 → swept, must NOT count toward total_owned_titles
                ("DDD-004", "nas",    "subtitle",    "/nas/DDD-004.mkv", 2_000_000_000, 0, "2026-05-30T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    return db_path


def test_summary_total_and_by_source(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/summary")
    assert r.status_code == 200
    body = r.json()
    # DDD-004 present=0 excluded → 3 distinct titles owned
    assert body["total_owned_titles"] == 3
    by_source = {s["source"]: s for s in body["by_source"]}
    assert by_source["qb"]["unique_titles"] == 2
    assert by_source["gdrive"]["unique_titles"] == 1
    # nas row is present=0 → not in by_source
    assert "nas" not in by_source


def test_summary_empty_table_is_zero(admin_client, _isolate_sqlite):
    r = admin_client.get("/api/library/ownership/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_owned_titles"] == 0
    assert body["by_source"] == []


def test_recent_returns_rows_newest_first(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/recent")
    assert r.status_code == 200
    items = r.json()
    # All 4 rows (including swept); ordered by observed_at DESC
    assert items[0]["video_code"] == "CCC-003"
    assert set(items[0].keys()) == {
        "video_code", "source", "category", "path", "size", "present", "observed_at",
    }


def test_recent_source_filter(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/recent", params={"source": "qb"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["source"] == "qb" for i in items)
    assert len(items) == 2


def test_recent_rejects_unknown_source(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/recent", params={"source": "unknown_src"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"]["code"] == "library.invalid_source"


def test_endpoints_require_auth(anon_client):
    for path in (
        "/api/library/ownership/summary",
        "/api/library/ownership/recent",
    ):
        assert anon_client.get(path).status_code in (401, 403)
