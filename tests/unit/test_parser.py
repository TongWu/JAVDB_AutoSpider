"""
Unit tests for index/detail parsing + selection (javdb.parsing +
javdb.pipeline.index_selection).
"""
import os
import sys
import pytest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.pipeline.index_selection import select_index_entries
from javdb.parsing import parse_index_page, parse_detail_page
from javdb.parsing.models import IndexPageResult, MovieIndexEntry
from javdb.parsing.common import extract_video_code
from bs4 import BeautifulSoup


def _parse_index(html_content, page_num, phase=1, disable_new_releases_filter=False, is_adhoc_mode=False):
    """Compose the canonical index parse + selection path used by callers.

    Mirrors how spider callers parse an index page: parse HTML via
    ``parse_index_page`` then apply business filtering via
    ``select_index_entries``. Returns ``[]`` when the page has no movie list.
    """
    page_result = parse_index_page(html_content, page_num)
    if not page_result.has_movie_list:
        return []
    return select_index_entries(
        page_result,
        page_num=page_num,
        phase=phase,
        disable_new_releases_filter=disable_new_releases_filter,
        is_adhoc_mode=is_adhoc_mode,
    )


def _parse_detail(html_content):
    """Compose the canonical detail parse + legacy accessor path.

    Mirrors how spider callers convert a parsed ``MovieDetail`` into the legacy
    6-tuple ``(magnets, actor_info, actor_gender, actor_link, supporting,
    parse_success)``.
    """
    detail = parse_detail_page(html_content)
    return (
        detail.get_magnets_as_legacy(),
        detail.get_first_actor_name(),
        detail.get_first_actor_gender(),
        detail.get_first_actor_href(),
        detail.get_supporting_actors_json(),
        detail.parse_success,
    )


class TestExtractVideoCode:
    """Test cases for extract_video_code function."""
    
    def test_extract_valid_code(self):
        """Test extracting valid video code."""
        html = '''
        <a class="box" href="/v/ABC-123">
            <div class="video-title"><strong>ABC-123</strong></div>
        </a>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        result = extract_video_code(a_tag)
        assert result == 'ABC-123'
    
    def test_extract_code_without_strong(self):
        """Test extracting code from text without strong tag."""
        html = '''
        <a class="box" href="/v/DEF-456">
            <div class="video-title">DEF-456</div>
        </a>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        result = extract_video_code(a_tag)
        assert result == 'DEF-456'
    
    def test_invalid_code_without_dash(self):
        """Test that codes without dash are rejected."""
        html = '''
        <a class="box" href="/v/INVALID">
            <div class="video-title"><strong>INVALID</strong></div>
        </a>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        result = extract_video_code(a_tag)
        assert result == ''
    
    def test_no_video_title_div(self):
        """Test when video-title div is missing."""
        html = '''
        <a class="box" href="/v/ABC-123">
            <div class="other-class">Some text</div>
        </a>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        result = extract_video_code(a_tag)
        assert result == ''


