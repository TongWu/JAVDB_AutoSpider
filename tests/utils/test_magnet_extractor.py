"""
Unit tests for utils/magnet_extractor.py
"""
import pytest
from utils.magnet_extractor import extract_magnets


class TestExtractMagnets:
    """Tests for extract_magnets function"""
    
    @pytest.fixture
    def sample_magnets_with_subtitle(self):
        """Sample magnet data with subtitle"""
        return [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-C',
                'tags': ['字幕', 'HD'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 10:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001',
                'tags': ['HD'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 09:00:00'
            }
        ]
    
    @pytest.fixture
    def sample_magnets_with_hacked(self):
        """Sample magnet data with hacked versions"""
        return [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-UC',
                'tags': ['HD'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 10:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001-U',
                'tags': ['HD'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 09:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test3',
                'name': 'TEST-001',
                'tags': ['HD'],
                'size': '1.8GB',
                'timestamp': '2024-01-01 08:00:00'
            }
        ]
    
    @pytest.fixture
    def sample_magnets_with_4k(self):
        """Sample magnet data with 4K version"""
        return [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-4k',
                'tags': ['4K'],
                'size': '8.0GB',
                'timestamp': '2024-01-01 10:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001',
                'tags': ['HD'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 09:00:00'
            }
        ]
    
    def test_extract_subtitle_magnet(self, sample_magnets_with_subtitle):
        """Test extracting subtitle magnet"""
        result = extract_magnets(sample_magnets_with_subtitle)
        
        assert result['subtitle'] == 'magnet:?xt=urn:btih:test1'
        assert result['size_subtitle'] == '2.5GB'
        assert result['hacked_subtitle'] == ''
        assert result['hacked_no_subtitle'] == ''
    
    def test_extract_hacked_subtitle(self, sample_magnets_with_hacked):
        """Test extracting hacked_subtitle (-UC pattern)"""
        result = extract_magnets(sample_magnets_with_hacked)
        
        assert result['hacked_subtitle'] == 'magnet:?xt=urn:btih:test1'
        assert result['size_hacked_subtitle'] == '2.5GB'
        assert result['hacked_no_subtitle'] == ''
    
    def test_extract_hacked_no_subtitle(self):
        """Test extracting hacked_no_subtitle (-U pattern)"""
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-U',
                'tags': ['HD'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 10:00:00'
            }
        ]
        result = extract_magnets(magnets)
        
        assert result['hacked_no_subtitle'] == 'magnet:?xt=urn:btih:test1'
        assert result['size_hacked_no_subtitle'] == '2.0GB'
        assert result['hacked_subtitle'] == ''
    
    def test_extract_4k_magnet(self, sample_magnets_with_4k):
        """Test preferring 4K magnet for no_subtitle"""
        result = extract_magnets(sample_magnets_with_4k)
        
        assert result['no_subtitle'] == 'magnet:?xt=urn:btih:test1'
        assert result['size_no_subtitle'] == '8.0GB'
    
    def test_extract_with_index(self, sample_magnets_with_subtitle):
        """Test extract with index parameter for logging"""
        result = extract_magnets(sample_magnets_with_subtitle, index=1)
        
        assert result['subtitle'] == 'magnet:?xt=urn:btih:test1'
    
    def test_extract_empty_magnets(self):
        """Test extracting from empty magnet list"""
        result = extract_magnets([])
        
        assert result['subtitle'] == ''
        assert result['hacked_subtitle'] == ''
        assert result['hacked_no_subtitle'] == ''
        assert result['no_subtitle'] == ''
    
    def test_extract_latest_by_timestamp(self):
        """Test that latest timestamp is preferred"""
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 09:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 10:00:00'
            }
        ]
        result = extract_magnets(magnets)
        
        # Should prefer latest timestamp
        assert result['subtitle'] == 'magnet:?xt=urn:btih:test2'
    
    def test_extract_largest_size_same_timestamp(self):
        """Test that largest size is preferred when timestamps are same"""
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 10:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 10:00:00'
            }
        ]
        result = extract_magnets(magnets)
        
        # Should prefer largest size
        assert result['subtitle'] == 'magnet:?xt=urn:btih:test2'
        assert result['size_subtitle'] == '2.5GB'
    
    def test_hacked_subtitle_priority_over_hacked_no_subtitle(self):
        """Test that hacked_subtitle is prioritized over hacked_no_subtitle"""
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-UC',
                'tags': ['HD'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 10:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001-U',
                'tags': ['HD'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 10:00:00'
            }
        ]
        result = extract_magnets(magnets)
        
        # Should have hacked_subtitle but not hacked_no_subtitle
        assert result['hacked_subtitle'] == 'magnet:?xt=urn:btih:test1'
        assert result['hacked_no_subtitle'] == ''
    
    def test_exclude_hacked_from_subtitle(self):
        """Test that .无码破解 torrents are excluded from subtitle category"""
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-C.无码破解',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 10:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 09:00:00'
            }
        ]
        result = extract_magnets(magnets)
        
        # Should use the non-hacked subtitle
        assert result['subtitle'] == 'magnet:?xt=urn:btih:test2'
        # The hacked subtitle should be in hacked category
        assert result['hacked_subtitle'] == 'magnet:?xt=urn:btih:test1'
    
    def test_all_categories_present(self):
        """Test extracting all four categories simultaneously"""
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:test1',
                'name': 'TEST-001-UC',
                'tags': ['HD'],
                'size': '3.0GB',
                'timestamp': '2024-01-01 12:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test2',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2024-01-01 11:00:00'
            },
            {
                'href': 'magnet:?xt=urn:btih:test3',
                'name': 'TEST-001',
                'tags': ['HD'],
                'size': '2.0GB',
                'timestamp': '2024-01-01 10:00:00'
            }
        ]
        result = extract_magnets(magnets)
        
        assert result['hacked_subtitle'] == 'magnet:?xt=urn:btih:test1'
        assert result['subtitle'] == 'magnet:?xt=urn:btih:test2'
        assert result['no_subtitle'] == 'magnet:?xt=urn:btih:test3'
