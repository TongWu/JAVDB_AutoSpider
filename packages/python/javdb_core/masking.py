"""
Masking utilities for sensitive data in logs.

This module provides functions to mask sensitive information before logging.
Different masking strategies are applied based on the sensitivity level:
- Full masking (100%): passwords, keys, proxy pool JSON, cookies
- Partial masking: usernames, emails, server addresses (show first/last few chars)

Prefers the Rust implementation (``javdb_rust_core``) when available,
falling back to the pure-Python implementation otherwise.
"""

import re
from typing import Optional

try:
    from javdb_rust_core import (
        mask_full as _rust_mask_full,
        mask_partial as _rust_mask_partial,
        mask_email as _rust_mask_email,
        mask_ip_address as _rust_mask_ip_address,
        mask_proxy_url as _rust_mask_proxy_url,
        mask_error as _rust_mask_error,
    )
    RUST_MASKING_AVAILABLE = True
except ImportError:
    RUST_MASKING_AVAILABLE = False


def mask_full(value: Optional[str]) -> str:
    """
    Fully mask a sensitive value (100% hidden).
    Use for: passwords, API keys, tokens, cookies, proxy pool JSON.
    
    Args:
        value: The sensitive value to mask
        
    Returns:
        '********' if value exists, 'None' if value is None/empty
    """
    if RUST_MASKING_AVAILABLE:
        return _rust_mask_full(value)
    if not value:
        return 'None'
    return '********'


def mask_partial(value: Optional[str], show_start: int = 2, show_end: int = 2, 
                  min_masked: int = 2) -> str:
    """
    Partially mask a value, showing first and last few characters.
    Use for: usernames, emails, server addresses.
    
    Args:
        value: The value to partially mask
        show_start: Number of characters to show at the start
        show_end: Number of characters to show at the end
        min_masked: Minimum number of characters to mask (default: 2)
        
    Returns:
        Partially masked string like 'us**me' or 'None' if value is None/empty
        
    Examples:
        'username' -> 'us****me' (8 chars, mask 4)
        'tedwu' -> 'te**u' (5 chars, mask 2)
        'test' -> 't**t' (4 chars, mask 2)
        'abc' -> 'a*c' (3 chars, mask 1 - minimum possible)
    """
    if RUST_MASKING_AVAILABLE:
        return _rust_mask_partial(value, show_start, show_end, min_masked)
    if not value:
        return 'None'
    
    value_str = str(value)
    length = len(value_str)
    
    # Very short strings - mask as much as possible while showing at least 2 chars
    if length <= 2:
        return '*' * length
    if length == 3:
        return value_str[0] + '*' + value_str[-1]
    
    # Calculate how many characters would be masked with default settings
    chars_to_mask = length - show_start - show_end
    
    # Ensure we mask at least min_masked characters
    if chars_to_mask < min_masked:
        # Need to reduce visible characters to mask more
        # Prioritize reducing show_end first, then show_start
        actual_masked = min(min_masked, length - 2)  # Always show at least 2 chars
        total_visible = length - actual_masked
        
        # Distribute visible chars: prioritize start
        show_start = min(show_start, max(1, total_visible - 1))
        show_end = max(1, total_visible - show_start)
        chars_to_mask = length - show_start - show_end
    
    return value_str[:show_start] + '*' * chars_to_mask + value_str[-show_end:]


def mask_email(email: Optional[str]) -> str:
    """
    Mask an email address, showing partial local part and domain.
    
    Args:
        email: Email address to mask
        
    Returns:
        Masked email like 'us***er@ex***le.com'
    """
    if RUST_MASKING_AVAILABLE:
        return _rust_mask_email(email)
    if not email:
        return 'None'
    
    if '@' not in email:
        return mask_partial(email)
    
    local, domain = email.rsplit('@', 1)
    masked_local = mask_partial(local, show_start=2, show_end=2)
    masked_domain = mask_partial(domain, show_start=2, show_end=3)
    
    return f"{masked_local}@{masked_domain}"