class TestParseIndex:
    """Test cases for parse_index function."""

    def test_select_index_entries_phase1(self, sample_index_html):
        """Phase 1 should keep subtitle entries with release-date tags."""
        page_result = parse_index_page(sample_index_html, page_num=1)
        results = select_index_entries(page_result, page_num=1, phase=1)

        assert [r["video_code"] for r in results] == ["ABC-123", "GHI-789"]

    def test_select_index_entries_phase1_accepts_simplified_yesterday_tag(self):
        """Simplified yesterday tag should count as a release-date tag."""
        page_result = IndexPageResult(
            has_movie_list=True,
            movies=[
                MovieIndexEntry(
                    href="/v/ABC-123",
                    video_code="ABC-123",
                    rate="4.47",
                    comment_count="595",
                    tags=["含中字磁鏈", "昨日新种"],
                ),
            ],
        )

        results = select_index_entries(page_result, page_num=1, phase=1)

        assert [r["video_code"] for r in results] == ["ABC-123"]
        assert results[0]["is_yesterday_release"] is True

    def test_select_index_entries_phase2(self, sample_index_html):
        """Phase 2 should keep qualifying non-subtitle entries only."""
        page_result = parse_index_page(sample_index_html, page_num=1)
        results = select_index_entries(page_result, page_num=1, phase=2)

        assert [r["video_code"] for r in results] == ["DEF-456"]

    def test_select_index_entries_adhoc_mode(self, sample_index_html_with_magnet_tags):
        """Adhoc mode should split subtitle and non-subtitle entries by phase."""
        page_result = parse_index_page(sample_index_html_with_magnet_tags, page_num=1)

        phase1 = select_index_entries(page_result, page_num=1, phase=1, is_adhoc_mode=True)
        phase2 = select_index_entries(page_result, page_num=1, phase=2, is_adhoc_mode=True)

        assert [r["video_code"] for r in phase1] == ["ABC-123"]
        assert [r["video_code"] for r in phase2] == ["DEF-456"]

    def test_select_index_entries_ignored_release_date(self, sample_index_html):
        """Release-date tags should be optional when the filter is disabled."""
        page_result = parse_index_page(sample_index_html, page_num=1)
        results = select_index_entries(
            page_result,
            page_num=1,
            phase=1,
            disable_new_releases_filter=True,
        )

        assert [r["video_code"] for r in results] == ["ABC-123", "GHI-789"]

    def test_select_index_entries_invalid_rate_or_comment(self):
        """Invalid rate/comment data should be skipped in phase 2."""
        page_result = IndexPageResult(
            has_movie_list=True,
            movies=[
                MovieIndexEntry(
                    href="/v/ABC-123",
                    video_code="ABC-123",
                    rate="bad",
                    comment_count="120",
                    tags=["今日新種"],
                ),
                MovieIndexEntry(
                    href="/v/DEF-456",
                    video_code="DEF-456",
                    rate="4.50",
                    comment_count="bad",
                    tags=["今日新種"],
                ),
            ],
        )
        results = select_index_entries(page_result, page_num=1, phase=2)

        assert results == []

    def test_select_index_entries_no_video_code(self):
        """Entries without a valid video code should be excluded."""
        page_result = IndexPageResult(
            has_movie_list=True,
            movies=[
                MovieIndexEntry(
                    href="/v/NO-CODE",
                    video_code="",
                    rate="4.50",
                    comment_count="120",
                    tags=["今日新種"],
                ),
            ],
        )
        results = select_index_entries(page_result, page_num=1, phase=1)

        assert results == []

    def test_select_index_entries_subtitle_and_magnet_tags(self, sample_index_html_with_magnet_tags):
        """Subtitle and magnet tags should route entries into the right phase."""
        page_result = parse_index_page(sample_index_html_with_magnet_tags, page_num=1)

        phase1 = select_index_entries(page_result, page_num=1, phase=1, is_adhoc_mode=True)
        phase2 = select_index_entries(page_result, page_num=1, phase=2, is_adhoc_mode=True)

        assert [r["video_code"] for r in phase1] == ["ABC-123"]
        assert [r["video_code"] for r in phase2] == ["DEF-456"]

    def test_select_index_entries_legacy_dict_output(self, sample_index_html):
        """Legacy dict output should stay byte-for-byte compatible in shape."""
        page_result = parse_index_page(sample_index_html, page_num=5)
        results = select_index_entries(page_result, page_num=5, phase=1)

        assert results == [
            {
                "href": "/v/ABC-123",
                "video_code": "ABC-123",
                "page": 5,
                "actor": "",
                "rate": "4.47",
                "comment_number": "595",
                "is_today_release": True,
                "is_yesterday_release": False,
            },
            {
                "href": "/v/GHI-789",
                "video_code": "GHI-789",
                "page": 5,
                "actor": "",
                "rate": "3.85",
                "comment_number": "50",
                "is_today_release": False,
                "is_yesterday_release": True,
            },
        ]
    
    def test_parse_phase1_with_subtitle_and_today(self, sample_index_html):
        """Test parsing phase 1 entries with both subtitle and today tags."""
        results = _parse_index(sample_index_html, page_num=1, phase=1)
        
        # Should find ABC-123 (has both subtitle and today tags)
        # and GHI-789 (has both subtitle and yesterday tags)
        assert len(results) >= 1
        
        hrefs = [r['href'] for r in results]
        assert '/v/ABC-123' in hrefs
    
    def test_parse_phase1_filter_disabled(self, sample_index_html):
        """Test parsing phase 1 with filter disabled."""
        results = _parse_index(sample_index_html, page_num=1, phase=1, disable_new_releases_filter=True)
        
        # Should find all entries with subtitle tag
        hrefs = [r['href'] for r in results]
        assert '/v/ABC-123' in hrefs
        assert '/v/GHI-789' in hrefs
    
    def test_parse_phase2_high_quality(self, sample_index_html):
        """Test parsing phase 2 entries (high quality, no subtitle)."""
        results = _parse_index(sample_index_html, page_num=1, phase=2)
        
        # DEF-456 has today tag, rate=4.52, comments=120
        # It should be included if it meets the quality thresholds
        hrefs = [r['href'] for r in results]
        # DEF-456 should pass quality filter (4.52 > 4.0, 120 > 100)
        assert '/v/DEF-456' in hrefs
    
    def test_parse_no_movie_list(self):
        """Test parsing page without movie list."""
        html = '''
        <html>
        <body>
            <div class="other-content">No movies here</div>
        </body>
        </html>
        '''
        results = _parse_index(html, page_num=1, phase=1)
        assert results == []
    
    def test_parse_extracts_rate_and_comments(self, sample_index_html):
        """Test that rate and comment_number are extracted."""
        results = _parse_index(sample_index_html, page_num=1, phase=1)
        
        # Find ABC-123 result
        abc_result = next((r for r in results if r['href'] == '/v/ABC-123'), None)
        if abc_result:
            assert abc_result['rate'] == '4.47'
            assert abc_result['comment_number'] == '595'
    
    def test_parse_includes_page_number(self, sample_index_html):
        """Test that page number is included in results."""
        results = _parse_index(sample_index_html, page_num=5, phase=1)
        
        for result in results:
            assert result['page'] == 5
    
    def test_parse_adhoc_mode_phase1_processes_subtitle_entries(self, sample_index_html):
        """Test that adhoc mode phase 1 processes entries WITH subtitle tag."""
        results = _parse_index(sample_index_html, page_num=1, phase=1, is_adhoc_mode=True)
        
        # Adhoc mode phase 1 should find entries with subtitle tag:
        # ABC-123 (has 含中字磁鏈), GHI-789 (has 含中字磁鏈)
        # DEF-456 has no subtitle tag, so it goes to phase 2
        hrefs = [r['href'] for r in results]
        assert len(results) == 2
        assert '/v/ABC-123' in hrefs
        assert '/v/GHI-789' in hrefs
        assert '/v/DEF-456' not in hrefs  # No subtitle tag, processed in phase 2
    
    def test_parse_adhoc_mode_phase2_processes_non_subtitle_entries(self, sample_index_html):
        """Test that adhoc mode phase 2 processes entries WITHOUT subtitle tag."""
        results = _parse_index(sample_index_html, page_num=1, phase=2, is_adhoc_mode=True)
        
        # Phase 2 in adhoc mode should find entries without subtitle tag:
        # DEF-456 (no subtitle tag)
        hrefs = [r['href'] for r in results]
        assert len(results) == 1
        assert '/v/DEF-456' in hrefs
    
    def test_parse_adhoc_mode_extracts_metadata(self, sample_index_html):
        """Test that adhoc mode still extracts rate and comment_number."""
        results = _parse_index(sample_index_html, page_num=1, phase=1, is_adhoc_mode=True)
        
        # Find ABC-123 result
        abc_result = next((r for r in results if r['href'] == '/v/ABC-123'), None)
        assert abc_result is not None
        assert abc_result['rate'] == '4.47'
        assert abc_result['comment_number'] == '595'

    def test_parse_adhoc_mode_filters_no_magnet_entries(self, sample_index_html_with_magnet_tags):
        """Test that adhoc mode filters out entries without magnet tags."""
        results = _parse_index(sample_index_html_with_magnet_tags, page_num=1, phase=1, is_adhoc_mode=True)
        
        # Phase 1 should only find entries WITH subtitle tag AND magnet tag
        # ABC-123 has 含中字磁鏈 (subtitle magnet tag)
        # DEF-456 has 含磁鏈 (regular magnet tag, no subtitle - goes to phase 2)
        # GHI-789 has NO magnet tag (should be filtered out completely)
        # JKL-012 has empty tags (should be filtered out completely)
        hrefs = [r['href'] for r in results]
        assert '/v/ABC-123' in hrefs  # Has subtitle magnet tag
        assert '/v/DEF-456' not in hrefs  # No subtitle, goes to phase 2
        assert '/v/GHI-789' not in hrefs  # No magnet tag, filtered out
        assert '/v/JKL-012' not in hrefs  # Empty tags, filtered out

    def test_parse_adhoc_mode_magnet_filter_phase2(self, sample_index_html_with_magnet_tags):
        """Test that phase 2 in adhoc mode also filters by magnet tags."""
        results = _parse_index(sample_index_html_with_magnet_tags, page_num=1, phase=2, is_adhoc_mode=True)
        
        # Phase 2 should find entries WITH magnet tag but WITHOUT subtitle tag
        # DEF-456 has 含磁鏈 (regular magnet tag)
        # ABC-123 has subtitle - processed in phase 1
        # GHI-789 and JKL-012 have no magnet tags - filtered out
        hrefs = [r['href'] for r in results]
        assert '/v/DEF-456' in hrefs  # Has magnet tag, no subtitle
        assert '/v/ABC-123' not in hrefs  # Has subtitle, processed in phase 1
        assert '/v/GHI-789' not in hrefs  # No magnet tag, filtered out
        assert '/v/JKL-012' not in hrefs  # Empty tags, filtered out


