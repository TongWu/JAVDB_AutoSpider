"""
Proxy Pool Manager for JavDB Spider

This module provides a proxy pool with automatic failover and passive health checking.
It avoids active health checks to prevent triggering JavDB's IP ban policy.

Prefers the Rust implementation (``javdb_rust_core``) when available,
falling back to the pure-Python implementation otherwise.

Features:
- Multiple proxy support with automatic failover
- Passive health checking (only marks proxy as failed on actual request failures)
- Cooldown mechanism for failed proxies
- Round-robin and fallback strategies
- Comprehensive logging and statistics
"""

import time
import logging
import random
from typing import Optional, Dict, List, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock

try:
    from javdb.rust_core import (
        RustProxyPool,
        RustProxyInfo,
        create_proxy_pool_from_config as _rust_create_proxy_pool,
        mask_proxy_url as _rust_mask_proxy_url,
    )
    RUST_PROXY_AVAILABLE = True
    RUST_IMPORT_ERROR = None
except ImportError as e:
    RUST_PROXY_AVAILABLE = False
    RUST_IMPORT_ERROR = str(e)

logger = logging.getLogger(__name__)

# Session-scoped ban: banned proxies never recover within a single run.
_SESSION_BAN_COOLDOWN = 86400 * 365

if RUST_PROXY_AVAILABLE:
    logger.debug("✅ Rust proxy pool available")
else:
    error_msg = RUST_IMPORT_ERROR if RUST_IMPORT_ERROR else 'unknown'
    # ADR-041: the proxy pool is Rust-Required. Import stays safe (ProxyInfo /
    # mask_proxy_url remain usable); constructing a pool raises in the factory.
    logger.debug(
        "Rust proxy core unavailable (ImportError: %s); proxy pool construction "
        "will raise. ProxyInfo / mask_proxy_url remain usable.",
        error_msg,
    )


def mask_proxy_url(url: Optional[str]) -> str:
    """
    Mask sensitive information (username/password/IP) in proxy URL for logging.
    Uses Rust implementation when available, otherwise pure Python.
    """
    if RUST_PROXY_AVAILABLE:
        return _rust_mask_proxy_url(url)

    if not url:
        return 'None'
    
    try:
        import re
        
        protocol = ''
        host_port = url
        
        if '://' in url:
            protocol, host_port = url.split('://', 1)
            protocol += '://'
        
        if '@' in host_port:
            _, host_port = host_port.split('@', 1)
        
        ip_pattern = r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})'
        
        def mask_ip(match):
            return f"{match.group(1)}.xxx.xxx.{match.group(4)}"
        
        host_port_masked = re.sub(ip_pattern, mask_ip, host_port)
        
        return protocol + host_port_masked
        
    except Exception:
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
    banned: bool = False
    
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
        if not self.banned:
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


def create_proxy_pool_from_config(proxy_list_config: List[Dict],
                                   max_failures: int = 3,
                                   **_kwargs):
    """Create and configure a proxy pool from configuration.

    ADR-041: the proxy pool is Rust-Required. This constructs the Rust pool and
    raises a clear error when the Rust core is unavailable (there is no Python
    pool fallback). Banned proxies never recover within a single session.

    Args:
        proxy_list_config: List of proxy configurations from config.py
        max_failures: Max failures before banning proxy for the session

    Returns:
        The Rust proxy pool (``RustProxyPool``).

    Raises:
        RuntimeError: if the Rust core (``javdb.rust_core``) is not installed.
    """
    if not RUST_PROXY_AVAILABLE:
        raise RuntimeError(
            "proxy pool requires the Rust core (javdb.rust_core); install the wheel "
            "(`cd javdb/rust_core && maturin develop --release`) or run with --no-proxy"
        )
    cooldown_seconds = _SESSION_BAN_COOLDOWN
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
    )
    logger.debug("Created Rust proxy pool")
    return pool
