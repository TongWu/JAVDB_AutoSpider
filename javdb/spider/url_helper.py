"""URL helper functions with Rust acceleration (Python fallback).

Provides URL type detection, query-parameter manipulation, and filename
sanitisation for JavDB URLs. When the ``javdb_rust_core`` extension is
available the Rust implementations are used for speed; otherwise the
equivalent pure-Python logic kicks in.
"""

import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote_plus

try:
    from javdb_rust_core import (
        detect_url_type as _rs_detect_url_type,
        extract_url_identifier as _rs_extract_url_identifier,
        has_magnet_filter as _rs_has_magnet_filter,
        add_magnet_filter_to_url as _rs_add_magnet_filter_to_url,
        get_page_url as _rs_get_page_url,
        sanitize_filename_part as _rs_sanitize_filename_part,
        extract_url_part_after_javdb as _rs_extract_url_part_after_javdb,
    )
    RUST_URL_HELPER_AVAILABLE = True
except ImportError:
    RUST_URL_HELPER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Pure-Python fallback implementations
# ---------------------------------------------------------------------------

def _py_detect_url_type(url: str) -> str:
    if not url or 'javdb.com' not in url:
        return 'unknown'
    try:
        path = urlparse(url).path.strip('/')
        for prefix in ('actors', 'makers', 'publishers', 'series', 'directors', 'video_codes', 'search'):
            if path.startswith(prefix + '/') or path == prefix:
                return prefix
        return 'unknown'
    except Exception:
        return 'unknown'


def _py_extract_url_identifier(url: str):
    try:
        path = urlparse(url).path.strip('/')
        parts = path.split('/')
        if len(parts) >= 2:
            return parts[1]
    except Exception:
        pass
    return None


def _py_has_magnet_filter(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return False
        params = parse_qs(parsed.query)
        path = parsed.path.strip('/')

        if path.startswith('actors/'):
            for t_val in params.get('t', []):
                parts = t_val.split(',')
                if 'd' in parts or 'c' in parts:
                    return True
            return False
        elif path.startswith('makers/') or path.startswith('video_codes/'):
            return 'download' in params.get('f', [])
        return False
    except Exception:
        return False


def _py_add_magnet_filter_to_url(url: str) -> str:
    if _py_has_magnet_filter(url):
        return url
    try:
        parsed = urlparse(url)
        path = parsed.path.strip('/')

        if path.startswith('actors/'):
            return _py_add_actors_filter(url, parsed)
        elif path.startswith('makers/') or path.startswith('video_codes/'):
            return _py_add_download_filter(url, parsed)
        return url
    except Exception:
        return url


def _py_add_actors_filter(url, parsed):
    if not parsed.query:
        return url.rstrip('?') + '?t=d'
    params = parse_qs(parsed.query, keep_blank_values=True)
    if 't' not in params:
        return url.rstrip('&') + '&t=d'
    new_t = []
    for t_val in params['t']:
        parts = t_val.split(',')
        if 'd' not in parts and 'c' not in parts:
            new_t.append(f"{t_val},d")
        else:
            new_t.append(t_val)
    params['t'] = new_t
    flat = [(k, v) for k, vals in params.items() for v in vals]
    new_query = urlencode(flat, safe=',')
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _py_add_download_filter(url, parsed):
    if not parsed.query:
        return url.rstrip('?') + '?f=download'
    params = parse_qs(parsed.query, keep_blank_values=True)
    if 'f' not in params:
        return url.rstrip('&') + '&f=download'
    params['f'] = ['download']
    flat = [(k, v) for k, vals in params.items() for v in vals]
    new_query = urlencode(flat)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _py_get_page_url(page_num: int, base_url: str, custom_url=None) -> str:
    if custom_url:
        if page_num == 1:
            return custom_url
        sep = '&' if '?' in custom_url else '?'
        return f"{custom_url}{sep}page={page_num}"
    sep = '&' if '?' in base_url else '?'
    return f'{base_url}{sep}page={page_num}'


def _py_build_search_url(base_url: str, video_code: str, f: str = 'all') -> str:
    base = (base_url or '').rstrip('/')
    encoded_code = quote_plus((video_code or '').strip())
    if not encoded_code:
        return f"{base}/search"
    if f:
        return f"{base}/search?q={encoded_code}&f={quote_plus(str(f))}"
    return f"{base}/search?q={encoded_code}"


def _py_sanitize_filename_part(text: str, max_length: int = 30) -> str:
    if not text:
        return ''
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, '')
    text = re.sub(r'\s+', '_', text)
    text = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]', '', text)
    return text[:max_length]


def _py_extract_url_part_after_javdb(url: str) -> str:
    try:
        pos = url.find('javdb.com')
        if pos == -1:
            return 'custom_url'
        after = url[pos + len('javdb.com'):].strip('/')
        if not after:
            return 'custom_url'
        for ch in ['/', '?', '&']:
            after = after.replace(ch, '_')
        after = after.replace('=', '-')
        after = re.sub(r'_+', '_', after).strip('_')
        return after or 'custom_url'
    except Exception:
        return 'custom_url'

# ---------------------------------------------------------------------------
# Public API — Rust when available, Python otherwise
# ---------------------------------------------------------------------------

if RUST_URL_HELPER_AVAILABLE:
    def detect_url_type(url: str) -> str:
        detected = _rs_detect_url_type(url)
        if detected == 'unknown':
            # Keep parity with newly-added Python support (e.g. /search)
            return _py_detect_url_type(url)
        return detected

    extract_url_identifier = _rs_extract_url_identifier
    has_magnet_filter = _rs_has_magnet_filter
    add_magnet_filter_to_url = _rs_add_magnet_filter_to_url
    sanitize_filename_part = _rs_sanitize_filename_part
    extract_url_part_after_javdb = _rs_extract_url_part_after_javdb

    def get_page_url(page_num, base_url, custom_url=None):
        return _rs_get_page_url(page_num, base_url, custom_url)
else:
    detect_url_type = _py_detect_url_type
    extract_url_identifier = _py_extract_url_identifier
    has_magnet_filter = _py_has_magnet_filter
    add_magnet_filter_to_url = _py_add_magnet_filter_to_url
    sanitize_filename_part = _py_sanitize_filename_part
    extract_url_part_after_javdb = _py_extract_url_part_after_javdb

    def get_page_url(page_num, base_url, custom_url=None):
        return _py_get_page_url(page_num, base_url, custom_url)


def build_search_url(video_code: str, f: str = 'all', base_url: str = 'https://javdb.com') -> str:
    """Build a JavDB search URL for an exact video code lookup."""
    return _py_build_search_url(base_url, video_code, f=f)
