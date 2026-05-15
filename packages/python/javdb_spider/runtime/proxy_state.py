"""Per-proxy CF-bypass and login-budget state mutators (W3.4).

Extracted from :mod:`runtime.state` so the canonical mutable-globals
module stays focused on globals + getters. These helpers all touch
state owned by :mod:`runtime.state` (``login_total_budget``,
``proxies_requiring_cf_bypass``, ``always_bypass_time``,
``global_proxy_coordinator``, the locks); we access that state via
``state.<name>`` rather than re-declaring it here, so the single source
of truth stays in one place.

External callers should continue to use the ``state.proxy_needs_cf_bypass``
/ ``state.mark_proxy_cf_bypass`` / ``state.deduct_proxy_login_budget``
spellings — :mod:`runtime.state` re-exports these so the API is
backwards-compatible.
"""

from __future__ import annotations

import time
from typing import Optional

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_spider.runtime.config import (
    LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
)
from packages.python.javdb_spider.runtime import state

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Login-budget deduction (idempotent per-proxy)
# ---------------------------------------------------------------------------


def _deduct_proxy_login_budget_locked(proxy_name: str) -> int:
    """Core deduction logic. Caller must hold ``state._login_budget_lock``."""
    if proxy_name in state._login_budget_deducted_proxies:
        return 0
    if state.login_total_budget <= 0:
        state._login_budget_deducted_proxies.add(proxy_name)
        return 0

    used = state.login_attempts_per_proxy.get(proxy_name, 0)
    remaining = LOGIN_ATTEMPTS_PER_PROXY_LIMIT - used
    if remaining <= 0:
        state._login_budget_deducted_proxies.add(proxy_name)
        return 0

    # Never let the global budget drop below total attempts already spent
    # (otherwise downstream budget checks would falsely report "exhausted").
    new_budget = max(
        state.login_total_attempts,
        state.login_total_budget - remaining,
    )
    actually_deducted = state.login_total_budget - new_budget
    state.login_total_budget = new_budget
    state._login_budget_deducted_proxies.add(proxy_name)
    if actually_deducted > 0:
        logger.info(
            "Login budget reduced by %d for banned proxy '%s' "
            "(now %d, attempts so far %d)",
            actually_deducted, proxy_name, new_budget,
            state.login_total_attempts,
        )
    return actually_deducted


def deduct_proxy_login_budget(proxy_name: Optional[str]) -> int:
    """Remove a proxy's unused login attempts from the global budget.

    Called when a proxy is banned (either pre-banned at startup or banned
    during runtime).  The proxy's *remaining* per-proxy budget
    (``LOGIN_ATTEMPTS_PER_PROXY_LIMIT - login_attempts_per_proxy[proxy]``,
    floored at 0) is subtracted from :data:`state.login_total_budget` so
    that banned workers no longer reserve login credits they cannot use.

    Idempotent per ``proxy_name`` — repeated calls for the same proxy are
    no-ops, even if it gets re-banned.  Thread-safe: concurrent callers
    for different (or the same) proxy cannot double-deduct.

    Args:
        proxy_name: Name of the proxy whose budget should be reclaimed.
            ``None``/empty inputs are silently ignored.

    Returns:
        The number of login attempts deducted (``0`` when nothing changed).
    """
    if not proxy_name:
        return 0
    with state._login_budget_lock:
        return _deduct_proxy_login_budget_locked(proxy_name)


# ---------------------------------------------------------------------------
# CF-bypass marker (local + cross-instance via coordinator)
# ---------------------------------------------------------------------------


def proxy_needs_cf_bypass(proxy_name: str) -> bool:
    """Check if a proxy is still within the configured CF bypass window."""
    if state.always_bypass_time is None:
        return False

    with state._cf_bypass_lock:
        marked_at = state.proxies_requiring_cf_bypass.get(proxy_name)
        if marked_at is None:
            return False

        if state.always_bypass_time == 0:
            return True

        window_seconds = state.always_bypass_time * 60
        if time.time() - marked_at <= window_seconds:
            return True

        # Expired: fall back to direct-first behavior.
        state.proxies_requiring_cf_bypass.pop(proxy_name, None)
        return False


def mark_proxy_cf_bypass(proxy_name: str):
    """Mark a proxy for CF bypass reuse according to --always-bypass-time.

    Side effect (P1-A): when the cross-instance proxy coordinator is wired
    up, the requirement is also published to the Worker DO via
    :meth:`ProxyCoordinatorClient.mark_cf_bypass` so peer runners pick it
    up on their next ``/lease``.  This is fire-and-forget and never raises;
    when the coordinator is not configured the call is a no-op and the
    behaviour is identical to the pre-DO world.
    """
    if state.always_bypass_time is None:
        return

    with state._cf_bypass_lock:
        state.proxies_requiring_cf_bypass[proxy_name] = time.time()
    if state.always_bypass_time == 0:
        logger.info(
            "Proxy '%s' marked as requiring CF bypass for this runtime",
            proxy_name,
        )
    else:
        logger.info(
            "Proxy '%s' marked for CF bypass reuse for %d minute(s)",
            proxy_name, state.always_bypass_time,
        )

    coord = state.global_proxy_coordinator
    if coord is not None and proxy_name:
        # ``always_bypass_time``:
        #   - 0       → permanent for this session  → DO ttl_ms = 0
        #   - N (min) → expires after N minutes     → DO ttl_ms = N * 60_000
        # The Worker stores the tri-state so peers see the right window.
        ttl_ms = (
            0 if state.always_bypass_time == 0
            else int(state.always_bypass_time) * 60 * 1000
        )
        try:
            coord.mark_cf_bypass(proxy_name, ttl_ms=ttl_ms)
        except Exception:  # noqa: BLE001 — fail-open; never break local marker
            logger.warning(
                "Failed to dispatch CF bypass marker for '%s' to coordinator",
                proxy_name, exc_info=True,
            )
