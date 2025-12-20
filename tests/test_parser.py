"""
Unit tests for utils/parser.py functions.
"""
import os
import sys
import pytest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.parser import extract_video_code, parse_index, parse_detail
from bs4 import BeautifulSoup


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
    
    def test_parse_phase1_with_subtitle_and_today(self, sample_index_html):
        """Test parsing phase 1 entries with both subtitle and today tags."""
        results = parse_index(sample_index_html, page_num=1, phase=1)
        
        # Should find ABC-123 (has both subtitle and today tags)
        # and GHI-789 (has both subtitle and yesterday tags)
        assert len(results) >= 1
        
        hrefs = [r['href'] for r in results]
        assert '/v/ABC-123' in hrefs
    
    def test_parse_phase1_filter_disabled(self, sample_index_html):
        """Test parsing phase 1 with filter disabled."""
        results = parse_index(sample_index_html, page_num=1, phase=1, disable_new_releases_filter=True)
        
        # Should find all entries with subtitle tag
        hrefs = [r['href'] for r in results]
        assert '/v/ABC-123' in hrefs
        assert '/v/GHI-789' in hrefs
    
    def test_parse_phase2_high_quality(self, sample_index_html):
        """Test parsing phase 2 entries (high quality, no subtitle)."""
        results = parse_index(sample_index_html, page_num=1, phase=2)
        
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
        results = parse_index(html, page_num=1, phase=1)
        assert results == []
    
    def test_parse_extracts_rate_and_comments(self, sample_index_html):
        """Test that rate and comment_number are extracted."""
        results = parse_index(sample_index_html, page_num=1, phase=1)
        
        # Find ABC-123 result
        abc_result = next((r for r in results if r['href'] == '/v/ABC-123'), None)
        if abc_result:
            assert abc_result['rate'] == '4.47'
            assert abc_result['comment_number'] == '595'
    
    def test_parse_includes_page_number(self, sample_index_html):
        """Test that page number is included in results."""
        results = parse_index(sample_index_html, page_num=5, phase=1)
        
        for result in results:
            assert result['page'] == 5


class TestParseDetail:
    """Test cases for parse_detail function."""
    
    def test_parse_detail_with_magnets(self, sample_detail_html):
        """Test parsing detail page with magnets."""
        magnets, actor_info, parse_success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        assert parse_success is True
        assert len(magnets) == 3
        assert actor_info == 'Sample Actor'
    
    def test_parse_detail_magnet_structure(self, sample_detail_html):
        """Test that parsed magnets have correct structure."""
        magnets, actor_info, parse_success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
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
        magnets, actor_info, parse_success = parse_detail(html, index=1, skip_sleep=True)
        
        assert parse_success is False
        assert magnets == []
        assert actor_info == 'Test Actor'
    
    def test_parse_detail_extracts_size(self, sample_detail_html):
        """Test that size is extracted correctly."""
        magnets, _, _ = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        # Find the subtitle magnet
        subtitle_magnet = next((m for m in magnets if 'subtitle' in m['name'].lower()), None)
        if subtitle_magnet:
            assert subtitle_magnet['size'] == '4.94GB'
    
    def test_parse_detail_extracts_tags(self, sample_detail_html):
        """Test that tags are extracted correctly."""
        magnets, _, _ = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        # Find the subtitle magnet
        subtitle_magnet = next((m for m in magnets if 'subtitle' in m['name'].lower()), None)
        if subtitle_magnet:
            assert '字幕' in subtitle_magnet['tags']

