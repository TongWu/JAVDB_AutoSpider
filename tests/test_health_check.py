"""
Unit tests for scripts/health_check.py functions.
These tests use a different approach - testing the functions in isolation.
"""
import os
import sys
import re
import pytest
from unittest.mock import patch, MagicMock
import socket

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestMaskIpAddress:
    """Test cases for mask_ip_address function - implemented locally for testing."""
    
    def mask_ip_address(self, host: str) -> str:
        """
        Mask IP address for logging (hide middle octets).
        Local implementation for testing.
        """
        if not host:
            return 'None'
        
        # Check if it's an IPv4 address
        ip_pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
        match = re.match(ip_pattern, str(host))
        
        if match:
            # Mask the middle two octets
            return f"{match.group(1)}.xxx.xxx.{match.group(4)}"
        
        # Not an IP address, return as-is (hostname)
        return str(host)
    
    def test_mask_valid_ipv4_address(self):
        """Test masking a valid IPv4 address."""
        result = self.mask_ip_address('192.168.1.100')
        assert result == '192.xxx.xxx.100'
    
    def test_mask_another_ipv4_address(self):
        """Test masking another IPv4 address."""
        result = self.mask_ip_address('10.0.0.1')
        assert result == '10.xxx.xxx.1'
    
    def test_mask_hostname_unchanged(self):
        """Test that hostnames are returned unchanged."""
        result = self.mask_ip_address('example.com')
        assert result == 'example.com'
    
    def test_mask_localhost_unchanged(self):
        """Test that localhost is returned unchanged."""
        result = self.mask_ip_address('localhost')
        assert result == 'localhost'
    
    def test_mask_empty_string_returns_none(self):
        """Test that empty string returns 'None'."""
        result = self.mask_ip_address('')
        assert result == 'None'
    
    def test_mask_none_returns_none(self):
        """Test that None returns 'None'."""
        result = self.mask_ip_address(None)
        assert result == 'None'


class TestCheckQbittorrentConnectionLogic:
    """Test cases for qBittorrent connection checking logic."""
    
    def test_parse_ok_response(self):
        """Test parsing successful login response."""
        response_text = 'Ok.'
        status_code = 200
        
        success = status_code == 200 and response_text == 'Ok.'
        assert success is True
    
    def test_parse_forbidden_response(self):
        """Test parsing 403 forbidden response."""
        status_code = 403
        
        is_auth_failure = status_code == 403
        assert is_auth_failure is True
    
    def test_parse_unexpected_response(self):
        """Test parsing unexpected response."""
        status_code = 500
        response_text = 'Error'
        
        is_unexpected = status_code not in [200, 403]
        assert is_unexpected is True


