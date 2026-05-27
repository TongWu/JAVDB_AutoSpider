"""Per-proxy CF-bypass and login-budget state mutators (W3.4)."""

from __future__ import annotations

import time
from typing import Optional

from javdb.infra.logging import get_logger
from javdb.spider.runtime import state

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Login-budget deduction (idempotent per-proxy)
# ---------------------------------------------------------------------------


def _deduct_proxy_login_budget_locked(proxy_name: str) -> int:
    """Core deduction logic. Caller must hold ``state._login_budget_lock``."""
    previous_attempts, _ = state.get_legacy_login_budget()
    actually_deducted = state._deduct_legacy_proxy_login_budget_locked(proxy_name)
    if actually_deducted > 0:
        _, new_budget = state.get_legacy_login_budget()
        logger.info(
            "Login budget reduced by %d for banned proxy '%s' "
            "(now %d, attempts so far %d)",
            actually_deducted, proxy_name, new_budget,
            previous_attempts,
        )
    return actually_deducted


def deduct_proxy_login_budget(proxy_name: Optional[str]) -> int:
    """Remove a proxy's unused login attempts from the global budget.

    Called when a proxy is banned (either pre-banned at startup or banned
    during runtime).  The proxy's *remaining* per-proxy budget
    (``LOGIN_ATTEMPTS_PER_PROXY_LIMIT - login_attempts_per_proxy[proxy]``,
    floored at 0) is subtracted from the runtime login budget so
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


def _resolve_runtime(runtime=None):
    return runtime if runtime is not None else state.get_active_runtime()


def _proxy_ctx(runtime=None):
    runtime = _resolve_runtime(runtime)
    return runtime.proxy if runtime is not None else None


def _proxy_coordinator(runtime=None):
    runtime = _resolve_runtime(runtime)
    if runtime is not None:
        return runtime.services.proxy_coordinator
    return state.get_legacy_proxy_coordinator()


def proxy_needs_cf_bypass(proxy_name: str, *, runtime=None) -> bool:
    """Check if a proxy is still within the configured CF bypass window."""
    proxy_ctx = _proxy_ctx(runtime)
    if proxy_ctx is not None:
        always_bypass_time = proxy_ctx.always_bypass_time
        cf_bypass_lock = proxy_ctx.cf_bypass_lock
        proxies_requiring_cf_bypass = proxy_ctx.proxies_requiring_cf_bypass
    else:
        (
            always_bypass_time,
            cf_bypass_lock,
            proxies_requiring_cf_bypass,
        ) = state.get_legacy_cf_bypass_state()
    if always_bypass_time is None:
        return False

    with cf_bypass_lock:
        marked_at = proxies_requiring_cf_bypass.get(proxy_name)
        if marked_at is None:
            return False

        if always_bypass_time == 0:
            return True

        window_seconds = always_bypass_time * 60
        if time.time() - marked_at <= window_seconds:
            return True

        # Expired: fall back to direct-first behavior.
        proxies_requiring_cf_bypass.pop(proxy_name, None)
        return False


def mark_proxy_cf_bypass(proxy_name: str, *, runtime=None):
    """Mark a proxy for CF bypass reuse according to --always-bypass-time.

    Side effect (P1-A): when the cross-instance proxy coordinator is wired
    up, the requirement is also published to the Worker DO via
    :meth:`ProxyCoordinatorClient.mark_cf_bypass` so peer runners pick it
    up on their next ``/lease``.  This is fire-and-forget and never raises;
    when the coordinator is not configured the call is a no-op and the
    behaviour is identical to the pre-DO world.
    """
    proxy_ctx = _proxy_ctx(runtime)
    if proxy_ctx is not None:
        always_bypass_time = proxy_ctx.always_bypass_time
        cf_bypass_lock = proxy_ctx.cf_bypass_lock
        proxies_requiring_cf_bypass = proxy_ctx.proxies_requiring_cf_bypass
    else:
        (
            always_bypass_time,
            cf_bypass_lock,
            proxies_requiring_cf_bypass,
        ) = state.get_legacy_cf_bypass_state()
    if always_bypass_time is None:
        return

    with cf_bypass_lock:
        proxies_requiring_cf_bypass[proxy_name] = time.time()
    if always_bypass_time == 0:
        logger.info(
            "Proxy '%s' marked as requiring CF bypass for this runtime",
            proxy_name,
        )
    else:
        logger.info(
            "Proxy '%s' marked for CF bypass reuse for %d minute(s)",
            proxy_name, always_bypass_time,
        )

    coord = _proxy_coordinator(runtime)
    if coord is not None and proxy_name:
        # ``always_bypass_time``:
        #   - 0       → permanent for this session  → DO ttl_ms = 0
        #   - N (min) → expires after N minutes     → DO ttl_ms = N * 60_000
        # The Worker stores the tri-state so peers see the right window.
        ttl_ms = (
            0 if always_bypass_time == 0
            else int(always_bypass_time) * 60 * 1000
        )
        try:
            coord.mark_cf_bypass(proxy_name, ttl_ms=ttl_ms)
        except Exception:  # noqa: BLE001 — fail-open; never break local marker
            logger.warning(
                "Failed to dispatch CF bypass marker for '%s' to coordinator",
                proxy_name, exc_info=True,
            )
