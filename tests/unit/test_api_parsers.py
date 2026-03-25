"""
Tests for api.parsers – using both inline HTML and real HTML test files.
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import pytest

from api.parsers.index_parser import (
    parse_index_page,
    parse_category_page,
    parse_top_page,
    find_exact_video_code_match,
)
from api.parsers.detail_parser import parse_detail_page
from api.parsers.common import (
    extract_rate_and_comments,
    extract_video_code,
    detect_page_type,
    extract_category_name,
)
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

    def test_english_format(self):
        rate, count = extract_rate_and_comments('4.2, by 101 users')
        assert rate == '4.2'
        assert count == '101'

    def test_english_partial_rate_only(self):
        rate, count = extract_rate_and_comments('4.2, by')
        assert rate == '4.2'
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

    def test_search_page(self):
        html = '<!-- saved from url=(0038)https://javdb.com/search?q=JAC-228&f=all -->'
        assert detect_page_type(html) == 'search'


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


class TestParseSearchPageRealHTML:
    def test_search_page_parses_entries(self):
        html = _load_html('search_JAC-228.html')
        result = parse_index_page(html, page_num=1)
        assert result.has_movie_list is True
        assert len(result.movies) > 0

    def test_search_page_exact_video_code_match(self):
        html = _load_html('search_JAC-228.html')
        result = parse_index_page(html, page_num=1)
        matched = find_exact_video_code_match(result.movies, 'JAC-228')
        assert matched is not None
        assert matched.video_code == 'JAC-228'

    def test_search_page_no_exact_match(self):
        html = _load_html('search_JAC-228.html')
        result = parse_index_page(html, page_num=1)
        matched = find_exact_video_code_match(result.movies, 'JAC-999')
        assert matched is None


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
        assert detail.actors[0].gender == 'female'

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

    def test_actors_panel_na_placeholder(self):
        from api.models import NO_ACTOR_LISTING_ACTOR_NAME, NO_ACTOR_LISTING_ACTOR_GENDER

        html = '''
        <html><body>
        <div class="video-meta-panel">
          <div class="panel-block">
            <strong>演員:</strong>
            &nbsp;<span class="value">
                N/A
            </span>
          </div>
        </div>
        <div id="magnets-content">
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:naactortest">
                        <span class="name">TEST-1.torrent</span>
                        <span class="meta">1GB, 1個文件</span>
                    </a>
                </div>
                <span class="time">2024-01-01</span>
            </div>
        </div>
        </body></html>
        '''
        detail = parse_detail_page(html)
        assert detail.parse_success is True
        assert detail.actors == []
        assert detail.no_actor_listing is True
        assert detail.get_first_actor_name() == NO_ACTOR_LISTING_ACTOR_NAME
        assert detail.get_first_actor_gender() == NO_ACTOR_LISTING_ACTOR_GENDER
        assert detail.get_first_actor_href() == ''
        assert detail.get_supporting_actors_json() == '[]'

    def test_single_actor_supporting_json_is_empty_array(self):
        html = '''
        <html><body>
        <div class="video-meta-panel">
          <div class="panel-block">
            <strong>演員:</strong>
            &nbsp;<span class="value">
                <a href="/actors/solo">Solo Star</a>
                <strong class="symbol female">♀</strong>
            </span>
          </div>
        </div>
        <div id="magnets-content">
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:solotest">
                        <span class="name">SOLO-1.torrent</span>
                        <span class="meta">1GB, 1個文件</span>
                    </a>
                </div>
                <span class="time">2024-01-01</span>
            </div>
        </div>
        </body></html>
        '''
        detail = parse_detail_page(html)
        assert detail.parse_success is True
        assert len(detail.actors) == 1
        assert detail.get_supporting_actors_json() == '[]'


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
        assert len(detail.actors) >= 4
        assert '真北祈' in detail.actors[0].name or '真野祈' in detail.actors[0].name
        assert detail.actors[0].gender == 'female'
        assert detail.actors[1].gender == 'male'
        sup_json = detail.get_supporting_actors_json()
        assert 'マッスル澤野' in sup_json

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


# ===================================================================
# Detail parser – AVSW-067 Traditional Chinese HTML
# ===================================================================

class TestParseDetailPageAVSW067ZH:
    """Parse AVSW-067 detail page in Traditional Chinese."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.detail = parse_detail_page(_load_html('detail_page_AVSW-067.html'))

    def test_parse_success(self):
        assert self.detail.parse_success is True

    def test_title(self):
        assert '田中ねね' in self.detail.title
        assert 'SPECIAL BEST' in self.detail.title

    def test_video_code(self):
        assert self.detail.video_code == 'AVSW-067'

    def test_code_prefix_link(self):
        assert self.detail.code_prefix_link == '/video_codes/AVSW'

    def test_release_date(self):
        assert self.detail.release_date == '2025-10-28'

    def test_duration(self):
        assert '304' in self.detail.duration
        assert '分鍾' in self.detail.duration

    def test_maker(self):
        assert self.detail.maker is not None
        assert self.detail.maker.name == 'AVS'
        assert '/makers/' in self.detail.maker.href

    def test_series(self):
        assert self.detail.series is not None
        assert '○○の世界' in self.detail.series.name
        assert '/series/' in self.detail.series.href

    def test_rating(self):
        assert self.detail.rate == '4.2'
        assert self.detail.comment_count == '101'

    def test_tags(self):
        tag_names = [t.name for t in self.detail.tags]
        assert '巨乳' in tag_names
        assert len(self.detail.tags) == 6

    def test_actors(self):
        assert len(self.detail.actors) == 1
        assert self.detail.actors[0].name == '田中ねね'
        assert self.detail.actors[0].href == '/actors/d78g'
        assert self.detail.actors[0].gender == 'female'

    def test_no_actor_listing_false(self):
        assert self.detail.no_actor_listing is False

    def test_magnets(self):
        assert len(self.detail.magnets) == 4
        first = self.detail.magnets[0]
        assert first.href.startswith('magnet:')
        assert first.size == '12.79GB'
        assert first.file_count == 5

    def test_poster(self):
        assert 'a8z5ar' in self.detail.poster_url

    def test_fanart(self):
        assert len(self.detail.fanart_urls) == 11

    def test_trailer(self):
        assert self.detail.trailer_url is not None

    def test_review_count(self):
        assert self.detail.review_count == 2

    def test_want_watched_counts(self):
        assert self.detail.want_count == 455
        assert self.detail.watched_count == 101


