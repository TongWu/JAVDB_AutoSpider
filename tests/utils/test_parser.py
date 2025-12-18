"""
Unit tests for utils/parser.py
"""
import pytest
from bs4 import BeautifulSoup
from utils.parser import (
    extract_video_code,
    parse_index,
    parse_detail
)


class TestExtractVideoCode:
    """Tests for extract_video_code function"""
    
    def test_extract_from_strong_tag(self):
        """Test extracting video code from <strong> tag"""
        html = '''
        <a class="box">
            <div class="video-title">
                <strong>TEST-001</strong> Test Video Title
            </div>
        </a>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a', class_='box')
        
        result = extract_video_code(a_tag)
        assert result == 'TEST-001'
    
    def test_extract_from_full_text(self):
        """Test extracting video code from full text when no strong tag"""
        html = '''
        <a class="box">
            <div class="video-title">
                TEST-002 Test Video Title
            </div>
        </a>
        '''
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a', class_='box')
        
        result = extract_video_code(a_tag)
        assert 'TEST-002' in result
    
    def test_extract_no_video_title(self):
        """Test extracting when no video-title div exists"""
        html = '<a class="box"></a>'
        soup = BeautifulSoup(html, 'html.parser')
        a_tag = soup.find('a', class_='box')
        
        result = extract_video_code(a_tag)
        assert result == ''


class TestParseIndex:
    """Tests for parse_index function"""
    
    @pytest.fixture
    def sample_phase1_html(self):
        """Sample HTML for phase 1 (subtitle + today/yesterday)"""
        return '''
        <html>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test001">
                        <div class="video-title">
                            <strong>TEST-001</strong> Test Video
                        </div>
                        <div class="tags has-addons">
                            <span class="tag">含中字磁鏈</span>
                            <span class="tag">今日新種</span>
                        </div>
                        <div class="score">
                            <span class="value">4.5分 由100人評價</span>
                        </div>
                    </a>
                </div>
            </div>
        </html>
        '''
    
    @pytest.fixture
    def sample_phase2_html(self):
        """Sample HTML for phase 2 (today/yesterday, high rating)"""
        return '''
        <html>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test002">
                        <div class="video-title">
                            <strong>TEST-002</strong> Test Video
                        </div>
                        <div class="tags has-addons">
                            <span class="tag">今日新種</span>
                        </div>
                        <div class="score">
                            <span class="value">4.5分 由150人評價</span>
                        </div>
                    </a>
                </div>
            </div>
        </html>
        '''
    
    @pytest.fixture
    def no_movie_list_html(self):
        """Sample HTML without movie list"""
        return '<html><body><h1>No Movies</h1></body></html>'
    
    def test_parse_phase1_with_subtitle_and_today(self, sample_phase1_html):
        """Test parsing phase 1 entries with subtitle and today tags"""
        results = parse_index(sample_phase1_html, page_num=1, phase=1)
        
        assert len(results) == 1
        assert results[0]['href'] == '/v/test001'
        assert results[0]['video_code'] == 'TEST-001'
        assert results[0]['rate'] == '4.5'
        assert results[0]['comment_number'] == '100'
    
    def test_parse_phase2_with_high_rating(self, sample_phase2_html):
        """Test parsing phase 2 entries with high rating and comments"""
        results = parse_index(sample_phase2_html, page_num=1, phase=2)
        
        assert len(results) == 1
        assert results[0]['href'] == '/v/test002'
        assert results[0]['video_code'] == 'TEST-002'
        assert float(results[0]['rate']) >= 4.0
        assert int(results[0]['comment_number']) >= 85
    
    def test_parse_phase2_filters_low_rating(self):
        """Test that phase 2 filters out low rating entries"""
        html = '''
        <html>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test003">
                        <div class="video-title">
                            <strong>TEST-003</strong> Low Rating
                        </div>
                        <div class="tags has-addons">
                            <span class="tag">今日新種</span>
                        </div>
                        <div class="score">
                            <span class="value">3.5分 由150人評價</span>
                        </div>
                    </a>
                </div>
            </div>
        </html>
        '''
        results = parse_index(html, page_num=1, phase=2)
        
        # Should be filtered out due to low rating
        assert len(results) == 0
    
    def test_parse_phase2_filters_low_comments(self):
        """Test that phase 2 filters out entries with low comments"""
        html = '''
        <html>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test004">
                        <div class="video-title">
                            <strong>TEST-004</strong> Low Comments
                        </div>
                        <div class="tags has-addons">
                            <span class="tag">今日新種</span>
                        </div>
                        <div class="score">
                            <span class="value">4.5分 由50人評價</span>
                        </div>
                    </a>
                </div>
            </div>
        </html>
        '''
        results = parse_index(html, page_num=1, phase=2)
        
        # Should be filtered out due to low comments
        assert len(results) == 0
    
    def test_parse_no_movie_list(self, no_movie_list_html):
        """Test parsing when no movie list is found"""
        results = parse_index(no_movie_list_html, page_num=1, phase=1)
        
        assert len(results) == 0
    
    def test_parse_with_disable_filter(self):
        """Test parsing with new releases filter disabled"""
        html = '''
        <html>
            <div class="movie-list h cols-4 vcols-8">
                <div class="item">
                    <a class="box" href="/v/test005">
                        <div class="video-title">
                            <strong>TEST-005</strong> Old Video
                        </div>
                        <div class="tags has-addons">
                            <span class="tag">含中字磁鏈</span>
                        </div>
                        <div class="score">
                            <span class="value">4.5分 由100人評價</span>
                        </div>
                    </a>
                </div>
            </div>
        </html>
        '''
        results = parse_index(html, page_num=1, phase=1, disable_new_releases_filter=True)
        
        # Should be included even without today/yesterday tag
        assert len(results) == 1


class TestParseDetail:
    """Tests for parse_detail function"""
    
    @pytest.fixture
    def sample_detail_html(self):
        """Sample HTML for detail page"""
        return '''
        <html>
            <div class="video-meta-panel">
                <div class="panel-block">
                    <strong>演員:</strong>
                    <span class="value">
                        <a href="/actors/1">Test Actor</a>
                        <a href="/actors/2">Other Actor</a>
                    </span>
                </div>
            </div>
            <div id="magnets-content">
                <div class="item columns is-desktop">
                    <div class="magnet-name">
                        <a href="magnet:?xt=urn:btih:test1">
                            <span class="name">TEST-001-UC</span>
                            <span class="meta">2.5GB, 1個文件</span>
                            <div class="tags">
                                <span class="tag">HD</span>
                            </div>
                        </a>
                    </div>
                    <span class="time">2024-01-01</span>
                </div>
            </div>
        </html>
        '''
    
    @pytest.fixture
    def detail_html_no_magnets(self):
        """Sample HTML with no magnets content"""
        return '''
        <html>
            <div class="video-meta-panel">
                <div class="panel-block">
                    <strong>演員:</strong>
                    <span class="value">
                        <a href="/actors/1">Test Actor</a>
                    </span>
                </div>
            </div>
        </html>
        '''
    
    def test_parse_detail_with_magnets(self, sample_detail_html):
        """Test parsing detail page with magnets"""
        magnets, actor, success = parse_detail(sample_detail_html, index=1, skip_sleep=True)
        
        assert len(magnets) == 1
        assert magnets[0]['href'] == 'magnet:?xt=urn:btih:test1'
        assert magnets[0]['name'] == 'TEST-001-UC'
        assert magnets[0]['size'] == '2.5GB'
        assert magnets[0]['timestamp'] == '2024-01-01'
        assert 'HD' in magnets[0]['tags']
        assert actor == 'Test Actor'
        assert success is True
    
    def test_parse_detail_no_magnets(self, detail_html_no_magnets):
        """Test parsing detail page without magnets"""
        magnets, actor, success = parse_detail(detail_html_no_magnets, index=1, skip_sleep=True)
        
        assert len(magnets) == 0
        assert actor == 'Test Actor'
        assert success is False
    
    def test_parse_detail_no_actor(self):
        """Test parsing detail page without actor info"""
        html = '''
        <html>
            <div id="magnets-content">
                <div class="item columns is-desktop">
                    <div class="magnet-name">
                        <a href="magnet:?xt=urn:btih:test1">
                            <span class="name">TEST-001</span>
                            <span class="meta">2.0GB, 1個文件</span>
                        </a>
                    </div>
                </div>
            </div>
        </html>
        '''
        magnets, actor, success = parse_detail(html, skip_sleep=True)
        
        assert len(magnets) == 1
        assert actor == ''
        assert success is True
    
    def test_parse_detail_multiple_magnets(self):
        """Test parsing detail page with multiple magnets"""
        html = '''
        <html>
            <div id="magnets-content">
                <div class="item columns is-desktop">
                    <div class="magnet-name">
                        <a href="magnet:?xt=urn:btih:test1">
                            <span class="name">TEST-001-UC</span>
                            <span class="meta">2.5GB, 1個文件</span>
                            <div class="tags">
                                <span class="tag">HD</span>
                            </div>
                        </a>
                    </div>
                    <span class="time">2024-01-01 10:00:00</span>
                </div>
                <div class="item columns is-desktop">
                    <div class="magnet-name">
                        <a href="magnet:?xt=urn:btih:test2">
                            <span class="name">TEST-001-C</span>
                            <span class="meta">2.0GB, 1個文件</span>
                            <div class="tags">
                                <span class="tag">字幕</span>
                            </div>
                        </a>
                    </div>
                    <span class="time">2024-01-01 09:00:00</span>
                </div>
            </div>
        </html>
        '''
        magnets, actor, success = parse_detail(html, skip_sleep=True)
        
        assert len(magnets) == 2
        assert magnets[0]['name'] == 'TEST-001-UC'
        assert magnets[1]['name'] == 'TEST-001-C'
        assert '字幕' in magnets[1]['tags']
