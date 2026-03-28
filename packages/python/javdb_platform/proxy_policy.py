"""Shared proxy policy helpers for CLI and runtime code."""

from __future__ import annotations

import argparse
import socket
from typing import Optional, Sequence


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
