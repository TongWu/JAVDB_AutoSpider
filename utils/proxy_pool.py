"""
Proxy Pool Manager for JavDB Spider

This module provides a proxy pool with automatic failover and passive health checking.
It avoids active health checks to prevent triggering JavDB's IP ban policy.

Features:
- Multiple proxy support with automatic failover
- Passive health checking (only marks proxy as failed on actual request failures)
- Cooldown mechanism for failed proxies
- Round-robin and fallback strategies
- Comprehensive logging and statistics
"""

import time
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock

from .proxy_ban_manager import get_ban_manager, ProxyBanManager


logger = logging.getLogger(__name__)


def mask_proxy_url(url: Optional[str]) -> str:
    """
    Mask sensitive information (username/password/IP) in proxy URL for logging
    
    Args:
        url: Proxy URL that may contain credentials and IP
        
    Returns:
        Masked URL with credentials and IP middle parts hidden
        
    Examples:
        http://user:pass@123.45.67.89:8080 -> http://***:***@123.xxx.xxx.89:8080
        http://123.45.67.89:8080 -> http://123.xxx.xxx.89:8080
        http://proxy.example.com:8080 -> http://proxy.example.com:8080
        None -> 'None'
    """
    if not url:
        return 'None'
    
    try:
        import re
        
        protocol = ''
        host_port = url
        credentials_masked = False
        
        # Extract protocol
        if '://' in url:
            protocol, host_port = url.split('://', 1)
            protocol += '://'
        
        # Check if URL contains credentials (username:password@)
        if '@' in host_port:
            creds, host_port = host_port.split('@', 1)
            protocol += '***:***@'
            credentials_masked = True
        
        # Mask IP address (mask middle two octets)
        # Match IPv4 pattern: xxx.xxx.xxx.xxx
        ip_pattern = r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})'
        
        def mask_ip(match):
            return f"{match.group(1)}.xxx.xxx.{match.group(4)}"
        
        host_port_masked = re.sub(ip_pattern, mask_ip, host_port)
        
        return protocol + host_port_masked
        
    except Exception:
        # If anything goes wrong, return a safe default
        return '[proxy URL masked]'


@dataclass
class ProxyInfo:
    """Information about a single proxy"""
    http_url: Optional[str] = None
    https_url: Optional[str] = None
    name: str = "Unnamed"
    failures: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    total_requests: int = 0
    successful_requests: int = 0
    is_available: bool = True
    cooldown_until: Optional[datetime] = None
    
    def get_proxies_dict(self) -> Dict[str, str]:
        """Get proxies dictionary for requests library"""
        proxies = {}
        if self.http_url:
            proxies['http'] = self.http_url
        if self.https_url:
            proxies['https'] = self.https_url
        return proxies
    
    def mark_success(self):
        """Mark this proxy as successful"""
        self.last_success = datetime.now()
        self.successful_requests += 1
        self.total_requests += 1
        self.failures = 0
        self.is_available = True
        self.cooldown_until = None
        
    def mark_failure(self, cooldown_seconds: int = 300):
        """Mark this proxy as failed and put it in cooldown"""
        self.last_failure = datetime.now()
        self.failures += 1
        self.total_requests += 1
        self.cooldown_until = datetime.now() + timedelta(seconds=cooldown_seconds)
        self.is_available = False
        
    def is_in_cooldown(self) -> bool:
        """Check if proxy is currently in cooldown period"""
        if self.cooldown_until is None:
            return False
        return datetime.now() < self.cooldown_until
    
    def get_success_rate(self) -> float:
        """Calculate success rate (0.0 to 1.0)"""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests


