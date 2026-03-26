"""
Proxy Ban Manager

Manages proxy ban records in-memory for the current session only.
When a proxy is banned, it is marked as banned for the current session.
On a new session (process restart), all proxies start fresh with no bans.

Prefers the Rust implementation (``javdb_rust_core``) when available,
falling back to the pure-Python implementation otherwise.
"""

import logging
from datetime import datetime, timedelta
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
    """Record of a proxy ban (session-scoped, in-memory only)"""
    
    def __init__(self, proxy_name: str, ban_time: datetime, unban_time: datetime, 
                 proxy_url: Optional[str] = None):
        self.proxy_name = proxy_name
        self.ban_time = ban_time
        self.unban_time = unban_time
        self.proxy_url = proxy_url
        
    def is_still_banned(self) -> bool:
        """Check if proxy is still in ban period"""
        return datetime.now() < self.unban_time
    
    def time_until_unban(self) -> timedelta:
        """Get remaining time until unban"""
        return self.unban_time - datetime.now()
    
    def days_until_unban(self) -> int:
        """Get remaining days until unban"""
        delta = self.time_until_unban()
        return max(0, delta.days)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary (without IP)"""
        return {
            'proxy_name': self.proxy_name,
            'ban_time': self.ban_time.strftime('%Y-%m-%d %H:%M:%S'),
            'unban_time': self.unban_time.strftime('%Y-%m-%d %H:%M:%S'),
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
    """
    
    BAN_DURATION_DAYS = 7  # JavDB bans IPs for 7 days
    COOLDOWN_DURATION_DAYS = 8  # 8 days cooldown (7 days ban + 1 day buffer)
    
    def __init__(self, **_kwargs):
        self.banned_proxies: Dict[str, ProxyBanRecord] = {}
        self.lock = Lock()
        logger.info("ProxyBanManager initialised (session-scoped, in-memory only)")
    
    def is_proxy_banned(self, proxy_name: str) -> bool:
        """Check if a proxy is currently banned"""
        with self.lock:
            if proxy_name not in self.banned_proxies:
                return False
            
            record = self.banned_proxies[proxy_name]
            if not record.is_still_banned():
                del self.banned_proxies[proxy_name]
                return False
            
            return True
    
    def add_ban(self, proxy_name: str, proxy_url: Optional[str] = None):
        """Add a new ban record for a proxy (in-memory only).

        Args:
            proxy_name: Name of the proxy
            proxy_url: Full proxy URL with IP (for email reporting)
        """
        with self.lock:
            if proxy_name in self.banned_proxies:
                existing = self.banned_proxies[proxy_name]
                if existing.is_still_banned():
                    logger.warning(f"Proxy '{proxy_name}' is already in ban period, not updating")
                    return
            
            ban_time = datetime.now()
            unban_time = ban_time + timedelta(days=self.BAN_DURATION_DAYS)
            
            record = ProxyBanRecord(proxy_name, ban_time, unban_time, proxy_url)
            self.banned_proxies[proxy_name] = record
            
            logger.warning(
                f"Proxy '{proxy_name}' banned until {unban_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"({self.BAN_DURATION_DAYS} days) [session-scoped]"
            )
    
    def get_banned_proxies(self) -> List[ProxyBanRecord]:
        """Get list of currently banned proxies"""
        with self.lock:
            self._cleanup_expired_bans()
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
        
        lines = [f"Currently banned proxies: {len(banned)}"]
        lines.append("")
        
        for record in sorted(banned, key=lambda r: r.unban_time):
            days_left = record.days_until_unban()
            hours_left = int(record.time_until_unban().total_seconds() / 3600) % 24
            
            line = f"  - {record.proxy_name}:"
            if include_ip and record.proxy_url:
                line += f"\n    IP: {record.proxy_url}"
            line += f"\n    Banned at: {record.ban_time.strftime('%Y-%m-%d %H:%M:%S')}"
            line += f"\n    Will unban: {record.unban_time.strftime('%Y-%m-%d %H:%M:%S')}"
            line += f"\n    Time remaining: {days_left} days {hours_left} hours"
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def get_cooldown_seconds(self) -> int:
        """Get cooldown duration in seconds (8 days)"""
        return self.COOLDOWN_DURATION_DAYS * 24 * 3600

    def _cleanup_expired_bans(self):
        """Remove expired ban records (must be called with lock held)"""
        expired = [name for name, record in self.banned_proxies.items() 
                  if not record.is_still_banned()]
        
        for proxy_name in expired:
            del self.banned_proxies[proxy_name]
            logger.info(f"Removed expired ban record for proxy '{proxy_name}'")


# Global ban manager instance
_global_ban_manager: Optional[ProxyBanManager] = None


def get_ban_manager(**_kwargs) -> ProxyBanManager:
    """Get or create the global ban manager instance (session-scoped)."""
    global _global_ban_manager
    
    if _global_ban_manager is None:
        _global_ban_manager = ProxyBanManager()
    
    return _global_ban_manager
