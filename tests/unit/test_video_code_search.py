"""Tests for the search-exact shared helper and the video-code search service."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import pytest

from api.parsers.index_parser import parse_index_page
from api.parsers.search_exact import find_exact_entry_first_search_page

HTML_DIR = os.path.join(project_root, 'html')


def _load_html(filename):
    path = os.path.join(HTML_DIR, filename)
    with open(path, encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# find_exact_entry_first_search_page
# ---------------------------------------------------------------------------

class TestFindExactEntryFirstSearchPage:
    def test_direct_match(self):
        html = _load_html('search_JAC-228.html')
        movies = parse_index_page(html, page_num=1).movies
        hit = find_exact_entry_first_search_page(movies, 'JAC-228')
        assert hit is not None
        assert hit.video_code == 'JAC-228'

    def test_no_match_returns_none(self):
        html = _load_html('search_JAC-228.html')
        movies = parse_index_page(html, page_num=1).movies
        assert find_exact_entry_first_search_page(movies, 'NONEXIST-999') is None

    def test_empty_list(self):
        assert find_exact_entry_first_search_page([], 'JAC-228') is None

    def test_letter_suffix_fallback_same_page(self):
        """Searching for 200GANA-3327 should match an entry with code GANA-3327."""
        fake_entry = SimpleNamespace(
            href='/v/abc', video_code='GANA-3327', title='',
            rate='', comment_count='', release_date='', tags=[],
            cover_url='', page=1, ranking=None,
        )
        result = find_exact_entry_first_search_page([fake_entry], '200GANA-3327')
        assert result is fake_entry

    def test_no_fallback_for_plain_code(self):
        fake_entry = SimpleNamespace(
            href='/v/x', video_code='OTHER-001', title='',
            rate='', comment_count='', release_date='', tags=[],
            cover_url='', page=1, ranking=None,
        )
        assert find_exact_entry_first_search_page([fake_entry], 'JAC-228') is None


# ---------------------------------------------------------------------------
# video_code_search_service.search_by_video_code
# ---------------------------------------------------------------------------

@pytest.fixture()
def _mock_config():
    with patch(
        'apps.api.services.video_code_search_service.config_service'
    ) as mock_cfg:
        mock_cfg.load_runtime_config.return_value = {
            'BASE_URL': 'https://javdb.com',
        }
        yield mock_cfg


def _run(coro):
    return asyncio.run(coro)


class TestSearchByVideoCodeService:
    def test_exact_match_annotated(self, _mock_config):
        html = _load_html('search_JAC-228.html')
        with patch(
            'apps.api.services.video_code_search_service._fetch_javdb_html',
            return_value=html,
        ):
            from apps.api.services.video_code_search_service import search_by_video_code
            result = _run(search_by_video_code('JAC-228'))

        assert result['video_code'] == 'JAC-228'
        assert result['exact_match_entry'] is not None
        assert result['exact_match_entry']['video_code'] == 'JAC-228'
        assert result['letter_suffix_fallback_searched'] is False
        exact_flags = [m['exact_match'] for m in result['movies']]
        assert any(exact_flags)
        assert sum(exact_flags) == 1

    def test_no_match_no_fallback(self, _mock_config):
        html = _load_html('search_JAC-228.html')
        with patch(
            'apps.api.services.video_code_search_service._fetch_javdb_html',
            return_value=html,
        ):
            from apps.api.services.video_code_search_service import search_by_video_code
            result = _run(search_by_video_code('NONEXIST-999'))

        assert result['exact_match_entry'] is None
        assert result['letter_suffix_fallback_searched'] is False
        assert all(not m['exact_match'] for m in result['movies'])

    def test_letter_suffix_fallback_triggers_second_search(self, _mock_config):
        primary_html = '<html><body><div class="movie-list"></div></body></html>'
        alt_html = _load_html('search_JAC-228.html')
        call_count = 0

        def _mock_fetch(url, *, use_proxy=True, use_cookie=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return primary_html
            return alt_html

        with patch(
            'apps.api.services.video_code_search_service._fetch_javdb_html',
            side_effect=_mock_fetch,
        ):
            from apps.api.services.video_code_search_service import search_by_video_code
            result = _run(search_by_video_code('200JAC-228'))

        assert result['letter_suffix_fallback_searched'] is True
        assert call_count == 2
        assert result['exact_match_entry'] is not None
        assert result['exact_match_entry']['video_code'] == 'JAC-228'
        assert all(not m['exact_match'] for m in result['movies'])

    def test_login_page_raises_403(self, _mock_config):
        login_html = '<html><head><title>登入</title></head><body></body></html>'
        with patch(
            'apps.api.services.video_code_search_service._fetch_javdb_html',
            return_value=login_html,
        ):
            from fastapi import HTTPException
            from apps.api.services.video_code_search_service import search_by_video_code
            with pytest.raises(HTTPException) as exc_info:
                _run(search_by_video_code('JAC-228'))
            assert exc_info.value.status_code == 403
