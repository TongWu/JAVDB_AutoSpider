"""
Proxy Ban Manager

Manages proxy ban records in-memory for the current session only.
When a proxy is banned, it is marked as banned permanently for the
current session.  On a new session (process restart), all proxies
start fresh with no bans.

Prefers the Rust implementation (``javdb_rust_core``) when available,
falling back to the pure-Python implementation otherwise.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from threading import Lock

try:
    from javdb_rust_core import (
        RustProxyBanManager,
        get_global_ban_manager as _rust_get_ban_manager,
    )
    RUST_BAN_MANAGER_AVAILABLE = True
except ImportError:
    RUST_BAN_MANAGER_AVAILABLE = False


logger = logging.getLogger(__name__)


class ProxyBanRecord:
    """Record of a proxy ban (session-scoped, in-memory only).

    Bans are permanent for the lifetime of the current process.
    """
    
    def __init__(self, proxy_name: str, ban_time: datetime,
                 proxy_url: Optional[str] = None):
        self.proxy_name = proxy_name
        self.ban_time = ban_time
        self.proxy_url = proxy_url
        
    def to_dict(self) -> Dict:
        """Convert to dictionary (without IP)"""
        return {
            'proxy_name': self.proxy_name,
            'ban_time': self.ban_time.strftime('%Y-%m-%d %H:%M:%S'),
        }
    
    def to_dict_with_ip(self) -> Dict:
        """Convert to dictionary with IP info (for email only)"""
        data = self.to_dict()
        data['proxy_url'] = self.proxy_url if self.proxy_url else 'N/A'
        return data


class ProxyBanManager:
    """Manages proxy ban records in-memory for the current session.

    Bans are NOT persisted to disk.  Every new process / session starts
    with a clean slate — all proxies are considered unbanned.
    A ban is permanent for the lifetime of the process.
    """
    
    def __init__(self, **_kwargs):
        self.banned_proxies: Dict[str, ProxyBanRecord] = {}
        self.lock = Lock()
        logger.info("ProxyBanManager initialised (session-scoped, in-memory only)")
    
    def is_proxy_banned(self, proxy_name: str) -> bool:
        """Check if a proxy is currently banned (session-permanent)."""
        with self.lock:
            return proxy_name in self.banned_proxies
    
    def add_ban(self, proxy_name: str, proxy_url: Optional[str] = None):
        """Add a new ban record for a proxy (in-memory only).

        Args:
            proxy_name: Name of the proxy
            proxy_url: Full proxy URL with IP (for email reporting)
        """
        with self.lock:
            if proxy_name in self.banned_proxies:
                logger.debug(f"Proxy '{proxy_name}' is already banned this session, not updating")
                return
            
            record = ProxyBanRecord(proxy_name, datetime.now(), proxy_url)
            self.banned_proxies[proxy_name] = record
            
            logger.debug(
                f"Proxy '{proxy_name}' banned [session-permanent]"
            )
    
    def get_banned_proxies(self) -> List[ProxyBanRecord]:
        """Get list of currently banned proxies."""
        with self.lock:
            return list(self.banned_proxies.values())
    
    def get_ban_summary(self, include_ip: bool = False) -> str:
        """Get a formatted summary of banned proxies.

        Args:
            include_ip: Whether to include IP information (for email)

        Returns:
            Formatted string summary
        """
        banned = self.get_banned_proxies()
        
        if not banned:
            return "No proxies currently banned."
        
        lines = [f"Currently banned proxies: {len(banned)} [session-scoped]"]
        lines.append("")
        
        for record in sorted(banned, key=lambda r: r.ban_time):
            line = f"  - {record.proxy_name}:"
            if include_ip and record.proxy_url:
                line += f"\n    IP: {record.proxy_url}"
            line += f"\n    Banned at: {record.ban_time.strftime('%Y-%m-%d %H:%M:%S')}"
            line += "\n    Status: banned until process restart"
            
            lines.append(line)
        
        return "\n".join(lines)


# Global ban manager instance (may be the Rust or Python implementation)
_global_ban_manager = None


def get_ban_manager(**_kwargs):
    """Get or create the global ban manager singleton (session-scoped).

    Returns the Rust ``RustProxyBanManager`` when available — this is the
    same singleton that every ``RustProxyPool`` uses internally, so ban
    state stays in sync across all components.
    """
    global _global_ban_manager

    if _global_ban_manager is None:
        if RUST_BAN_MANAGER_AVAILABLE:
            _global_ban_manager = _rust_get_ban_manager()
        else:
            _global_ban_manager = ProxyBanManager()

    return _global_ban_manager
