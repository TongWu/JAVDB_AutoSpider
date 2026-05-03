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
from typing import Callable, Dict, List, Optional
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


# P1-A — cross-runner ban dispatcher.  ``state.setup_proxy_coordinator``
# registers a hook here that fires
# ``ProxyCoordinatorClient.mark_proxy_banned(name)`` when the local ban
# manager records a new ban; this propagates the ban into the Worker
# Durable Object so peer runners pick it up via their next ``/lease``.
#
# The hook is intentionally module-level (not threaded through ProxyBanManager
# constructors) because:
#   1. The Rust ban manager bypasses the Python ``add_ban`` entry point, so
#      a constructor-injected hook would miss Rust-mediated bans;
#   2. Tests can simply ``set_remote_ban_hook(None)`` to short-circuit;
#   3. Fail-open is preserved: when the coordinator is not configured the
#      hook stays ``None`` and behaviour matches the pre-coordinator world.
_remote_ban_hook: Optional[Callable[[str], None]] = None
_remote_unban_hook: Optional[Callable[[str], None]] = None


def set_remote_ban_hook(hook: Optional[Callable[[str], None]]) -> None:
    """Register the cross-runner ban dispatcher.  Pass ``None`` to clear."""
    global _remote_ban_hook
    _remote_ban_hook = hook


def set_remote_unban_hook(hook: Optional[Callable[[str], None]]) -> None:
    """Register the cross-runner unban dispatcher.  Pass ``None`` to clear."""
    global _remote_unban_hook
    _remote_unban_hook = hook


def _dispatch_remote_ban(proxy_name: str) -> None:
    """Best-effort fire of the registered remote-ban hook.

    Never raises; failures are logged at WARNING and otherwise ignored so
    that local ban bookkeeping is never blocked by a coordinator outage.
    Idempotency is delegated to the hook implementation (``mark_proxy_banned``
    is naturally idempotent: the Worker takes the max TTL of concurrent bans).
    """
    hook = _remote_ban_hook
    if hook is None or not proxy_name:
        return
    try:
        hook(proxy_name)
    except Exception:  # noqa: BLE001 — must NEVER escape the ban path
        logger.warning(
            "Remote ban hook for '%s' failed; ban remains local-only",
            proxy_name, exc_info=True,
        )


def _dispatch_remote_unban(proxy_name: str) -> None:
    """Best-effort fire of the registered remote-unban hook.  Never raises."""
    hook = _remote_unban_hook
    if hook is None or not proxy_name:
        return
    try:
        hook(proxy_name)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Remote unban hook for '%s' failed; unban remains local-only",
            proxy_name, exc_info=True,
        )


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
        newly_banned = False
        with self.lock:
            if proxy_name in self.banned_proxies:
                logger.debug(f"Proxy '{proxy_name}' is already banned this session, not updating")
            else:
                record = ProxyBanRecord(proxy_name, datetime.now(), proxy_url)
                self.banned_proxies[proxy_name] = record
                newly_banned = True
                logger.debug(
                    f"Proxy '{proxy_name}' banned [session-permanent]"
                )

        # P1-A — fire the cross-runner ban dispatcher OUTSIDE the lock so a
        # slow / unavailable coordinator can never block the ban path.  Only
        # dispatched when the ban is *newly* recorded; repeats are idempotent
        # but firing on every call would amplify queue pressure pointlessly.
        if newly_banned:
            _dispatch_remote_ban(proxy_name)
    
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
