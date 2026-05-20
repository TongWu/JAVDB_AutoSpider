"""Unit tests for HistoryRepo search and export methods (Phase 2, Task 1)."""
from __future__ import annotations

import base64
import csv
import io
import sqlite3

import pytest

from javdb.storage.repos.history_repo import HistoryRepo


# ── Seed helpers ─────────────────────────────────────────────────────────────


def _seed_movies(db_path: str, movies: list[dict]) -> list[int]:
    """Insert MovieHistory rows and return their Ids."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ids = []
    for m in movies:
        cur = conn.execute(
            """
            INSERT INTO MovieHistory
                (VideoCode, Href, ActorName, ActorGender, SupportingActors,
                 PerfectMatchIndicator, HiResIndicator, DateTimeCreated, SessionId)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                m["VideoCode"],
                m["Href"],
                m.get("ActorName"),
                m.get("ActorGender"),
                m.get("SupportingActors"),
                int(m.get("PerfectMatchIndicator", 0)),
                int(m.get("HiResIndicator", 0)),
                m.get("DateTimeCreated", "2026-01-01 00:00:00"),
                m.get("SessionId"),
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


def _seed_torrents(db_path: str, torrents: list[dict]) -> list[int]:
    """Insert TorrentHistory rows and return their Ids."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ids = []
    for t in torrents:
        cur = conn.execute(
            """
            INSERT INTO TorrentHistory
                (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                 ResolutionType, Size, FileCount, DateTimeCreated, SessionId)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t["MovieHistoryId"],
                t.get("MagnetUri", "magnet:?xt=urn:btih:aaa"),
                t.get("SubtitleIndicator", 0),
                t.get("CensorIndicator", 1),
                t.get("ResolutionType", 1),
                t.get("Size", "1.0GB"),
                t.get("FileCount", 1),
                t.get("DateTimeCreated", "2026-01-01 00:00:00"),
                t.get("SessionId"),
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded_db(_isolate_sqlite):
    """Return (db_path, movie_ids, torrent_ids) with a realistic seed."""
    db_path = _isolate_sqlite
    movie_rows = [
        {
            "VideoCode": "ABC-001",
            "Href": "/v/abc001",
            "ActorName": "Alice",
            "ActorGender": "female",
            "SupportingActors": None,
            "PerfectMatchIndicator": 1,
            "HiResIndicator": 0,
            "DateTimeCreated": "2026-01-01 10:00:00",
            "SessionId": "sess-A",
        },
        {
            "VideoCode": "ABC-002",
            "Href": "/v/abc002",
            "ActorName": "Bob",
            "ActorGender": "male",
            "SupportingActors": None,
            "PerfectMatchIndicator": 0,
            "HiResIndicator": 1,
            "DateTimeCreated": "2026-01-02 10:00:00",
            "SessionId": "sess-A",
        },
        {
            "VideoCode": "XYZ-001",
            "Href": "/v/xyz001",
            "ActorName": "Alice",
            "ActorGender": "female",
            "SupportingActors": None,
            "PerfectMatchIndicator": 0,
            "HiResIndicator": 0,
            "DateTimeCreated": "2026-01-03 10:00:00",
            "SessionId": "sess-B",
        },
    ]
    movie_ids = _seed_movies(db_path, movie_rows)

    torrent_rows = [
        {
            "MovieHistoryId": movie_ids[0],
            "MagnetUri": "magnet:?xt=urn:btih:aaa001",
            "SubtitleIndicator": 1,
            "CensorIndicator": 1,
            "ResolutionType": 2,
            "SessionId": "sess-A",
        },
        {
            "MovieHistoryId": movie_ids[1],
            "MagnetUri": "magnet:?xt=urn:btih:bbb001",
            "SubtitleIndicator": 0,
            "CensorIndicator": 1,
            "ResolutionType": 3,
            "SessionId": "sess-A",
        },
        {
            "MovieHistoryId": movie_ids[2],
            "MagnetUri": "magnet:?xt=urn:btih:ccc001",
            "SubtitleIndicator": 1,
            "CensorIndicator": 0,
            "ResolutionType": 1,
            "SessionId": "sess-B",
        },
    ]
    torrent_ids = _seed_torrents(db_path, torrent_rows)

    return db_path, movie_ids, torrent_ids