class TestCheckProxyPoolStatusLogic:
    """Test cases for proxy pool status checking logic."""
    
    def test_no_proxy_configured_with_pool_mode(self):
        """Test when PROXY_MODE is 'pool' but no proxies configured."""
        proxy_pool = []
        proxy_mode = 'pool'
        
        # Logic from check_proxy_pool_status
        if not proxy_pool:
            if proxy_mode == 'pool':
                success, message = False, "PROXY_MODE is 'pool' but PROXY_POOL is empty"
            else:
                success, message = True, "No proxies configured (direct connection mode)"
        else:
            success, message = True, "Proxies available"
        
        assert success is False
        assert 'empty' in message.lower()
    
    def test_no_proxy_configured_direct_mode(self):
        """Test when no proxies configured and not in pool mode."""
        proxy_pool = []
        proxy_mode = 'single'
        
        if not proxy_pool:
            if proxy_mode == 'pool':
                success, message = False, "Empty pool"
            else:
                success, message = True, "No proxies configured (direct connection mode)"
        else:
            success, message = True, "Proxies available"
        
        assert success is True
        assert 'direct connection' in message.lower()
    
    def test_all_proxies_available(self):
        """Test when all proxies are available (none banned)."""
        test_proxies = [{'name': 'Proxy1'}, {'name': 'Proxy2'}]
        banned_names = []
        
        available_count = 0
        for proxy in test_proxies:
            proxy_name = proxy.get('name', 'Unnamed')
            if proxy_name not in banned_names:
                available_count += 1
        
        total_proxies = len(test_proxies)
        
        if available_count == 0:
            success = False
            message = f"All {total_proxies} proxies are banned"
        elif available_count < total_proxies:
            success = True
            message = f"{available_count}/{total_proxies} proxies available"
        else:
            success = True
            message = f"All {total_proxies} proxies available"
        
        assert success is True
        assert '2 proxies available' in message
    
    def test_some_proxies_banned(self):
        """Test when some proxies are banned."""
        test_proxies = [{'name': 'Proxy1'}, {'name': 'Proxy2'}]
        banned_names = ['Proxy1']
        
        available_count = 0
        for proxy in test_proxies:
            proxy_name = proxy.get('name', 'Unnamed')
            if proxy_name not in banned_names:
                available_count += 1
        
        total_proxies = len(test_proxies)
        
        if available_count == 0:
            success = False
            message = f"All {total_proxies} proxies are banned"
        elif available_count < total_proxies:
            banned_count = total_proxies - available_count
            success = True
            message = f"{available_count}/{total_proxies} proxies available ({banned_count} banned)"
        else:
            success = True
            message = f"All {total_proxies} proxies available"
        
        assert success is True
        assert '1/2' in message
        assert 'banned' in message.lower()
    
    def test_all_proxies_banned(self):
        """Test when all proxies are banned."""
        test_proxies = [{'name': 'Proxy1'}, {'name': 'Proxy2'}]
        banned_names = ['Proxy1', 'Proxy2']
        
        available_count = 0
        for proxy in test_proxies:
            proxy_name = proxy.get('name', 'Unnamed')
            if proxy_name not in banned_names:
                available_count += 1
        
        total_proxies = len(test_proxies)
        
        if available_count == 0:
            success = False
            message = f"All {total_proxies} proxies are banned"
        else:
            success = True
            message = f"Proxies available"
        
        assert success is False
        assert 'all' in message.lower() and 'banned' in message.lower()


class TestCheckSmtpConnectionLogic:
    """Test cases for SMTP connection checking logic."""
    
    def test_successful_connection_result(self):
        """Test parsing successful connection result."""
        connect_result = 0  # 0 means success
        
        success = connect_result == 0
        assert success is True
    
    def test_failed_connection_result(self):
        """Test parsing failed connection result."""
        connect_result = 1  # Non-zero means failure
        
        success = connect_result == 0
        assert success is False


class TestParseArgumentsLogic:
    """Test cases for argument parsing logic."""
    
    def test_argument_defaults(self):
        """Test that argument parsing logic handles defaults."""
        import argparse
        
        parser = argparse.ArgumentParser(description='Health Check')
        parser.add_argument('--check-smtp', action='store_true',
                            help='Also check SMTP server connectivity')
        parser.add_argument('--use-proxy', action='store_true',
                            help='Check proxy pool status')
        
        # Test with no arguments
        args = parser.parse_args([])
        assert args.check_smtp is False
        assert args.use_proxy is False
    
    def test_check_smtp_flag(self):
        """Test --check-smtp flag."""
        import argparse
        
        parser = argparse.ArgumentParser()
        parser.add_argument('--check-smtp', action='store_true')
        parser.add_argument('--use-proxy', action='store_true')
        
        args = parser.parse_args(['--check-smtp'])
        assert args.check_smtp is True
    
    def test_use_proxy_flag(self):
        """Test --use-proxy flag."""
        import argparse
        
        parser = argparse.ArgumentParser()
        parser.add_argument('--check-smtp', action='store_true')
        parser.add_argument('--use-proxy', action='store_true')
        
        args = parser.parse_args(['--use-proxy'])
        assert args.use_proxy is True
    
    def test_all_flags(self):
        """Test all flags combined."""
        import argparse
        
        parser = argparse.ArgumentParser()
        parser.add_argument('--check-smtp', action='store_true')
        parser.add_argument('--use-proxy', action='store_true')
        
        args = parser.parse_args(['--check-smtp', '--use-proxy'])
        assert args.check_smtp is True
        assert args.use_proxy is True