def mask_ip_address(host: Optional[str]) -> str:
    """
    Mask IP address for logging (hide middle octets).
    
    Args:
        host: Hostname or IP address
        
    Returns:
        Masked IP (e.g., 192.xxx.xxx.168) or partially masked hostname
        
    Examples:
        '192.168.1.100' -> '192.xxx.xxx.100'
        'example.com' -> 'ex***om'
    """
    if RUST_MASKING_AVAILABLE:
        return _rust_mask_ip_address(host)
    if not host:
        return 'None'
    
    host_str = str(host)
    
    # Check if it's an IPv4 address
    ip_pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
    match = re.match(ip_pattern, host_str)
    
    if match:
        # Mask the middle two octets
        return f"{match.group(1)}.xxx.xxx.{match.group(4)}"
    
    # Check if it's a URL with IP
    url_ip_pattern = r'^(https?://)?(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(:\d+)?(.*)$'
    url_match = re.match(url_ip_pattern, host_str)
    
    if url_match:
        prefix = url_match.group(1) or ''
        port = url_match.group(6) or ''
        suffix = url_match.group(7) or ''
        return f"{prefix}{url_match.group(2)}.xxx.xxx.{url_match.group(5)}{port}{suffix}"
    
    # Not an IP address, partially mask as hostname
    return mask_partial(host_str, show_start=2, show_end=3)


def mask_username(username: Optional[str]) -> str:
    """
    Mask a username, showing first and last few characters.
    
    Args:
        username: Username to mask
        
    Returns:
        Masked username like 'us***er'
    """
    return mask_partial(username, show_start=2, show_end=2)


def mask_server(server: Optional[str]) -> str:
    """
    Mask a server address (hostname or IP).
    
    Args:
        server: Server address to mask
        
    Returns:
        Masked server address
    """
    if not server:
        return 'None'
    
    # Check if it's an IP address
    ip_pattern = r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
    if re.match(ip_pattern, str(server)):
        return mask_ip_address(server)
    
    # It's a hostname, partially mask it
    return mask_partial(server, show_start=2, show_end=4)


def mask_error(error_msg: Optional[str]) -> str:
    """
    Mask sensitive data inside an error/exception message while preserving
    the error type and diagnostic text.

    Scrubs proxy URLs (with embedded credentials), standalone IP addresses,
    ``port=`` ports in urllib3-style errors, and ``_jdb_session`` cookie values
    so that logs remain useful for debugging without leaking secrets.

    Args:
        error_msg: The stringified exception (``str(e)``)

    Returns:
        Error message with sensitive fragments replaced
    """
    if not error_msg:
        return 'None'

    if RUST_MASKING_AVAILABLE:
        result = _rust_mask_error(error_msg)
        return re.sub(r'\bport=(\d+)\b', 'port=****', str(result))

    result = str(error_msg)

    # 1. Mask proxy URLs  (http[s]://user:pass@host:port...)
    proxy_pattern = re.compile(
        r'https?://[^:]+:[^@]+@[^\s/:]+:\d+'
    )
    result = proxy_pattern.sub(
        lambda m: mask_proxy_url(m.group(0)),
        result,
    )

    # 2. Mask session cookie values  (_jdb_session=<value>)
    result = re.sub(
        r'(_jdb_session=)\S+',
        r'\1********',
        result,
    )

    # 3. Mask remaining bare IP addresses (skip already-masked xxx.xxx)
    def _mask_bare_ip(m: re.Match) -> str:
        if 'xxx' in m.group(0):
            return m.group(0)
        return mask_ip_address(m.group(0))

    result = re.sub(
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
        _mask_bare_ip,
        result,
    )

    result = re.sub(r'\bport=(\d+)\b', 'port=****', result)

    return result


def mask_proxy_url(proxy_url: Optional[str]) -> str:
    """
    Mask a proxy URL, hiding credentials and partially showing host.
    
    Args:
        proxy_url: Proxy URL like 'http://user:pass@host:port'
        
    Returns:
        Masked proxy URL like 'http://***@192.xxx.xxx.100:8080'
    """
    if RUST_MASKING_AVAILABLE:
        return _rust_mask_proxy_url(proxy_url)
    if not proxy_url:
        return 'None'
    
    # Parse proxy URL
    # Format: protocol://[user:pass@]host:port
    pattern = r'^(https?://)(?:([^:]+):([^@]+)@)?([^:]+):(\d+)(.*)$'
    match = re.match(pattern, str(proxy_url))
    
    if match:
        protocol = match.group(1)
        user = match.group(2)
        password = match.group(3)
        host = match.group(4)
        port = match.group(5)
        suffix = match.group(6) or ''
        
        masked_host = mask_ip_address(host)
        
        if user and password:
            return f"{protocol}***:***@{masked_host}:{port}{suffix}"
        else:
            return f"{protocol}{masked_host}:{port}{suffix}"
    
    # Couldn't parse, return partially masked
    return mask_partial(proxy_url, show_start=10, show_end=5)