# ── search_movies ─────────────────────────────────────────────────────────────


class TestSearchMovies:

    def test_returns_all_when_no_filters(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, next_cursor, total = repo.search_movies()
        assert len(items) == 3
        assert next_cursor is None
        assert total == 3

    def test_search_by_video_code(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, next_cursor, total = repo.search_movies(q="ABC")
        codes = [i["VideoCode"] for i in items]
        assert all("ABC" in c for c in codes)
        assert len(items) == 2

    def test_search_by_actor(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, next_cursor, total = repo.search_movies(actor="Alice")
        assert len(items) == 2
        for item in items:
            assert item["ActorName"] == "Alice"

    def test_filter_perfect_match(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_movies(perfect_match=True)
        assert len(items) == 1
        assert items[0]["VideoCode"] == "ABC-001"

    def test_filter_hi_res(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_movies(hi_res=True)
        assert len(items) == 1
        assert items[0]["VideoCode"] == "ABC-002"

    def test_filter_session_id(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_movies(session_id="sess-B")
        assert len(items) == 1
        assert items[0]["VideoCode"] == "XYZ-001"

    def test_torrent_count_populated(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_movies()
        # Each movie has exactly 1 torrent in our seed
        for item in items:
            assert item["torrent_count"] == 1

    def test_cursor_pagination_advances(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)

        # First page: limit=2 → should get 2 items and a next_cursor
        page1, cursor1, total1 = repo.search_movies(limit=2)
        assert len(page1) == 2
        assert cursor1 is not None
        assert total1 == 3

        # Second page: use cursor from first page
        page2, cursor2, total2 = repo.search_movies(limit=2, cursor=cursor1)
        assert len(page2) == 1  # 3 total - 2 on page 1 = 1 remaining
        assert cursor2 is None  # last page

    def test_second_page_ids_greater_than_first_page(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)

        page1, cursor1, _ = repo.search_movies(limit=2)
        page2, _, _ = repo.search_movies(limit=2, cursor=cursor1)

        first_page_ids = [i["Id"] for i in page1]
        second_page_ids = [i["Id"] for i in page2]
        assert all(sid > max(first_page_ids) for sid in second_page_ids)

    def test_cursor_is_base64_encoded_id(self, seeded_db):
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        page1, cursor1, _ = repo.search_movies(limit=2)
        # cursor should be decodable to an integer (the last Id on page 1)
        decoded = int(base64.b64decode(cursor1).decode())
        assert decoded == page1[-1]["Id"]

    def test_date_from_filter_iso_datetime(self, seeded_db):
        """ISO 8601 datetime with T and Z is normalized and filters correctly."""
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        # abc001 is 2026-01-01 10:00:00; abc002 and xyz001 are later
        items, _, _ = repo.search_movies(date_from="2026-01-02T00:00:00Z")
        assert len(items) == 2  # abc002 and xyz001

    def test_date_to_filter_iso_datetime(self, seeded_db):
        """ISO 8601 datetime with T and Z is normalized and filters correctly."""
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_movies(date_to="2026-01-01T23:59:59Z")
        assert len(items) == 1  # only abc001

    def test_date_from_filter_date_only(self, seeded_db):
        """Date-only date_from expands to 00:00:00 of that day."""
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_movies(date_from="2026-01-02")
        assert len(items) == 2  # abc002 (2026-01-02) and xyz001 (2026-01-03)

    def test_date_to_filter_date_only_inclusive(self, seeded_db):
        """Date-only date_to expands to 23:59:59, making that day inclusive."""
        db_path, movie_ids, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        # abc001 is 2026-01-01 10:00:00; date_to="2026-01-01" should include it
        items, _, _ = repo.search_movies(date_to="2026-01-01")
        assert len(items) == 1  # only abc001 (abc002 is 2026-01-02)


# ── search_torrents ───────────────────────────────────────────────────────────


class TestSearchTorrents:

    def test_returns_all_when_no_filters(self, seeded_db):
        db_path, _, torrent_ids = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, next_cursor, total = repo.search_torrents()
        assert len(items) == 3
        assert next_cursor is None
        assert total == 3

    def test_joined_movie_video_code(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_torrents()
        # All items must carry a movie_video_code from the join
        for item in items:
            assert "movie_video_code" in item
            assert item["movie_video_code"] is not None

    def test_filter_by_subtitle(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_torrents(has_subtitle=True)
        for item in items:
            assert item["SubtitleIndicator"] == 1

    def test_filter_by_uncensored(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        # uncensored means CensorIndicator == 0
        items, _, _ = repo.search_torrents(uncensored=True)
        for item in items:
            assert item["CensorIndicator"] == 0

    def test_filter_by_resolution_type(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_torrents(resolution_type=2)
        assert len(items) == 1
        assert items[0]["ResolutionType"] == 2

    def test_filter_by_session_id(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_torrents(session_id="sess-B")
        assert len(items) == 1

    def test_cursor_pagination(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        page1, cursor1, total = repo.search_torrents(limit=2)
        assert len(page1) == 2
        assert cursor1 is not None
        assert total == 3

        page2, cursor2, _ = repo.search_torrents(limit=2, cursor=cursor1)
        assert len(page2) == 1
        assert cursor2 is None

    def test_second_page_ids_greater(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        page1, cursor1, _ = repo.search_torrents(limit=2)
        page2, _, _ = repo.search_torrents(limit=2, cursor=cursor1)
        first_ids = [i["Id"] for i in page1]
        second_ids = [i["Id"] for i in page2]
        assert all(sid > max(first_ids) for sid in second_ids)

    def test_q_filter_searches_movie_video_code(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        items, _, _ = repo.search_torrents(q="ABC")
        # Only ABC-001 and ABC-002 match
        assert len(items) == 2
        for item in items:
            assert "ABC" in item["movie_video_code"]


# ── export_movies_csv ─────────────────────────────────────────────────────────


class TestExportMoviesCsv:

    def test_header_row_present(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_movies_csv())
        assert len(rows) >= 2  # header + at least 1 data row
        header = rows[0]
        # Should be a CSV string with recognizable column names
        assert "VideoCode" in header
        assert "ActorName" in header

    def test_more_than_one_row(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_movies_csv())
        # 1 header + 3 data rows
        assert len(rows) > 1
        data_rows = rows[1:]
        assert len(data_rows) == 3

    def test_filter_by_q_reduces_rows(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_movies_csv(q="ABC"))
        data_rows = rows[1:]
        assert len(data_rows) == 2

    def test_rows_parseable_as_csv(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_movies_csv())
        reader = csv.reader(io.StringIO("".join(rows)))
        parsed = list(reader)
        assert len(parsed) == 4  # header + 3 data rows


# ── export_torrents_csv ───────────────────────────────────────────────────────


class TestExportTorrentsCsv:

    def test_header_row_present(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_torrents_csv())
        assert len(rows) >= 2
        header = rows[0]
        assert "MagnetUri" in header
        assert "movie_video_code" in header

    def test_more_than_one_row(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_torrents_csv())
        data_rows = rows[1:]
        assert len(data_rows) == 3

    def test_rows_parseable_as_csv(self, seeded_db):
        db_path, _, _ = seeded_db
        repo = HistoryRepo(db_path=db_path)
        rows = list(repo.export_torrents_csv())
        reader = csv.reader(io.StringIO("".join(rows)))
        parsed = list(reader)
        assert len(parsed) == 4  # header + 3 data rows
