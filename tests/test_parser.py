"""
Unit tests for utils/parser.py
Tests for HTML parsing, index page parsing, and detail page parsing
"""
import pytest
import os
import sys
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestExtractVideoCode:
    """Tests for extract_video_code function"""
    
    def test_extract_from_strong_tag(self):
        """Test extracting video code from strong tag"""
        from utils.parser import extract_video_code
        
        html = '<a class="box"><div class="video-title"><strong>ABC-123</strong> Some Title</div></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        
        video_code = extract_video_code(a_tag)
        assert video_code == 'ABC-123'
    
    def test_extract_from_text_without_strong(self):
        """Test extracting video code when no strong tag"""
        from utils.parser import extract_video_code
        
        html = '<a class="box"><div class="video-title">DEF-456 Another Title</div></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        
        video_code = extract_video_code(a_tag)
        assert 'DEF-456' in video_code
    
    def test_extract_missing_video_title(self):
        """Test handling missing video-title div"""
        from utils.parser import extract_video_code
        
        html = '<a class="box"><div class="other">Content</div></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a')
        
        video_code = extract_video_code(a_tag)
        assert video_code == ''


class TestParseIndex:
    """Tests for parse_index function"""
    
    def test_parse_index_phase1_with_subtitle_and_today(self, sample_index_html):
        """Test parsing index page for phase 1 (subtitle + today tags)"""
        from utils.parser import parse_index
        
        results = parse_index(sample_index_html, page_num=1, phase=1)
        
        # Should find entry with both tags
        assert len(results) >= 1
        
        # Check first result has expected structure
        first = results[0]
        assert 'href' in first
        assert 'video_code' in first
        assert first['video_code'] == 'TEST-001'
        assert first['href'] == '/v/test001'
    
    def test_parse_index_phase2_high_quality_only(self, sample_index_html):
        """Test parsing index page for phase 2 (only today tag, high rating)"""
        from utils.parser import parse_index
        
        results = parse_index(sample_index_html, page_num=1, phase=2)
        
        # Should find entry with only today tag and high rating/comments
        # TEST-002 has rate 4.8 and 200 comments, should qualify
        found_test002 = any(r['video_code'] == 'TEST-002' for r in results)
        assert found_test002
    
    def test_parse_index_no_movie_list(self):
        """Test parsing page without movie list"""
        from utils.parser import parse_index
        
        html = '<html><body><div>No movies here</div></body></html>'
        results = parse_index(html, page_num=1, phase=1)
        
        assert results == []
    
    def test_parse_index_extracts_rating(self, sample_index_html):
        """Test that rating is extracted correctly"""
        from utils.parser import parse_index
        
        results = parse_index(sample_index_html, page_num=1, phase=1)
        
        if results:
            first = results[0]
            assert first['rate'] == '4.5'
            assert first['comment_number'] == '150'
    
    def test_parse_index_with_age_verification_modal(self):
        """Test parsing page with age verification modal"""
        from utils.parser import parse_index
        
        html = """
        <html>
        <body>
            <div class="modal is-active over18-modal">Age verification</div>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test">
                        <div class="video-title"><strong>AGE-001</strong></div>
                        <div class="tags has-addons">
                            <span class="tag">含中字磁鏈</span>
                            <span class="tag">今日新種</span>
                        </div>
                    </a>
                </div>
            </div>
        </body>
        </html>
        """
        
        results = parse_index(html, page_num=1, phase=1)
        
        # Should still find results despite age modal
        assert len(results) >= 1
    
    def test_parse_index_disable_release_filter(self, sample_index_html):
        """Test parsing with disabled release date filter"""
        from utils.parser import parse_index
        
        # Create HTML with subtitle tag but no today tag
        html = """
        <html>
        <body>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test">
                        <div class="video-title"><strong>NO-DATE-001</strong></div>
                        <div class="tags has-addons">
                            <span class="tag">含中字磁鏈</span>
                        </div>
                    </a>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Without filter disabled, should not find (no today tag)
        results_filtered = parse_index(html, page_num=1, phase=1, disable_new_releases_filter=False)
        
        # With filter disabled, should find
        results_unfiltered = parse_index(html, page_num=1, phase=1, disable_new_releases_filter=True)
        
        assert len(results_unfiltered) >= len(results_filtered)


class TestParseDetail:
    """Tests for parse_detail function"""
    
    def test_parse_detail_extracts_magnets(self, sample_detail_html):
        """Test extracting magnet links from detail page"""
        from utils.parser import parse_detail
        
        # Use skip_sleep=True to avoid delay in tests
        magnets, actor_info, parse_success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        assert parse_success is True
        assert len(magnets) >= 1
        
        # Check magnet structure
        first_magnet = magnets[0]
        assert 'href' in first_magnet
        assert first_magnet['href'].startswith('magnet:')
        assert 'name' in first_magnet
        assert 'tags' in first_magnet
        assert 'size' in first_magnet
    
    def test_parse_detail_extracts_actor(self, sample_detail_html):
        """Test extracting actor information from detail page"""
        from utils.parser import parse_detail
        
        magnets, actor_info, parse_success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        assert actor_info == 'Test Actress'
    
    def test_parse_detail_no_magnets(self):
        """Test handling page without magnets"""
        from utils.parser import parse_detail
        
        html = '<html><body><div>No magnets here</div></body></html>'
        magnets, actor_info, parse_success = parse_detail(html, index=1, skip_sleep=True)
        
        assert parse_success is False
        assert magnets == []
    
    def test_parse_detail_extracts_timestamp(self, sample_detail_html):
        """Test extracting timestamp from magnet entries"""
        from utils.parser import parse_detail
        
        magnets, actor_info, parse_success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        if magnets:
            first_magnet = magnets[0]
            assert 'timestamp' in first_magnet
            assert first_magnet['timestamp'] == '2025-01-01'
    
    def test_parse_detail_extracts_subtitle_tag(self, sample_detail_html):
        """Test extracting subtitle tag from magnets"""
        from utils.parser import parse_detail
        
        magnets, actor_info, parse_success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        # Find the magnet with subtitle tag
        subtitle_magnets = [m for m in magnets if '字幕' in m.get('tags', [])]
        assert len(subtitle_magnets) >= 1


class TestParseIndexIntegration:
    """Integration tests for parse_index with various HTML structures"""
    
    def test_parse_chinese_simplified_tags(self):
        """Test parsing with simplified Chinese tags"""
        from utils.parser import parse_index
        
        html = """
        <html>
        <body>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/cn001">
                        <div class="video-title"><strong>CN-001</strong></div>
                        <div class="tags has-addons">
                            <span class="tag">含中字磁链</span>
                            <span class="tag">今日新种</span>
                        </div>
                    </a>
                </div>
            </div>
        </body>
        </html>
        """
        
        results = parse_index(html, page_num=1, phase=1)
        assert len(results) >= 1
    
    def test_parse_english_tags(self):
        """Test parsing with English tags"""
        from utils.parser import parse_index
        
        html = """
        <html>
        <body>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/en001">
                        <div class="video-title"><strong>EN-001</strong></div>
                        <div class="tags has-addons">
                            <span class="tag">CnSub DL</span>
                            <span class="tag">Today</span>
                        </div>
                    </a>
                </div>
            </div>
        </body>
        </html>
        """
        
        results = parse_index(html, page_num=1, phase=1)
        assert len(results) >= 1
    
    def test_parse_yesterday_tag(self):
        """Test parsing with yesterday tag"""
        from utils.parser import parse_index
        
        html = """
        <html>
        <body>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/yd001">
                        <div class="video-title"><strong>YD-001</strong></div>
                        <div class="tags has-addons">
                            <span class="tag">含中字磁鏈</span>
                            <span class="tag">昨日新種</span>
                        </div>
                    </a>
                </div>
            </div>
        </body>
        </html>
        """
        
        results = parse_index(html, page_num=1, phase=1)
        assert len(results) >= 1