# ── P0-7 helpers: header / proxy-dict redaction for DEBUG logs ──────────
#
# ``request_handler`` previously logged raw ``req_headers`` and ``req_proxies``
# at DEBUG, which exposed the JavDB session cookie and proxy credentials
# (``http://user:pass@host:port``) whenever CI dialled up the log level.
# Use the helpers below in any log-line that would otherwise stringify
# those mappings.

# Header names whose VALUE must be fully redacted in logs. Compared
# case-insensitively. Keys themselves are left visible for diagnostics.
_SENSITIVE_HEADER_NAMES = frozenset({
    'cookie',
    'set-cookie',
    'authorization',
    'proxy-authorization',
    'x-api-key',
    'x-auth-token',
    'x-csrf-token',
    'x-jdb-token',
    'x-d1-token',
    'cf-access-client-secret',
    'cf-access-client-id',
})


def mask_headers(headers) -> dict:
    """Return a copy of *headers* with sensitive values redacted.

    Accepts any mapping-like object (``dict``, ``requests.CaseInsensitiveDict``,
    ``None``).  Sensitive header names listed in ``_SENSITIVE_HEADER_NAMES``
    are replaced with ``********``; all other values are returned verbatim.

    The function never raises and degrades gracefully so callers can use
    it inline inside ``logger.debug(...)`` without try/except boilerplate.
    """
    if not headers:
        return {}
    try:
        out: dict = {}
        for raw_key, raw_value in headers.items():
            key_lc = str(raw_key).lower()
            if key_lc in _SENSITIVE_HEADER_NAMES:
                out[raw_key] = mask_full(str(raw_value) if raw_value is not None else '')
            else:
                out[raw_key] = raw_value
        return out
    except Exception:  # noqa: BLE001 — last-resort safeguard, never raise
        return {'_mask_error': '<unmaskable headers>'}


def mask_proxies(proxies) -> dict:
    """Return a copy of a ``requests``-style proxy mapping with creds hidden.

    Input is typically ``{"http": "http://user:pass@host:port", "https": ...}``
    or ``None``.  Each value is run through :func:`mask_proxy_url`; other
    types are stringified and partially masked.
    """
    if not proxies:
        return {}
    try:
        out: dict = {}
        for scheme, url in proxies.items():
            if url is None:
                out[scheme] = None
            elif isinstance(url, str):
                out[scheme] = mask_proxy_url(url)
            else:
                out[scheme] = mask_partial(str(url), show_start=4, show_end=4)
        return out
    except Exception:  # noqa: BLE001
        return {'_mask_error': '<unmaskable proxies>'}


# Environment-variable name patterns whose VALUE is always sensitive.
# Used by :func:`mask_env_value` for ad-hoc log lines that may render
# resolved config (SMTP credentials, D1 / Cloudflare Bearer tokens,
# proxy-coordinator JWTs, etc.).
_SENSITIVE_ENV_PATTERNS = (
    re.compile(r'(?i)password'),
    re.compile(r'(?i)secret'),
    re.compile(r'(?i)token'),
    re.compile(r'(?i)api[_-]?key'),
    re.compile(r'(?i)smtp[_-]?(?:user|host|server)'),
    re.compile(r'(?i)cloudflare[_-]?api'),
    re.compile(r'(?i)d1[_-]?(?:db|bearer)'),
    re.compile(r'(?i)proxy[_-]?coordinator'),
)


def mask_env_value(name: Optional[str], value: Optional[str]) -> str:
    """Return ``mask_full(value)`` when *name* matches a sensitive pattern.

    Used by request_handler / config loaders to redact resolved env values
    in DEBUG logs without having to hardcode each token name.  Falls back
    to ``value`` unchanged for non-sensitive names.
    """
    if not name:
        return str(value) if value is not None else 'None'
    for pattern in _SENSITIVE_ENV_PATTERNS:
        if pattern.search(name):
            return mask_full(value)
    return str(value) if value is not None else 'None'

