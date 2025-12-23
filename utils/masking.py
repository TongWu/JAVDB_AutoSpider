"""
Masking utilities for sensitive data in logs.

This module provides functions to mask sensitive information before logging.
Different masking strategies are applied based on the sensitivity level:
- Full masking (100%): passwords, keys, proxy pool JSON, cookies
- Partial masking: usernames, emails, server addresses (show first/last few chars)
"""

import re
from typing import Optional


def mask_full(value: Optional[str]) -> str:
    """
    Fully mask a sensitive value (100% hidden).
    Use for: passwords, API keys, tokens, cookies, proxy pool JSON.
    
    Args:
        value: The sensitive value to mask
        
    Returns:
        '********' if value exists, 'None' if value is None/empty
    """
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


def mask_proxy_url(proxy_url: Optional[str]) -> str:
    """
    Mask a proxy URL, hiding credentials and partially showing host.
    
    Args:
        proxy_url: Proxy URL like 'http://user:pass@host:port'
        
    Returns:
        Masked proxy URL like 'http://***@192.xxx.xxx.100:8080'
    """
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

