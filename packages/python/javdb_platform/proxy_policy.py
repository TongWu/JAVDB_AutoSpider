"""Shared proxy policy helpers for CLI and runtime code."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence


ProxyOverride = Optional[bool]


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
) -> bool:
    """Decide whether a module should use proxy under the current policy."""
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
