"""Unit tests for PreferenceRepo (ADR-022)."""

import json
import pathlib
import sqlite3

import pytest

from javdb.storage.repos.preference_repo import PreferenceRepo


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

# Reuse the canonical D1 migration DDL so the test schema carries the same
# CHECK constraints (content_type whitelist, hearted IN (0,1), rating range)
# and defaults as production — preventing fixture/schema drift.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RATINGS_PREFS_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_pref.db")
    conn = sqlite3.connect(path)
    conn.executescript(_RATINGS_PREFS_DDL)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# MovieRatings tests
# ---------------------------------------------------------------------------

class TestMovieRatings:

    def test_upsert_creates_row(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        row = repo.upsert_rating(
            href='/video/ABC-001', rating=4,
            tags=['quality_high', 'plot_good'], notes='Great',
        )
        assert row['rating'] == 4
        assert json.loads(row['tags']) == ['quality_high', 'plot_good']
        assert row['notes'] == 'Great'

    def test_upsert_overwrites_existing_row(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_rating(href='/video/ABC-002', rating=3, tags=[], notes=None)
        repo.upsert_rating(href='/video/ABC-002', rating=5, tags=['would_rewatch'], notes='Updated')
        row = repo.get_rating('/video/ABC-002')
        assert row['rating'] == 5
        assert json.loads(row['tags']) == ['would_rewatch']

    def test_upsert_allows_null_rating(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        row = repo.upsert_rating(href='/video/ABC-003', rating=None, tags=[], notes=None)
        assert row['rating'] is None

    def test_get_rating_returns_none_for_missing(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        assert repo.get_rating('/video/MISSING') is None

    def test_list_ratings_returns_all(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        for i in range(5):
            repo.upsert_rating(
                href=f'/video/X-{i:03d}', rating=i + 1, tags=[], notes=None
            )
        items, total = repo.list_ratings(limit=10, offset=0)
        assert total == 5
        assert len(items) == 5

    def test_list_ratings_pagination(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        for i in range(5):
            repo.upsert_rating(
                href=f'/video/Y-{i:03d}', rating=i + 1, tags=[], notes=None
            )
        items, total = repo.list_ratings(limit=3, offset=0)
        assert total == 5
        assert len(items) == 3

        page2, _ = repo.list_ratings(limit=3, offset=3)
        assert len(page2) == 2


# ---------------------------------------------------------------------------
# ContentPreferences tests
# ---------------------------------------------------------------------------

class TestContentPreferences:

    def test_upsert_creates_row(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        row = repo.upsert_preference(
            content_type='actor', content_id='/actors/EvkJ',
            content_name='Test Actor', hearted=True,
        )
        assert row['hearted'] == 1
        assert row['content_name'] == 'Test Actor'
        assert row['weight'] == pytest.approx(1.0)

    def test_upsert_overwrites_hearted_value(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(
            content_type='actor', content_id='/actors/X',
            content_name='X', hearted=True,
        )
        repo.upsert_preference(
            content_type='actor', content_id='/actors/X',
            content_name='X', hearted=False,
        )
        row = repo.get_preference('actor', '/actors/X')
        assert row['hearted'] == 0

    def test_is_actor_blocked_true_when_hearted_false(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(
            content_type='actor', content_id='/actors/BLOCKED',
            content_name='Blocked', hearted=False,
        )
        assert repo.is_actor_blocked('/actors/BLOCKED') is True

    def test_is_actor_blocked_false_when_hearted_true(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(
            content_type='actor', content_id='/actors/LIKED',
            content_name='Liked', hearted=True,
        )
        assert repo.is_actor_blocked('/actors/LIKED') is False

    def test_is_actor_blocked_false_when_no_record(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        assert repo.is_actor_blocked('/actors/UNKNOWN') is False

    def test_list_preferences_returns_all(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(content_type='actor', content_id='/actors/A', content_name='A', hearted=True)
        repo.upsert_preference(content_type='category', content_id='/tags?c=1', content_name='Cat1', hearted=False)
        items = repo.list_preferences()
        assert len(items) == 2

    def test_list_preferences_filter_by_content_type(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(content_type='actor', content_id='/actors/A', content_name='A', hearted=True)
        repo.upsert_preference(content_type='maker', content_id='/makers/M', content_name='M', hearted=True)
        items = repo.list_preferences(content_type='actor')
        assert len(items) == 1
        assert items[0]['content_type'] == 'actor'

    def test_list_preferences_hearted_only(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(content_type='actor', content_id='/actors/A', content_name='A', hearted=True)
        repo.upsert_preference(content_type='actor', content_id='/actors/B', content_name='B', hearted=False)
        items = repo.list_preferences(hearted_only=True)
        assert len(items) == 1
        assert items[0]['content_id'] == '/actors/A'
