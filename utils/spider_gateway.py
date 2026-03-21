"""Unified URL fetch-and-parse gateway.

Provides a single entry-point for any script to fetch a JavDB URL and
receive a fully-parsed, structured result — without needing to manually
assemble proxy pools, request handlers, or parser dispatch logic.

Usage::

    from utils.spider_gateway import create_gateway, fetch_and_parse_url

    # One-shot (uses default config.py settings):
    result = fetch_and_parse_url("https://javdb.com/v/AbC12")

    # Or reuse a gateway instance across multiple calls:
    gw = create_gateway(use_proxy=True)
    r1 = gw.fetch_and_parse("https://javdb.com/v/AbC12")
    r2 = gw.fetch_and_parse("https://javdb.com/?page=2", page_num=2)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from api.parsers import (
    parse_index_page,
    parse_detail_page,
    parse_category_page,
    parse_top_page,
    parse_tag_page,
    detect_page_type,
)
from utils.proxy_pool import create_proxy_pool_from_config
from utils.request_handler import RequestConfig, RequestHandler
from utils.rust_adapters.parser_adapter import result_to_dict

logger = logging.getLogger(__name__)

_PAGE_TYPE_USES_PAGE_NUM = frozenset({
    'index', 'category', 'makers', 'publishers', 'series',
    'directors', 'actors', 'top250', 'tags', 'search',
})

_PARSER_MAP = {
    'index':      lambda html, pn: parse_index_page(html, pn),
    'search':     lambda html, pn: parse_index_page(html, pn),
    'category':   lambda html, pn: parse_category_page(html, pn),
    'makers':     lambda html, pn: parse_category_page(html, pn),
    'publishers': lambda html, pn: parse_category_page(html, pn),
    'series':     lambda html, pn: parse_category_page(html, pn),
    'directors':  lambda html, pn: parse_category_page(html, pn),
    'actors':     lambda html, pn: parse_category_page(html, pn),
    'top250':     lambda html, pn: parse_top_page(html, pn),
    'tags':       lambda html, pn: parse_tag_page(html, pn),
    'detail':     lambda html, _pn: parse_detail_page(html),
}


@dataclass
class GatewayResult:
    """Structured return from :func:`SpiderGateway.fetch_and_parse`."""
    ok: bool
    page_type: str = ''
    url: str = ''
    html_len: int = 0
    used_proxy: bool = False
    used_cf_bypass: bool = False
    result: Optional[Dict[str, Any]] = field(default=None)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'page_type': self.page_type,
            'url': self.url,
            'html_len': self.html_len,
            'used_proxy': self.used_proxy,
            'used_cf_bypass': self.used_cf_bypass,
            'result': self.result,
            'error': self.error,
        }


def _load_config() -> dict:
    """Read config.py with safe fallbacks."""
    try:
        import config as cfg
    except ImportError:
        return {}
    keys = {
        'PROXY_POOL': None,
        'PROXY_MODE': 'single',
        'PROXY_HTTP': None,
        'PROXY_HTTPS': None,
        'PROXY_MODULES': ['all'],
        'PROXY_POOL_COOLDOWN_SECONDS': 300,
        'PROXY_POOL_MAX_FAILURES': 3,
        'BASE_URL': 'https://javdb.com',
        'CF_BYPASS_SERVICE_PORT': 8000,
        'CF_BYPASS_ENABLED': True,
        'CF_TURNSTILE_COOLDOWN': 30,
        'FALLBACK_COOLDOWN': 15,
        'JAVDB_SESSION_COOKIE': None,
        'LOGIN_PROXY_NAME': None,
        'REPORTS_DIR': 'reports',
    }
    return {k: getattr(cfg, k, v) for k, v in keys.items()}


def _build_proxy_pool(cfg: dict, ban_log_file: str):
    pool_list = cfg.get('PROXY_POOL')
    if pool_list and len(pool_list) > 0:
        entries = pool_list if cfg.get('PROXY_MODE') == 'pool' else [pool_list[0]]
        return create_proxy_pool_from_config(
            entries,
            cooldown_seconds=cfg.get('PROXY_POOL_COOLDOWN_SECONDS', 300),
            max_failures=cfg.get('PROXY_POOL_MAX_FAILURES', 3),
            ban_log_file=ban_log_file,
        )
    http_p = cfg.get('PROXY_HTTP')
    https_p = cfg.get('PROXY_HTTPS')
    if http_p or https_p:
        return create_proxy_pool_from_config(
            [{'name': 'Legacy-Proxy', 'http': http_p, 'https': https_p}],
            cooldown_seconds=cfg.get('PROXY_POOL_COOLDOWN_SECONDS', 300),
            max_failures=cfg.get('PROXY_POOL_MAX_FAILURES', 3),
            ban_log_file=ban_log_file,
        )
    return None


def _build_handler(cfg: dict, proxy_pool) -> RequestHandler:
    return RequestHandler(
        proxy_pool=proxy_pool,
        config=RequestConfig(
            base_url=cfg.get('BASE_URL', 'https://javdb.com'),
            cf_bypass_service_port=cfg.get('CF_BYPASS_SERVICE_PORT', 8000),
            cf_bypass_enabled=cfg.get('CF_BYPASS_ENABLED', True),
            cf_bypass_max_failures=3,
            cf_turnstile_cooldown=cfg.get('CF_TURNSTILE_COOLDOWN', 30),
            fallback_cooldown=cfg.get('FALLBACK_COOLDOWN', 15),
            javdb_session_cookie=cfg.get('JAVDB_SESSION_COOKIE'),
            proxy_http=cfg.get('PROXY_HTTP'),
            proxy_https=cfg.get('PROXY_HTTPS'),
            proxy_modules=cfg.get('PROXY_MODULES', ['all']),
            proxy_mode=cfg.get('PROXY_MODE', 'single'),
        ),
    )


class SpiderGateway:
    """Reusable fetch-and-parse gateway backed by a single ``RequestHandler``."""

    def __init__(
        self,
        handler: RequestHandler,
        *,
        use_proxy: bool = True,
        use_cf_bypass: bool = True,
        use_cookie: bool = False,
    ):
        self._handler = handler
        self._use_proxy = use_proxy
        self._use_cf_bypass = use_cf_bypass
        self._use_cookie = use_cookie

    def fetch_html(self, url: str) -> Optional[str]:
        """Fetch raw HTML for *url* using the configured handler."""
        return self._handler.get_page(
            url=url,
            use_cookie=self._use_cookie,
            use_proxy=self._use_proxy,
            module_name='gateway',
            max_retries=2,
            use_cf_bypass=self._use_cf_bypass,
        )

    def parse_html(self, html: str, page_num: int = 1) -> GatewayResult:
        """Detect page type and parse *html* (no fetch)."""
        page_type = detect_page_type(html)
        parser = _PARSER_MAP.get(page_type)
        if parser is None:
            return GatewayResult(
                ok=False, page_type=page_type,
                html_len=len(html),
                error=f'Unsupported page type: {page_type}',
            )
        try:
            raw = parser(html, page_num)
            return GatewayResult(
                ok=True, page_type=page_type,
                html_len=len(html),
                result=result_to_dict(raw),
            )
        except Exception as exc:
            return GatewayResult(
                ok=False, page_type=page_type,
                html_len=len(html),
                error=str(exc),
            )

    def fetch_and_parse(
        self,
        url: str,
        *,
        page_num: int = 1,
    ) -> GatewayResult:
        """Fetch *url* then auto-detect page type and parse."""
        html = self.fetch_html(url)
        if html is None:
            return GatewayResult(
                ok=False, url=url,
                used_proxy=self._use_proxy,
                used_cf_bypass=self._use_cf_bypass,
                error='Failed to fetch URL (no response)',
            )
        gr = self.parse_html(html, page_num)
        gr.url = url
        gr.used_proxy = self._use_proxy
        gr.used_cf_bypass = self._use_cf_bypass
        return gr


def create_gateway(
    *,
    use_proxy: bool = True,
    use_cf_bypass: bool = True,
    use_cookie: bool = False,
    handler: Optional[RequestHandler] = None,
) -> SpiderGateway:
    """Create a :class:`SpiderGateway` from ``config.py`` (or a custom handler)."""
    if handler is None:
        cfg = _load_config()
        reports_dir = cfg.get('REPORTS_DIR', 'reports')
        ban_log = os.path.join(reports_dir, 'proxy_bans.csv')
        os.makedirs(reports_dir, exist_ok=True)
        proxy_pool = _build_proxy_pool(cfg, ban_log) if use_proxy else None
        handler = _build_handler(cfg, proxy_pool)
    return SpiderGateway(
        handler, use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass, use_cookie=use_cookie,
    )


def create_handler_for_proxy(
    proxy_config: dict,
    *,
    ban_log_file: str = '',
) -> RequestHandler:
    """Build a ``RequestHandler`` bound to a single proxy.

    Used by parallel-worker migration scripts that need per-proxy handlers
    but want to avoid duplicating the config-assembly boilerplate.
    """
    cfg = _load_config()
    from utils.proxy_pool import create_proxy_pool_from_config

    pool = create_proxy_pool_from_config(
        [proxy_config],
        cooldown_seconds=cfg.get('PROXY_POOL_COOLDOWN_SECONDS', 300),
        max_failures=cfg.get('PROXY_POOL_MAX_FAILURES', 3),
        ban_log_file=ban_log_file,
    )
    return RequestHandler(
        proxy_pool=pool,
        config=RequestConfig(
            base_url=cfg.get('BASE_URL', 'https://javdb.com'),
            cf_bypass_service_port=cfg.get('CF_BYPASS_SERVICE_PORT', 8000),
            cf_bypass_enabled=cfg.get('CF_BYPASS_ENABLED', True),
            cf_bypass_max_failures=3,
            cf_turnstile_cooldown=cfg.get('CF_TURNSTILE_COOLDOWN', 30),
            fallback_cooldown=cfg.get('FALLBACK_COOLDOWN', 15),
            javdb_session_cookie=cfg.get('JAVDB_SESSION_COOKIE'),
            proxy_http=proxy_config.get('http'),
            proxy_https=proxy_config.get('https'),
            proxy_modules=['all'],
            proxy_mode='single',
        ),
    )


def fetch_and_parse_url(
    url: str,
    *,
    page_num: int = 1,
    use_proxy: bool = True,
    use_cf_bypass: bool = True,
    use_cookie: bool = False,
) -> GatewayResult:
    """Convenience one-shot: create a gateway, fetch, parse, return."""
    gw = create_gateway(
        use_proxy=use_proxy, use_cf_bypass=use_cf_bypass,
        use_cookie=use_cookie,
    )
    return gw.fetch_and_parse(url, page_num=page_num)