# ===================================================================
# Detail parser – AVSW-067 English HTML
# ===================================================================

class TestParseDetailPageAVSW067EN:
    """Parse AVSW-067 detail page in English – validates locale-independent parsing."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.detail = parse_detail_page(_load_html('detail_page_AVSW-067_EN.html'))

    def test_parse_success(self):
        assert self.detail.parse_success is True

    def test_title(self):
        assert '田中ねね' in self.detail.title
        assert 'SPECIAL BEST' in self.detail.title

    def test_video_code(self):
        assert self.detail.video_code == 'AVSW-067'

    def test_code_prefix_link(self):
        assert self.detail.code_prefix_link == '/video_codes/AVSW'

    def test_release_date(self):
        assert self.detail.release_date == '2025-10-28'

    def test_duration(self):
        assert '304' in self.detail.duration
        assert 'minute' in self.detail.duration

    def test_maker(self):
        assert self.detail.maker is not None
        assert self.detail.maker.name == 'AVS'
        assert '/makers/' in self.detail.maker.href

    def test_series(self):
        assert self.detail.series is not None
        assert '○○の世界' in self.detail.series.name
        assert '/series/' in self.detail.series.href

    def test_rating(self):
        assert self.detail.rate == '4.2'
        assert self.detail.comment_count == '101'

    def test_tags(self):
        tag_names = [t.name for t in self.detail.tags]
        assert 'Big Tits' in tag_names
        assert len(self.detail.tags) == 6

    def test_actors(self):
        assert len(self.detail.actors) == 1
        assert self.detail.actors[0].name == '田中ねね'
        assert self.detail.actors[0].href == '/actors/d78g'
        assert self.detail.actors[0].gender == 'female'

    def test_no_actor_listing_false(self):
        assert self.detail.no_actor_listing is False

    def test_magnets(self):
        assert len(self.detail.magnets) == 4
        first = self.detail.magnets[0]
        assert first.href.startswith('magnet:')
        assert first.size == '12.79GB'
        assert first.file_count == 5

    def test_poster(self):
        assert 'a8z5ar' in self.detail.poster_url

    def test_fanart(self):
        assert len(self.detail.fanart_urls) == 11

    def test_trailer(self):
        assert self.detail.trailer_url is not None

    def test_review_count(self):
        assert self.detail.review_count == 2

    def test_want_watched_counts(self):
        assert self.detail.want_count == 455
        assert self.detail.watched_count == 101

    def test_same_core_data_as_zh(self):
        """English and Traditional Chinese pages should yield identical core data."""
        zh = parse_detail_page(_load_html('detail_page_AVSW-067.html'))
        assert self.detail.video_code == zh.video_code
        assert self.detail.release_date == zh.release_date
        assert self.detail.rate == zh.rate
        assert self.detail.comment_count == zh.comment_count
        assert self.detail.maker.name == zh.maker.name
        assert self.detail.series.name == zh.series.name
        assert len(self.detail.actors) == len(zh.actors)
        assert self.detail.actors[0].name == zh.actors[0].name
        assert self.detail.actors[0].gender == zh.actors[0].gender
        assert len(self.detail.magnets) == len(zh.magnets)
        assert self.detail.review_count == zh.review_count
        assert self.detail.want_count == zh.want_count
        assert self.detail.watched_count == zh.watched_count
