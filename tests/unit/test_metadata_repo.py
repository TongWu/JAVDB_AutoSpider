"""Unit tests for MetadataRepo (ADR-022)."""

import json
import pathlib
import sqlite3

import pytest

from javdb.storage.repos.metadata_repo import MetadataRepo


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

# Reuse the canonical D1 migration DDL so the test schema (columns, NOT NULL
# defaults, and the idx_movie_metadata_video_code index) can never drift from
# production.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_METADATA_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_METADATA_DDL)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Link:
    def __init__(self, name: str, href: str):
        self.name = name
        self.href = href


def _minimal_detail(**overrides) -> dict:
    base = {
        'title': 'Test Movie',
        'video_code': 'TEST-001',
        'release_date': '2025-01-15',
        'duration': '120 分鍾',
        'rate': '4.2',
        'comment_count': '101',
        'review_count': 5,
        'want_count': 200,
        'watched_count': 800,
        'maker': _Link('TestMaker', '/makers/001'),
        'publisher': None,
        'series': None,
        'directors': [_Link('Director A', '/directors/abc')],
        'tags': [_Link('熟女', '/tags?c4=15')],
        'poster_url': 'https://example.com/cover.jpg',
        'fanart_urls': ['https://example.com/p1.jpg'],
        'trailer_url': None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetadataRepoUpsert:

    def test_upsert_stores_scalar_fields(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        assert row is not None
        assert row['title'] == 'Test Movie'
        assert row['video_code'] == 'TEST-001'
        assert row['release_date'] == '2025-01-15'
        assert row['duration_minutes'] == 120
        assert row['rate'] == pytest.approx(4.2)
        assert row['comment_count'] == 101
        assert row['review_count'] == 5
        assert row['want_count'] == 200
        assert row['watched_count'] == 800
        assert row['poster_url'] == 'https://example.com/cover.jpg'

    def test_upsert_serialises_maker_as_json(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        maker = json.loads(row['maker'])
        assert maker['name'] == 'TestMaker'
        assert maker['href'] == '/makers/001'

    def test_upsert_serialises_directors_as_json_array(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        directors = json.loads(row['directors'])
        assert len(directors) == 1
        assert directors[0]['href'] == '/directors/abc'

    def test_upsert_serialises_categories_from_tags_field(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        categories = json.loads(row['categories'])
        assert categories[0]['name'] == '熟女'

    def test_upsert_overwrites_on_conflict(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-002', _minimal_detail(
            video_code='TEST-002', title='Old Title', rate='3.0'
        ))
        repo.upsert('/video/TEST-002', _minimal_detail(
            video_code='TEST-002', title='New Title', rate='4.5'
        ))
        row = repo.get('/video/TEST-002')

        assert row['title'] == 'New Title'
        assert row['rate'] == pytest.approx(4.5)

    def test_upsert_null_optional_fields(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        detail = _minimal_detail(
            maker=None, publisher=None, series=None,
            directors=[], tags=[], fanart_urls=[], trailer_url=None,
        )
        repo.upsert('/video/TEST-003', detail)
        row = repo.get('/video/TEST-003')

        assert row['maker'] is None
        assert row['trailer_url'] is None


class TestMetadataRepoGet:

    def test_get_returns_none_for_missing_href(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        assert repo.get('/video/MISSING') is None

    def test_get_returns_dict(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-004', _minimal_detail(video_code='TEST-004'))
        row = repo.get('/video/TEST-004')
        assert isinstance(row, dict)
