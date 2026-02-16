"""
Tests for api.models data classes.
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from dataclasses import asdict

from api.models import (
    MovieLink,
    MagnetInfo,
    MovieIndexEntry,
    MovieDetail,
    IndexPageResult,
    CategoryPageResult,
    TopPageResult,
)


class TestMovieLink:
    def test_creation(self):
        link = MovieLink(name='Sample Actor', href='/actors/xyz')
        assert link.name == 'Sample Actor'
        assert link.href == '/actors/xyz'

    def test_to_dict(self):
        link = MovieLink(name='Test', href='/test')
        d = link.to_dict()
        assert d == {'name': 'Test', 'href': '/test'}


class TestMagnetInfo:
    def test_creation_with_defaults(self):
        m = MagnetInfo(href='magnet:?xt=urn:btih:abc', name='test')
        assert m.href == 'magnet:?xt=urn:btih:abc'
        assert m.name == 'test'
        assert m.tags == []
        assert m.size == ''
        assert m.timestamp == ''

    def test_creation_with_all_fields(self):
        m = MagnetInfo(
            href='magnet:?xt=urn:btih:abc',
            name='ABC-123',
            tags=['字幕', 'HD'],
            size='4.94GB',
            timestamp='2024-01-15',
        )
        assert m.tags == ['字幕', 'HD']
        assert m.size == '4.94GB'

    def test_to_dict(self):
        m = MagnetInfo(href='magnet:?abc', name='test', tags=['HD'], size='1GB', timestamp='2024-01-01')
        d = m.to_dict()
        assert d['href'] == 'magnet:?abc'
        assert d['tags'] == ['HD']


class TestMovieIndexEntry:
    def test_creation_minimal(self):
        entry = MovieIndexEntry(href='/v/ABC-123', video_code='ABC-123')
        assert entry.href == '/v/ABC-123'
        assert entry.video_code == 'ABC-123'
        assert entry.title == ''
        assert entry.tags == []
        assert entry.ranking is None

    def test_creation_full(self):
        entry = MovieIndexEntry(
            href='/v/ABC-123',
            video_code='ABC-123',
            title='Test Movie Title',
            rate='4.47',
            comment_count='595',
            release_date='2024-01-15',
            tags=['含中字磁鏈', '今日新種'],
            cover_url='https://example.com/cover.jpg',
            page=1,
            ranking=5,
        )
        assert entry.title == 'Test Movie Title'
        assert entry.ranking == 5
        assert len(entry.tags) == 2

    def test_to_legacy_dict(self):
        entry = MovieIndexEntry(
            href='/v/ABC-123',
            video_code='ABC-123',
            rate='4.47',
            comment_count='595',
            page=2,
        )
        legacy = entry.to_legacy_dict()
        assert legacy == {
            'href': '/v/ABC-123',
            'video_code': 'ABC-123',
            'page': 2,
            'actor': '',
            'rate': '4.47',
            'comment_number': '595',
        }

    def test_to_dict(self):
        entry = MovieIndexEntry(href='/v/X', video_code='X-1', page=1)
        d = entry.to_dict()
        assert 'href' in d
        assert 'ranking' in d


class TestMovieDetail:
    def test_creation_defaults(self):
        detail = MovieDetail()
        assert detail.title == ''
        assert detail.actors == []
        assert detail.magnets == []
        assert detail.parse_success is True

    def test_get_first_actor_name_empty(self):
        detail = MovieDetail()
        assert detail.get_first_actor_name() == ''

    def test_get_first_actor_name(self):
        detail = MovieDetail(actors=[
            MovieLink(name='Actor One', href='/actors/1'),
            MovieLink(name='Actor Two', href='/actors/2'),
        ])
        assert detail.get_first_actor_name() == 'Actor One'

    def test_get_magnets_as_legacy(self):
        detail = MovieDetail(magnets=[
            MagnetInfo(href='magnet:?abc', name='test', tags=['HD'], size='1GB', timestamp='2024-01-01'),
        ])
        legacy = detail.get_magnets_as_legacy()
        assert len(legacy) == 1
        assert legacy[0]['href'] == 'magnet:?abc'
        assert legacy[0]['tags'] == ['HD']


class TestIndexPageResult:
    def test_defaults(self):
        r = IndexPageResult()
        assert r.has_movie_list is False
        assert r.movies == []
        assert r.page_title == ''


class TestCategoryPageResult:
    def test_inherits_index(self):
        r = CategoryPageResult(
            has_movie_list=True,
            category_type='makers',
            category_name='PRESTIGE',
        )
        assert r.has_movie_list is True
        assert r.category_type == 'makers'
        assert r.category_name == 'PRESTIGE'


class TestTopPageResult:
    def test_fields(self):
        r = TopPageResult(
            has_movie_list=True,
            top_type='top250',
            period='2025',
        )
        assert r.top_type == 'top250'
        assert r.period == '2025'
