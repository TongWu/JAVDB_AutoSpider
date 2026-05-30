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
from typing import Callable, Optional

try:
    from javdb.rust_core import (
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


# Global ban manager instance (the Rust implementation; ADR-041 Rust-Required).
_global_ban_manager = None


def get_ban_manager(**_kwargs):
    """Get or create the global ban manager singleton (session-scoped).

    Returns the Rust ``RustProxyBanManager`` when available — this is the
    same singleton that every ``RustProxyPool`` uses internally, so ban
    state stays in sync across all components.
    """
    global _global_ban_manager

    if _global_ban_manager is None:
        if not RUST_BAN_MANAGER_AVAILABLE:
            raise RuntimeError(
                "proxy ban manager requires the Rust core (javdb.rust_core); "
                "install the wheel (`cd javdb/rust_core && maturin develop --release`) "
                "or run with --no-proxy"
            )
        _global_ban_manager = _rust_get_ban_manager()

    return _global_ban_manager
