"""
Unit tests for utils/magnet_extractor.py
Tests for magnet link extraction and categorization
"""
import pytest
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestExtractMagnets:
    """Tests for extract_magnets function"""
    
    def test_extract_subtitle_magnet(self):
        """Test extracting subtitle magnet"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:subtitle123',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        assert result['subtitle'] == 'magnet:?xt=urn:btih:subtitle123'
        assert result['size_subtitle'] == '2.5GB'
    
    def test_extract_hacked_subtitle_magnet(self):
        """Test extracting hacked subtitle magnet (-UC pattern)"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:hacked_sub',
                'name': 'TEST-001-UC',
                'tags': [],
                'size': '3.0GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        assert result['hacked_subtitle'] == 'magnet:?xt=urn:btih:hacked_sub'
        assert result['size_hacked_subtitle'] == '3.0GB'
    
    def test_extract_hacked_no_subtitle_magnet(self):
        """Test extracting hacked no-subtitle magnet (-U pattern)"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:hacked_nosub',
                'name': 'TEST-001-U',
                'tags': [],
                'size': '2.8GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        assert result['hacked_no_subtitle'] == 'magnet:?xt=urn:btih:hacked_nosub'
        assert result['size_hacked_no_subtitle'] == '2.8GB'
    
    def test_extract_no_subtitle_magnet(self):
        """Test extracting regular no-subtitle magnet"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:normal',
                'name': 'TEST-001',
                'tags': [],
                'size': '2.0GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        assert result['no_subtitle'] == 'magnet:?xt=urn:btih:normal'
        assert result['size_no_subtitle'] == '2.0GB'
    
    def test_extract_prefers_4k_for_no_subtitle(self):
        """Test that 4K torrents are preferred for no_subtitle category"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:normal',
                'name': 'TEST-001',
                'tags': [],
                'size': '2.0GB',
                'timestamp': '2025-01-01'
            },
            {
                'href': 'magnet:?xt=urn:btih:4k_version',
                'name': 'TEST-001-4K',
                'tags': [],
                'size': '8.0GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        # Should prefer 4K version
        assert result['no_subtitle'] == 'magnet:?xt=urn:btih:4k_version'
    
    def test_extract_multiple_categories(self):
        """Test extracting multiple magnet categories"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:hacked_sub',
                'name': 'TEST-001-UC',
                'tags': [],
                'size': '3.0GB',
                'timestamp': '2025-01-01'
            },
            {
                'href': 'magnet:?xt=urn:btih:subtitle',
                'name': 'TEST-001-C',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2025-01-01'
            },
            {
                'href': 'magnet:?xt=urn:btih:normal',
                'name': 'TEST-001',
                'tags': [],
                'size': '2.0GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        assert result['hacked_subtitle'] != ''
        assert result['subtitle'] != ''
        assert result['no_subtitle'] != ''
    
    def test_extract_empty_magnets_list(self):
        """Test handling empty magnets list"""
        from utils.magnet_extractor import extract_magnets
        
        result = extract_magnets([])
        
        assert result['hacked_subtitle'] == ''
        assert result['hacked_no_subtitle'] == ''
        assert result['subtitle'] == ''
        assert result['no_subtitle'] == ''
    
    def test_extract_prefers_latest_timestamp(self):
        """Test that latest timestamp is preferred when sizes are equal"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:older',
                'name': 'TEST-001-C-old',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2025-01-01'
            },
            {
                'href': 'magnet:?xt=urn:btih:newer',
                'name': 'TEST-001-C-new',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2025-01-05'
            }
        ]
        
        result = extract_magnets(magnets)
        
        # Should prefer newer timestamp
        assert result['subtitle'] == 'magnet:?xt=urn:btih:newer'
    
    def test_extract_prefers_larger_size(self):
        """Test that larger size is preferred when timestamps are equal"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:smaller',
                'name': 'TEST-001-C-small',
                'tags': ['字幕'],
                'size': '1.5GB',
                'timestamp': '2025-01-01'
            },
            {
                'href': 'magnet:?xt=urn:btih:larger',
                'name': 'TEST-001-C-large',
                'tags': ['字幕'],
                'size': '2.5GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        # Should prefer larger size
        assert result['subtitle'] == 'magnet:?xt=urn:btih:larger'
    
    def test_extract_hacked_patterns(self):
        """Test various hacked torrent name patterns"""
        from utils.magnet_extractor import extract_magnets
        
        # Test -CU pattern
        magnets_cu = [{'href': 'magnet:?xt=urn:btih:cu', 'name': 'TEST-CU', 'tags': [], 'size': '2GB', 'timestamp': '2025-01-01'}]
        result_cu = extract_magnets(magnets_cu)
        assert result_cu['hacked_subtitle'] != ''
        
        # Test -U-C pattern
        magnets_uc = [{'href': 'magnet:?xt=urn:btih:uc', 'name': 'TEST-U-C', 'tags': [], 'size': '2GB', 'timestamp': '2025-01-01'}]
        result_uc = extract_magnets(magnets_uc)
        assert result_uc['hacked_subtitle'] != ''
        
        # Test -C-U pattern
        magnets_c_u = [{'href': 'magnet:?xt=urn:btih:c_u', 'name': 'TEST-C-U', 'tags': [], 'size': '2GB', 'timestamp': '2025-01-01'}]
        result_c_u = extract_magnets(magnets_c_u)
        assert result_c_u['hacked_subtitle'] != ''
        
        # Test .无码破解 pattern
        magnets_wm = [{'href': 'magnet:?xt=urn:btih:wm', 'name': 'TEST.无码破解', 'tags': [], 'size': '2GB', 'timestamp': '2025-01-01'}]
        result_wm = extract_magnets(magnets_wm)
        assert result_wm['hacked_no_subtitle'] != ''
    
    def test_extract_excludes_hacked_from_subtitle(self):
        """Test that torrents with .无码破解 are excluded from subtitle category"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:hacked',
                'name': 'TEST-001.无码破解',
                'tags': ['字幕'],  # Has subtitle tag but should be categorized as hacked
                'size': '2.5GB',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        # Should NOT be in subtitle category
        assert result['subtitle'] == ''
        # Should be in hacked category
        assert result['hacked_no_subtitle'] != ''


class TestExtractMagnetsEdgeCases:
    """Edge case tests for extract_magnets"""
    
    def test_handle_missing_size(self):
        """Test handling magnets without size info"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:nosize',
                'name': 'TEST-001',
                'tags': [],
                'size': '',
                'timestamp': '2025-01-01'
            }
        ]
        
        result = extract_magnets(magnets)
        
        # Should still extract the magnet
        assert result['no_subtitle'] == 'magnet:?xt=urn:btih:nosize'
        assert result['size_no_subtitle'] == ''
    
    def test_handle_missing_timestamp(self):
        """Test handling magnets without timestamp"""
        from utils.magnet_extractor import extract_magnets
        
        magnets = [
            {
                'href': 'magnet:?xt=urn:btih:notime',
                'name': 'TEST-001',
                'tags': [],
                'size': '2.0GB',
                'timestamp': ''
            }
        ]
        
        result = extract_magnets(magnets)
        
        # Should still extract the magnet
        assert result['no_subtitle'] == 'magnet:?xt=urn:btih:notime'
    
    def test_handle_various_size_formats(self):
        """Test handling different size format strings"""
        from utils.magnet_extractor import extract_magnets
        
        # Test GB format
        magnets_gb = [{'href': 'magnet:?xt=urn:btih:gb', 'name': 'TEST-GB', 'tags': [], 'size': '4.5GB', 'timestamp': '2025-01-01'}]
        result_gb = extract_magnets(magnets_gb)
        assert result_gb['no_subtitle'] != ''
        
        # Test MB format
        magnets_mb = [{'href': 'magnet:?xt=urn:btih:mb', 'name': 'TEST-MB', 'tags': [], 'size': '800MB', 'timestamp': '2025-01-01'}]
        result_mb = extract_magnets(magnets_mb)
        assert result_mb['no_subtitle'] != ''
        
        # Test KB format
        magnets_kb = [{'href': 'magnet:?xt=urn:btih:kb', 'name': 'TEST-KB', 'tags': [], 'size': '500KB', 'timestamp': '2025-01-01'}]
        result_kb = extract_magnets(magnets_kb)
        assert result_kb['no_subtitle'] != ''
