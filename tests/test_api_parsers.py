"""
Tests for api.parsers – using both inline HTML and real HTML test files.
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pytest

from api.parsers.index_parser import parse_index_page, parse_category_page, parse_top_page
from api.parsers.detail_parser import parse_detail_page
from api.parsers.common import (
    extract_rate_and_comments,
    extract_video_code,
    detect_page_type,
    extract_category_name,
)
from api.models import MovieLink

HTML_DIR = os.path.join(project_root, 'html')


def _load_html(filename):
    """Load an HTML file from the html/ directory."""
    path = os.path.join(HTML_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f'HTML test file not found: {filename}')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ===================================================================
# Common utilities
# ===================================================================

class TestExtractRateAndComments:
    def test_normal(self):
        rate, count = extract_rate_and_comments('4.47分, 由595人評價')
        assert rate == '4.47'
        assert count == '595'

    def test_integer_rate(self):
        rate, count = extract_rate_and_comments('5分, 由10人評價')
        assert rate == '5'
        assert count == '10'

    def test_no_match(self):
        rate, count = extract_rate_and_comments('no data here')
        assert rate == ''
        assert count == ''

    def test_partial_rate_only(self):
        rate, count = extract_rate_and_comments('3.95分')
        assert rate == '3.95'
        assert count == ''


class TestExtractVideoCode:
    def test_with_strong_tag(self):
        from bs4 import BeautifulSoup
        html = '<a class="box"><div class="video-title"><strong>ABC-123</strong> Title</div></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a = soup.find('a')
        assert extract_video_code(a) == 'ABC-123'

    def test_invalid_no_dash(self):
        from bs4 import BeautifulSoup
        html = '<a class="box"><div class="video-title"><strong>NODASH</strong></div></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a = soup.find('a')
        assert extract_video_code(a) == ''

    def test_no_video_title(self):
        from bs4 import BeautifulSoup
        html = '<a class="box"><div>something</div></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a = soup.find('a')
        assert extract_video_code(a) == ''


class TestDetectPageType:
    def test_detail_page(self):
        html = '<!-- saved from url=(0026)https://javdb.com/v/1AWMKA --><div class="video-meta-panel"></div>'
        assert detect_page_type(html) == 'detail'

    def test_top250_page(self):
        html = '<!-- saved from url=(0038)https://javdb.com/rankings/top?t=y2025 -->'
        assert detect_page_type(html) == 'top250'

    def test_makers_page(self):
        html = '<!-- saved from url=(0040)https://javdb.com/makers/6M?f=download -->'
        assert detect_page_type(html) == 'makers'

    def test_index_fallback(self):
        html = '<html><div class="movie-list h cols-4"></div></html>'
        assert detect_page_type(html) == 'index'

    def test_unknown(self):
        assert detect_page_type('<html></html>') == 'unknown'


# ===================================================================
# Index parser – inline HTML
# ===================================================================

class TestParseIndexPageInline:
    def test_basic_parsing(self, sample_index_html):
        result = parse_index_page(sample_index_html, page_num=1)
        assert result.has_movie_list is True
        assert len(result.movies) == 3

    def test_movie_fields(self, sample_index_html):
        result = parse_index_page(sample_index_html, page_num=1)
        first = result.movies[0]
        assert first.href == '/v/ABC-123'
        assert first.video_code == 'ABC-123'
        assert first.rate == '4.47'
        assert first.comment_count == '595'
        assert first.page == 1
        assert '含中字磁鏈' in first.tags
        assert '今日新種' in first.tags

    def test_no_movie_list(self):
        html = '<html><body><div>no list here</div></body></html>'
        result = parse_index_page(html)
        assert result.has_movie_list is False
        assert result.movies == []

    def test_all_entries_returned_no_filtering(self, sample_index_html):
        """API parser returns ALL entries – no phase/filter logic."""
        result = parse_index_page(sample_index_html, page_num=1)
        codes = [m.video_code for m in result.movies]
        assert 'ABC-123' in codes
        assert 'DEF-456' in codes
        assert 'GHI-789' in codes


# ===================================================================
# Index parser – real HTML files
# ===================================================================

class TestParseIndexPageRealHTML:
    def test_normal_index_page(self):
        html = _load_html('JavDB-normal_index-page1.html')
        result = parse_index_page(html, page_num=1)
        assert result.has_movie_list is True
        assert len(result.movies) > 0

        # Check that movies have the expected enhanced fields
        first = result.movies[0]
        assert first.href != ''
        assert first.video_code != ''
        # Normal index page should have release dates for the main list
        # (but recommendation section at top may not)

    def test_normal_index_has_tags(self):
        html = _load_html('JavDB-normal_index-page1.html')
        result = parse_index_page(html, page_num=1)
        # At least some entries should have tags
        entries_with_tags = [m for m in result.movies if m.tags]
        assert len(entries_with_tags) > 0

    def test_normal_index_has_cover_urls(self):
        html = _load_html('JavDB-normal_index-page1.html')
        result = parse_index_page(html, page_num=1)
        entries_with_covers = [m for m in result.movies if m.cover_url]
        assert len(entries_with_covers) > 0

    def test_normal_index_has_release_dates(self):
        html = _load_html('JavDB-normal_index-page1.html')
        result = parse_index_page(html, page_num=1)
        entries_with_dates = [m for m in result.movies if m.release_date]
        assert len(entries_with_dates) > 0


class TestParseCategoryPageRealHTML:
    def test_maker_page(self):
        html = _load_html('maker_6M.html')
        result = parse_category_page(html)
        assert result.has_movie_list is True
        assert len(result.movies) > 0
        assert result.category_name != ''

    def test_publisher_page(self):
        html = _load_html('publisher_ O2ydO.html')
        result = parse_category_page(html)
        assert result.has_movie_list is True
        assert len(result.movies) > 0
        assert result.category_name != ''

    def test_series_page(self):
        html = _load_html('series_ KdqA.html')
        result = parse_category_page(html)
        assert result.has_movie_list is True
        assert len(result.movies) > 0

    def test_director_page(self):
        html = _load_html('director_前田文豪.html')
        result = parse_category_page(html)
        assert result.has_movie_list is True
        assert len(result.movies) > 0

    def test_video_codes_page(self):
        html = _load_html('video_codes_ABF.html')
        result = parse_category_page(html)
        assert result.has_movie_list is True
        assert len(result.movies) > 0


class TestParseTopPageRealHTML:
    def test_top250(self):
        html = _load_html('top250_2025.html')
        result = parse_top_page(html)
        assert result.has_movie_list is True
        assert result.top_type == 'top250'
        assert result.period == '2025'
        assert len(result.movies) > 0

    def test_top250_has_rankings(self):
        html = _load_html('top250_2025.html')
        result = parse_top_page(html)
        ranked = [m for m in result.movies if m.ranking is not None]
        assert len(ranked) > 0
        # First entry should be ranked #1
        assert result.movies[0].ranking == 1

    def test_top250_has_ratings(self):
        html = _load_html('top250_2025.html')
        result = parse_top_page(html)
        rated = [m for m in result.movies if m.rate]
        assert len(rated) > 0


# ===================================================================
# Detail parser – inline HTML
# ===================================================================

class TestParseDetailPageInline:
    def test_basic_parsing(self, sample_detail_html):
        detail = parse_detail_page(sample_detail_html)
        assert detail.parse_success is True
        assert len(detail.magnets) == 3

    def test_actor_extraction(self, sample_detail_html):
        detail = parse_detail_page(sample_detail_html)
        assert len(detail.actors) == 1
        assert detail.actors[0].name == 'Sample Actor'
        assert detail.actors[0].href == '/actors/xyz'

    def test_magnet_fields(self, sample_detail_html):
        detail = parse_detail_page(sample_detail_html)
        first = detail.magnets[0]
        assert first.href == 'magnet:?xt=urn:btih:abc123subtitle'
        assert first.name == 'ABC-123-subtitle.torrent'
        assert '字幕' in first.tags
        assert first.size == '4.94GB'
        assert first.timestamp == '2024-01-15'

    def test_no_magnets(self):
        html = '<html><body><div class="video-meta-panel"></div></body></html>'
        detail = parse_detail_page(html)
        assert detail.parse_success is False
        assert detail.magnets == []


# ===================================================================
# Detail parser – real HTML file
# ===================================================================

class TestParseDetailPageRealHTML:
    def test_vdd201_title(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert '女教師' in detail.title or '脅迫スイートルーム' in detail.title

    def test_vdd201_video_code(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.video_code == 'VDD-201'

    def test_vdd201_code_prefix_link(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert '/video_codes/VDD' in detail.code_prefix_link

    def test_vdd201_duration(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert '130' in detail.duration

    def test_vdd201_release_date(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.release_date == '2026-02-06'

    def test_vdd201_maker(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.maker is not None
        assert 'ドリームチケット' in detail.maker.name
        assert '/makers/' in detail.maker.href

    def test_vdd201_series(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.series is not None
        assert '脅迫スイートルーム' in detail.series.name

    def test_vdd201_directors(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert len(detail.directors) > 0
        assert detail.directors[0].name == '沢庵'

    def test_vdd201_tags(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert len(detail.tags) > 0
        tag_names = [t.name for t in detail.tags]
        assert '美乳' in tag_names
        assert '女教師' in tag_names

    def test_vdd201_rating(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.rate == '3.95'
        assert detail.comment_count == '191'

    def test_vdd201_poster(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.poster_url != ''
        assert '1AWMKA' in detail.poster_url

    def test_vdd201_fanart(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert len(detail.fanart_urls) > 0
        # All should be full-size sample image URLs
        for url in detail.fanart_urls:
            assert 'samples' in url or '1AWMKA' in url

    def test_vdd201_trailer(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        # Trailer should be detected (either URL or preview container)
        assert detail.trailer_url is not None

    def test_vdd201_actors(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert len(detail.actors) > 0
        assert '真北祈' in detail.actors[0].name or '真野祈' in detail.actors[0].name

    def test_vdd201_magnets(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.parse_success is True
        assert len(detail.magnets) > 0
        # Check first magnet has expected fields
        first = detail.magnets[0]
        assert first.href.startswith('magnet:')
        assert first.size != ''

    def test_vdd201_review_count(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.review_count == 4

    def test_vdd201_want_watched_counts(self):
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        assert detail.want_count == 1030
        assert detail.watched_count == 191

    def test_vdd201_backward_compat(self):
        """Verify the detail can be converted to the legacy format."""
        html = _load_html('detailed_page_VDD-201.html')
        detail = parse_detail_page(html)
        actor = detail.get_first_actor_name()
        assert actor != ''
        magnets = detail.get_magnets_as_legacy()
        assert isinstance(magnets, list)
        assert all(isinstance(m, dict) for m in magnets)
        assert all('href' in m for m in magnets)
