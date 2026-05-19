"""Shared proxy policy helpers for CLI and runtime code.

Mixes two concerns under one "policy" umbrella:

1. CLI / config policy — proxy mode parsing, ``--use-proxy`` / ``--no-proxy``
   flag handling, module-level proxy gating. (Original purpose of this
   module.)
2. Identity / selection policy (W3.3) — canonical proxy ID for DO
   addressing (:func:`normalize_proxy_id`) and the unified
   "is this proxy currently usable?" predicate
   (:func:`is_proxy_usable`).

Both are pure functions with no I/O. Kept together because both encode
operator-facing decisions about how proxies are addressed and selected.

Out of scope for this module (deliberately):

* ``mask_proxy_url`` — two implementations (``proxy_pool`` and
  ``javdb_core.masking``) emit subtly different log formats; merging
  them risks breaking log-grep workflows. Tracked separately.
* CF-bypass time-window predicate — depends on mutable runtime state in
  ``runtime/state.py`` and is unified there in W3.4.
* Health-score clamping — entangled with :class:`ProxyPool` instance
  state; left in the pool module until a follow-up.
"""

from __future__ import annotations

import argparse
import hashlib
import socket
from typing import Optional, Sequence

from javdb.infra.logging import get_logger

logger = get_logger(__name__)


ProxyOverride = Optional[bool]


def is_proxy_mode_disabled(mode: str) -> bool:
    """Return True when *mode* represents globally-disabled proxy."""
    return isinstance(mode, str) and mode.strip().lower() == 'none'


def normalize_proxy_mode(raw) -> str:
    """Normalise a raw PROXY_MODE value into a canonical string.

    - Python ``None``, empty string, ``'none'``/``'None'``/``'NONE'`` → ``'none'``
    - Otherwise return the stripped, lower-cased value (``'pool'``, ``'single'``).
    """
    if raw is None:
        return 'none'
    s = str(raw).strip().lower()
    return s if s else 'none'


def add_proxy_arguments(
    parser: argparse.ArgumentParser,
    *,
    use_help: str = 'Force-enable proxy for this command',
    no_help: str = 'Force-disable proxy for this command',
) -> argparse.ArgumentParser:
    """Add mutually exclusive proxy override flags to a parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--use-proxy', action='store_true', help=use_help)
    group.add_argument('--no-proxy', action='store_true', help=no_help)
    parser.set_defaults(use_proxy=False, no_proxy=False)
    return parser


def resolve_proxy_override(use_proxy_flag: bool = False, no_proxy_flag: bool = False) -> ProxyOverride:
    """Resolve CLI proxy flags into a tri-state override.

    Returns:
        True: force proxy on
        False: force proxy off
        None: auto mode, defer to PROXY_MODULES
    """
    if use_proxy_flag and no_proxy_flag:
        raise ValueError('--use-proxy and --no-proxy cannot be used together')
    if use_proxy_flag:
        return True
    if no_proxy_flag:
        return False
    return None


def should_proxy_module(
    module_name: str,
    proxy_override: ProxyOverride,
    proxy_modules: Optional[Sequence[str]],
    *,
    proxy_mode: str = 'pool',
) -> bool:
    """Decide whether a module should use proxy under the current policy.

    When *proxy_mode* is the disabled sentinel (``'none'``), this always
    returns ``False`` regardless of *proxy_override* or *proxy_modules*.
    """
    if is_proxy_mode_disabled(proxy_mode):
        return False
    if proxy_override is False:
        return False
    if proxy_override is True:
        return True
    if not proxy_modules:
        return False
    return 'all' in proxy_modules or module_name in proxy_modules


def describe_proxy_override(proxy_override: ProxyOverride) -> str:
    """Human-readable label for logging."""
    if proxy_override is True:
        return 'forced-on'
    if proxy_override is False:
        return 'forced-off'
    return 'auto'


_cf_bypass_checked: Optional[bool] = None


def is_cf_bypass_reachable(host: str = '127.0.0.1', port: int = 8000,
                           timeout: float = 2.0, *, use_cache: bool = True) -> bool:
    """Quick TCP probe to check whether the CF bypass service is listening.

    The result is cached for the process lifetime (reset on first failure
    after a success).  Pass ``use_cache=False`` to force a fresh probe.
    """
    global _cf_bypass_checked
    if use_cache and _cf_bypass_checked is not None:
        return _cf_bypass_checked
    try:
        with socket.create_connection((host, port), timeout=timeout):
            _cf_bypass_checked = True
            return True
    except OSError:
        if _cf_bypass_checked is True:
            _cf_bypass_checked = None
        else:
            _cf_bypass_checked = False
        return False


# ── Identity & selection policy (W3.3) ─────────────────────────────────────


def normalize_proxy_id(
    raw: Optional[str],
    *,
    fallback_seed: Optional[str] = None,
) -> str:
    """Deterministically normalise a proxy identifier for DO addressing.

    All runners must derive the same string for the same physical proxy,
    or the per-proxy Durable Object mutex falls apart silently. The rule
    is:

    1. If *raw* is a non-empty string, strip whitespace and use it verbatim
       (truncated to the 256-char DO ``idFromName`` limit).
    2. Otherwise, if *fallback_seed* is provided (typically ``host:port``),
       hash it to a stable 16-char hex digest and prefix ``proxy-``.
    3. Otherwise, raise :class:`ValueError` so the bug surfaces loudly.

    Returns a string of length 1..256.
    """
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed:
            return trimmed[:256]
    if fallback_seed:
        # Not security-critical — only used to bucket a configurable
        # host:port into a stable DO key. ``usedforsecurity=False``
        # silences Bandit/Ruff S324 without changing the digest, so
        # existing runners that already derived IDs keep agreeing on
        # the same DO key.
        digest = hashlib.sha1(  # noqa: S324 — see comment above
            fallback_seed.encode("utf-8"), usedforsecurity=False,
        ).hexdigest()[:16]
        derived = f"proxy-{digest}"
        logger.warning(
            "Coordinator proxy_id derived from host:port hash: %s — "
            "recommend setting `name` in PROXY_POOL_JSON so all "
            "runners agree",
            derived,
        )
        return derived
    raise ValueError("proxy_id is empty and no fallback_seed was provided")


def is_proxy_usable(proxy) -> bool:
    """Return ``True`` iff *proxy* is currently selectable for a request.

    Canonical form of the three-clause check that the proxy pool's
    selection paths previously repeated at five different call-sites:

    .. code-block:: python

        proxy.is_available and not proxy.banned and not proxy.is_in_cooldown()

    Strictly speaking the ``not banned`` clause is redundant given the
    pool's invariant (a banned proxy always has ``is_available = False``),
    but keeping it makes the predicate self-documenting and resilient to
    future drift.

    Duck-typed: accepts any object exposing the three attributes / method
    — typically :class:`ProxyInfo`, but the loose signature lets the
    Rust-backed pool reuse the same predicate without coupling to a
    specific dataclass.
    """
    return (
        proxy.is_available
        and not proxy.banned
        and not proxy.is_in_cooldown()
    )