class TestParseDetail:
    """Test cases for parse_detail function."""
    
    def test_parse_detail_with_magnets(self, sample_detail_html):
        """Test parsing detail page with magnets."""
        magnets, actor_info, actor_gender, actor_link, supporting, parse_success = _parse_detail(
            sample_detail_html)

        assert parse_success is True
        assert len(magnets) == 3
        assert actor_info == 'Sample Actor'
        assert actor_gender == 'female'
        assert actor_link == '/actors/xyz'
        assert supporting == '[]'
    
    def test_parse_detail_magnet_structure(self, sample_detail_html):
        """Test that parsed magnets have correct structure."""
        magnets, _a, _g, _l, _s, _parse_success = _parse_detail(
            sample_detail_html)

        for magnet in magnets:
            assert 'href' in magnet
            assert 'name' in magnet
            assert 'tags' in magnet
            assert 'size' in magnet
            assert 'timestamp' in magnet
    
    def test_parse_detail_no_magnets(self):
        """Test parsing detail page without magnets."""
        html = '''
        <html>
        <body>
            <div class="video-meta-panel">
                <div class="panel-block">
                    <strong>演員:</strong>
                    <span class="value">
                        <a href="/actors/xyz">Test Actor</a>
                    </span>
                </div>
            </div>
            <div class="other-content">No magnets here</div>
        </body>
        </html>
        '''
        magnets, actor_info, actor_gender, actor_link, supporting, parse_success = _parse_detail(
            html)

        assert parse_success is False
        assert magnets == []
        assert actor_info == 'Test Actor'
        assert actor_gender == ''
        assert actor_link == '/actors/xyz'
        assert supporting == '[]'
    
    def test_parse_detail_extracts_size(self, sample_detail_html):
        """Test that size is extracted correctly."""
        magnets, _, _, _, _, _ = _parse_detail(sample_detail_html)

        # Find the subtitle magnet
        subtitle_magnet = next((m for m in magnets if 'subtitle' in m['name'].lower()), None)
        if subtitle_magnet:
            assert subtitle_magnet['size'] == '4.94GB'
    
    def test_parse_detail_extracts_tags(self, sample_detail_html):
        """Test that tags are extracted correctly."""
        magnets, _, _, _, _, _ = _parse_detail(sample_detail_html)

        # Find the subtitle magnet
        subtitle_magnet = next((m for m in magnets if 'subtitle' in m['name'].lower()), None)
        if subtitle_magnet:
            assert '字幕' in subtitle_magnet['tags']

    def test_parse_detail_vdd201_fixture_html(self):
        html_path = os.path.join(project_root, 'html', 'detailed_page_VDD-201.html')
        if not os.path.isfile(html_path):
            pytest.skip('fixture HTML not present')
        with open(html_path, encoding='utf-8') as f:
            html = f.read()
        magnets, actor_info, actor_gender, actor_link, supporting, parse_success = _parse_detail(
            html)
        assert actor_info == '真北祈'
        assert actor_gender == 'female'
        assert actor_link == '/actors/450wJ'
        assert parse_success is True
        assert len(magnets) >= 1
        assert 'male' in supporting and 'マッスル澤野' in supporting
