"""
Proxy Pool Manager — powered by javdb_rust_core.

All classes and functions are provided by the high-performance Rust implementation.
The original Python implementations are preserved below as commented-out reference code.

Features:
- Multiple proxy support with automatic failover
- Passive health checking (only marks proxy as failed on actual request failures)
- Cooldown mechanism for failed proxies
- Round-robin and fallback strategies
- Comprehensive logging and statistics
"""

import re
import logging
from typing import Optional, Dict, List

from .proxy_ban_manager import get_ban_manager, ProxyBanManager

logger = logging.getLogger(__name__)

try:
    from javdb_rust_core import (
        RustProxyPool as ProxyPool,
        RustProxyInfo as ProxyInfo,
        create_proxy_pool_from_config as _rust_create_proxy_pool,
        mask_proxy_url,
    )
    RUST_PROXY_AVAILABLE = True
    logger.info("Rust proxy pool loaded - using high-performance Rust implementation")
except Exception as e:
    RUST_PROXY_AVAILABLE = False
    logger.warning("javdb_rust_core proxy module not available, using Python fallback stubs: %s", e)

    _UNAVAILABLE_MSG = (
        "javdb_rust_core is not available. Install the Rust wheel to use proxy pool. "
        "Import error: %s" % e
    )

    class ProxyPool:  # type: ignore[no-redef]
        """Stub — Rust proxy pool is unavailable."""
        def __init__(self, *args, **kwargs):
            raise RuntimeError(_UNAVAILABLE_MSG)

    class ProxyInfo:  # type: ignore[no-redef]
        """Stub — Rust proxy info is unavailable."""
        def __init__(self, *args, **kwargs):
            raise RuntimeError(_UNAVAILABLE_MSG)

    def _rust_create_proxy_pool(*args, **kwargs):
        raise RuntimeError(_UNAVAILABLE_MSG)

    def mask_proxy_url(url: Optional[str]) -> str:
        """Mask sensitive information in proxy URL for logging (Python fallback)."""
        if not url:
            return 'None'
        try:
            protocol = ''
            host_port = str(url)
            if '://' in host_port:
                protocol, host_port = host_port.split('://', 1)
                protocol += '://'
            if '@' in host_port:
                _, host_port = host_port.split('@', 1)
            ip_pattern = r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})'
            def _mask_ip(match):
                return f"{match.group(1)}.xxx.xxx.{match.group(4)}"
            host_port_masked = re.sub(ip_pattern, _mask_ip, host_port)
            return protocol + host_port_masked
        except Exception:
            return '[proxy URL masked]'


def create_proxy_pool_from_config(proxy_list_config: List[Dict],
                                   cooldown_seconds: int = 300,
                                   max_failures: int = 3,
                                   ban_log_file: str = 'reports/proxy_bans.csv'):
    """
    Create and configure a proxy pool from configuration using the Rust backend.

    Args:
        proxy_list_config: List of proxy configurations from config.py
        cooldown_seconds: Cooldown duration in seconds
        max_failures: Max failures before cooldown
        ban_log_file: Path to ban log file (default: reports/proxy_bans.csv)

    Returns:
        Configured RustProxyPool instance
    """
    rust_proxy_list = []
    for proxy_config in proxy_list_config:
        rust_proxy_dict = {}
        if 'name' in proxy_config:
            rust_proxy_dict['name'] = proxy_config['name']
        if 'http' in proxy_config and proxy_config['http']:
            rust_proxy_dict['http'] = proxy_config['http']
        if 'https' in proxy_config and proxy_config['https']:
            rust_proxy_dict['https'] = proxy_config['https']
        rust_proxy_list.append(rust_proxy_dict)

    pool = _rust_create_proxy_pool(
        rust_proxy_list,
        cooldown_seconds=cooldown_seconds,
        max_failures=max_failures,
        ban_log_file=ban_log_file
    )
    logger.debug("Created Rust proxy pool")
    return pool


# ═════════════════════════════════════════════════════════════════════════
# ORIGINAL PYTHON IMPLEMENTATIONS (commented out — kept as reference)
# ═════════════════════════════════════════════════════════════════════════