class ProxyPool:
    """
    Proxy pool manager with automatic failover and passive health checking
    
    This class manages multiple proxies and automatically switches to another proxy
    when the current one fails. It uses passive health checking to avoid triggering
    rate limits or bans from the target website.
    """
    
    def __init__(self, cooldown_seconds: int = 300, max_failures_before_cooldown: int = 3,
                 ban_log_file: str = 'reports/proxy_bans.csv'):
        """
        Initialize proxy pool
        
        Args:
            cooldown_seconds: How long to wait before retrying a failed proxy (default: 691200s = 8 days)
            max_failures_before_cooldown: Max consecutive failures before putting proxy in cooldown
            ban_log_file: Path to ban log file
        """
        self.proxies: List[ProxyInfo] = []
        self.current_index: int = 0
        self.cooldown_seconds = cooldown_seconds
        self.max_failures_before_cooldown = max_failures_before_cooldown
        self.lock = Lock()
        self.no_proxy_mode = False  # If True, no proxy will be used
        
        # Initialize ban manager
        self.ban_manager = get_ban_manager(ban_log_file)
        
    def add_proxy(self, http_url: Optional[str] = None, https_url: Optional[str] = None, 
                  name: Optional[str] = None) -> None:
        """
        Add a proxy to the pool (checks ban status first)
        
        Args:
            http_url: HTTP proxy URL (e.g., 'http://proxy.example.com:8080')
            https_url: HTTPS proxy URL (e.g., 'http://proxy.example.com:8080')
            name: Friendly name for this proxy
        """
        if not http_url and not https_url:
            logger.warning("Attempted to add proxy with no URLs, skipping")
            return
            
        if name is None:
            name = f"Proxy-{len(self.proxies) + 1}"
        
        # Check if proxy is banned
        if self.ban_manager.is_proxy_banned(name):
            logger.warning(f"Proxy '{name}' is currently banned, skipping")
            return
            
        proxy_info = ProxyInfo(
            http_url=http_url,
            https_url=https_url,
            name=name
        )
        
        self.proxies.append(proxy_info)
        # Mask sensitive credentials in log output
        masked_http = mask_proxy_url(http_url)
        masked_https = mask_proxy_url(https_url)
        logger.info(f"Added proxy '{name}' to pool (HTTP: {masked_http}, HTTPS: {masked_https})")
        
    def add_proxies_from_list(self, proxy_list: List[Dict]) -> None:
        """
        Add multiple proxies from a list
        
        Args:
            proxy_list: List of dictionaries with keys 'http', 'https', and optionally 'name'
        """
        for i, proxy_config in enumerate(proxy_list):
            http_url = proxy_config.get('http')
            https_url = proxy_config.get('https')
            name = proxy_config.get('name', f"Proxy-{i + 1}")
            self.add_proxy(http_url, https_url, name)
            
    def enable_no_proxy_mode(self) -> None:
        """Enable no-proxy mode (direct connection)"""
        self.no_proxy_mode = True
        logger.info("No-proxy mode enabled (direct connection)")
        
    def disable_no_proxy_mode(self) -> None:
        """Disable no-proxy mode"""
        self.no_proxy_mode = False
        logger.info("No-proxy mode disabled")
        
    def _check_cooldowns(self) -> None:
        """Check and update cooldown status for all proxies"""
        for proxy in self.proxies:
            if proxy.is_in_cooldown():
                # Still in cooldown
                continue
            elif not proxy.is_available and not proxy.is_in_cooldown():
                # Cooldown period ended, make proxy available again
                proxy.is_available = True
                logger.info(f"Proxy '{proxy.name}' cooldown period ended, marked as available")
                
    def get_current_proxy(self) -> Optional[Dict[str, str]]:
        """
        Get the current active proxy (without rotating)
        
        Returns:
            Dictionary with 'http' and 'https' keys for requests library, or None if no proxy available
        """
        if self.no_proxy_mode:
            return None
            
        if not self.proxies:
            logger.warning("No proxies configured in pool")
            return None
            
        with self.lock:
            self._check_cooldowns()
            
            # Try to find an available proxy starting from current_index
            attempts = 0
            while attempts < len(self.proxies):
                proxy = self.proxies[self.current_index]
                
                if proxy.is_available and not proxy.is_in_cooldown():
                    return proxy.get_proxies_dict()
                    
                # This proxy is not available, try next
                self.current_index = (self.current_index + 1) % len(self.proxies)
                attempts += 1
            
            # All proxies are unavailable or in cooldown
            logger.warning("All proxies are unavailable or in cooldown")
            return None
    
    def get_next_proxy(self) -> Optional[Dict[str, str]]:
        """
        Get the next available proxy in round-robin fashion.
        Each call will rotate to a different proxy, distributing load evenly.
        
        Returns:
            Dictionary with 'http' and 'https' keys for requests library, or None if no proxy available
        """
        if self.no_proxy_mode:
            return None
            
        if not self.proxies:
            logger.warning("No proxies configured in pool")
            return None
            
        with self.lock:
            self._check_cooldowns()
            
            # Count available proxies
            available_count = sum(1 for p in self.proxies if p.is_available and not p.is_in_cooldown())
            if available_count == 0:
                logger.warning("All proxies are unavailable or in cooldown")
                return None
            
            # Find next available proxy in round-robin fashion
            attempts = 0
            while attempts < len(self.proxies):
                # Move to next proxy first (rotate before returning)
                self.current_index = (self.current_index + 1) % len(self.proxies)
                proxy = self.proxies[self.current_index]
                
                if proxy.is_available and not proxy.is_in_cooldown():
                    logger.debug(f"Round-robin selected proxy: {proxy.name}")
                    return proxy.get_proxies_dict()
                    
                attempts += 1
            
            # Should not reach here if available_count > 0
            logger.warning("Unexpected: no available proxy found after rotation")
            return None
            
    def get_current_proxy_name(self) -> str:
        """Get the name of current active proxy"""
        if self.no_proxy_mode:
            return "No-Proxy (Direct)"
            
        if not self.proxies:
            return "None"
            
        proxy = self.proxies[self.current_index]
        return proxy.name
        
    def mark_success(self) -> None:
        """Mark the current proxy as successful"""
        if self.no_proxy_mode or not self.proxies:
            return
            
        with self.lock:
            proxy = self.proxies[self.current_index]
            proxy.mark_success()
            logger.debug(f"Proxy '{proxy.name}' marked as successful (success rate: {proxy.get_success_rate():.1%})")
            
    def mark_failure_and_switch(self) -> bool:
        """
        Mark current proxy as failed and switch to next available proxy
        
        Returns:
            True if switched to another proxy, False if no other proxy available
        """
        if self.no_proxy_mode or not self.proxies:
            return False
            
        with self.lock:
            current_proxy = self.proxies[self.current_index]
            
            # Mark as failure
            if current_proxy.failures >= self.max_failures_before_cooldown:
                # Record ban in ban manager
                proxy_url = current_proxy.http_url or current_proxy.https_url
                self.ban_manager.add_ban(current_proxy.name, proxy_url)
                
                current_proxy.mark_failure(self.cooldown_seconds)
                logger.warning(
                    f"Proxy '{current_proxy.name}' reached {current_proxy.failures} failures, "
                    f"putting in cooldown for {self.cooldown_seconds}s (8 days)"
                )
            else:
                # Just increment failure counter but don't put in cooldown yet
                current_proxy.failures += 1
                current_proxy.total_requests += 1
                current_proxy.last_failure = datetime.now()
                logger.warning(
                    f"Proxy '{current_proxy.name}' failed ({current_proxy.failures}/{self.max_failures_before_cooldown})"
                )
            
            # Try to switch to next available proxy
            original_index = self.current_index
            attempts = 0
            
            while attempts < len(self.proxies):
                self.current_index = (self.current_index + 1) % len(self.proxies)
                next_proxy = self.proxies[self.current_index]
                
                if next_proxy.is_available and not next_proxy.is_in_cooldown():
                    logger.info(f"Switched from '{current_proxy.name}' to '{next_proxy.name}'")
                    return True
                    
                attempts += 1
            
            # Could not find any available proxy, restore original index
            self.current_index = original_index
            logger.error("Failed to switch proxy: all proxies are unavailable")
            return False
            
    def get_statistics(self) -> Dict:
        """Get statistics about proxy pool usage"""
        if not self.proxies:
            return {
                'total_proxies': 0,
                'available_proxies': 0,
                'in_cooldown': 0,
                'no_proxy_mode': self.no_proxy_mode,
                'proxies': []
            }
            
        with self.lock:
            self._check_cooldowns()
            
            available = sum(1 for p in self.proxies if p.is_available and not p.is_in_cooldown())
            in_cooldown = sum(1 for p in self.proxies if p.is_in_cooldown())
            
            proxy_stats = []
            for i, proxy in enumerate(self.proxies):
                stats = {
                    'name': proxy.name,
                    'is_current': i == self.current_index,
                    'is_available': proxy.is_available,
                    'in_cooldown': proxy.is_in_cooldown(),
                    'total_requests': proxy.total_requests,
                    'successful_requests': proxy.successful_requests,
                    'success_rate': f"{proxy.get_success_rate():.1%}",
                    'consecutive_failures': proxy.failures,
                    'last_success': proxy.last_success.strftime('%Y-%m-%d %H:%M:%S') if proxy.last_success else 'Never',
                    'last_failure': proxy.last_failure.strftime('%Y-%m-%d %H:%M:%S') if proxy.last_failure else 'Never'
                }
                
                if proxy.cooldown_until:
                    remaining = (proxy.cooldown_until - datetime.now()).total_seconds()
                    stats['cooldown_remaining'] = f"{int(remaining)}s"
                    
                proxy_stats.append(stats)
            
            return {
                'total_proxies': len(self.proxies),
                'available_proxies': available,
                'in_cooldown': in_cooldown,
                'no_proxy_mode': self.no_proxy_mode,
                'proxies': proxy_stats
            }
            
    def log_statistics(self, level: int = logging.INFO) -> None:
        """Log proxy pool statistics"""
        stats = self.get_statistics()
        
        logger.log(level, "=" * 50)
        logger.log(level, "PROXY POOL STATISTICS")
        logger.log(level, "=" * 50)
        logger.log(level, f"Total proxies: {stats['total_proxies']}")
        logger.log(level, f"Available proxies: {stats['available_proxies']}")
        logger.log(level, f"In cooldown: {stats['in_cooldown']}")
        logger.log(level, f"No-proxy mode: {stats['no_proxy_mode']}")
        logger.log(level, "")
        
        if stats['proxies']:
            logger.log(level, "Individual proxy statistics:")
            for proxy_stat in stats['proxies']:
                status = "ACTIVE" if proxy_stat['is_current'] else "Standby"
                if proxy_stat['in_cooldown']:
                    status += f" (Cooldown: {proxy_stat.get('cooldown_remaining', 'N/A')})"
                elif not proxy_stat['is_available']:
                    status += " (Unavailable)"
                    
                logger.log(level, f"  [{status}] {proxy_stat['name']}:")
                logger.log(level, f"    - Total requests: {proxy_stat['total_requests']}")
                logger.log(level, f"    - Success rate: {proxy_stat['success_rate']}")
                logger.log(level, f"    - Consecutive failures: {proxy_stat['consecutive_failures']}")
                logger.log(level, f"    - Last success: {proxy_stat['last_success']}")
                logger.log(level, f"    - Last failure: {proxy_stat['last_failure']}")
        
        logger.log(level, "=" * 50)
    
    def get_ban_summary(self, include_ip: bool = False) -> str:
        """
        Get ban summary from ban manager
        
        Args:
            include_ip: Whether to include IP information (for email)
        
        Returns:
            Formatted string summary
        """
        return self.ban_manager.get_ban_summary(include_ip=include_ip)


def create_proxy_pool_from_config(proxy_list_config: List[Dict], 
                                   cooldown_seconds: int = 300,
                                   max_failures: int = 3,
                                   ban_log_file: str = 'reports/proxy_bans.csv') -> ProxyPool:
    """
    Create and configure a proxy pool from configuration
    
    Args:
        proxy_list_config: List of proxy configurations from config.py
        cooldown_seconds: Cooldown duration in seconds
        max_failures: Max failures before cooldown
        ban_log_file: Path to ban log file (default: reports/proxy_bans.csv)
        
    Returns:
        Configured ProxyPool instance
    """
    pool = ProxyPool(
        cooldown_seconds=cooldown_seconds,
        max_failures_before_cooldown=max_failures,
        ban_log_file=ban_log_file
    )
    
    pool.add_proxies_from_list(proxy_list_config)
    
    return pool