# import time
# import re
# from dataclasses import dataclass, field
# from datetime import datetime, timedelta
# from threading import Lock
#
#
# def mask_proxy_url(url: Optional[str]) -> str:
#     """Mask sensitive information (username/password/IP) in proxy URL for logging"""
#     if not url:
#         return 'None'
#     try:
#         protocol = ''
#         host_port = url
#         if '://' in url:
#             protocol, host_port = url.split('://', 1)
#             protocol += '://'
#         if '@' in host_port:
#             _, host_port = host_port.split('@', 1)
#         ip_pattern = r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})'
#         def mask_ip(match):
#             return f"{match.group(1)}.xxx.xxx.{match.group(4)}"
#         host_port_masked = re.sub(ip_pattern, mask_ip, host_port)
#         return protocol + host_port_masked
#     except Exception:
#         return '[proxy URL masked]'
#
#
# @dataclass
# class ProxyInfo:
#     """Information about a single proxy"""
#     http_url: Optional[str] = None
#     https_url: Optional[str] = None
#     name: str = "Unnamed"
#     failures: int = 0
#     last_success: Optional[datetime] = None
#     last_failure: Optional[datetime] = None
#     total_requests: int = 0
#     successful_requests: int = 0
#     is_available: bool = True
#     cooldown_until: Optional[datetime] = None
#
#     def get_proxies_dict(self) -> Dict[str, str]:
#         proxies = {}
#         if self.http_url:
#             proxies['http'] = self.http_url
#         if self.https_url:
#             proxies['https'] = self.https_url
#         return proxies
#
#     def mark_success(self):
#         self.last_success = datetime.now()
#         self.successful_requests += 1
#         self.total_requests += 1
#         self.failures = 0
#         self.is_available = True
#         self.cooldown_until = None
#
#     def mark_failure(self, cooldown_seconds: int = 300):
#         self.last_failure = datetime.now()
#         self.failures += 1
#         self.total_requests += 1
#         self.cooldown_until = datetime.now() + timedelta(seconds=cooldown_seconds)
#         self.is_available = False
#
#     def is_in_cooldown(self) -> bool:
#         if self.cooldown_until is None:
#             return False
#         return datetime.now() < self.cooldown_until
#
#     def get_success_rate(self) -> float:
#         if self.total_requests == 0:
#             return 0.0
#         return self.successful_requests / self.total_requests
#
#
# class ProxyPool:
#     """Proxy pool manager with automatic failover and passive health checking"""
#
#     def __init__(self, cooldown_seconds=300, max_failures_before_cooldown=3,
#                  ban_log_file='reports/proxy_bans.csv'):
#         self.proxies: List[ProxyInfo] = []
#         self.current_index: int = 0
#         self.cooldown_seconds = cooldown_seconds
#         self.max_failures_before_cooldown = max_failures_before_cooldown
#         self.lock = Lock()
#         self.no_proxy_mode = False
#         self.ban_manager = get_ban_manager(ban_log_file)
#
#     def add_proxy(self, http_url=None, https_url=None, name=None):
#         ...  # see git history
#
#     def add_proxies_from_list(self, proxy_list):
#         ...  # see git history
#
#     def enable_no_proxy_mode(self):
#         self.no_proxy_mode = True
#
#     def disable_no_proxy_mode(self):
#         self.no_proxy_mode = False
#
#     def _check_cooldowns(self):
#         ...  # see git history
#
#     def get_current_proxy(self):
#         ...  # see git history
#
#     def get_next_proxy(self):
#         ...  # see git history
#
#     def get_current_proxy_name(self):
#         ...  # see git history
#
#     def mark_success(self):
#         ...  # see git history
#
#     def mark_failure_and_switch(self):
#         ...  # see git history
#
#     def get_proxy_count(self):
#         return len(self.proxies)
#
#     def get_statistics(self):
#         ...  # see git history
#
#     def log_statistics(self, level=logging.INFO):
#         ...  # see git history
#
#     def get_ban_summary(self, include_ip=False):
#         return self.ban_manager.get_ban_summary(include_ip=include_ip)
#
#
# def create_proxy_pool_from_config(proxy_list_config, cooldown_seconds=300,
#                                    max_failures=3, ban_log_file='reports/proxy_bans.csv'):
#     """Factory function — see Rust implementation above"""
#     pool = ProxyPool(
#         cooldown_seconds=cooldown_seconds,
#         max_failures_before_cooldown=max_failures,
#         ban_log_file=ban_log_file
#     )
#     pool.add_proxies_from_list(proxy_list_config)
#     return pool
