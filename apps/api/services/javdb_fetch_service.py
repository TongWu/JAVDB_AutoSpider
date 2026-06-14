"""JavDB page fetching for the web API surface.

Single home for "fetch a JavDB URL for the API" — the SSRF-guarded URL check,
the request-handler (proxy/CF) path, and the urllib3 simple-fetch fallback.
Extracted from explore_service so the explore one-click flow and the
video-code search flow share one fetch implementation instead of one importing
the other's private helper.
"""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urljoin

import urllib3
from fastapi import HTTPException

from apps.api.infra.security import _resolve_public_target_or_422
from apps.api.services import config_service, context
from javdb.proxy.pool import create_proxy_pool_from_config
from javdb.infra.request import (
    ProxyExhaustedError,
    create_request_handler_from_config,
)


def validate_javdb_url_or_422(url: str) -> None:
    parsed, _hostname, _resolved_ip = _resolve_public_target_or_422(url)
    if parsed.scheme.lower() != "https":
        raise HTTPException(status_code=422, detail="url must use https")


def javdb_html_looks_like_error_blob(html: str) -> bool:
    if len(html) >= 200:
        return False
    lower = html.lower()
    return "traceback (most recent call last)" in lower or "error:" in lower


def runtime_proxy_pool(config_data: Dict[str, Any]):
    proxy_pool_raw = config_data.get("PROXY_POOL", [])
    if not isinstance(proxy_pool_raw, list) or not proxy_pool_raw:
        return None
    try:
        return create_proxy_pool_from_config(
            proxy_pool_raw,
            max_failures=int(
                config_data.get("PROXY_POOL_MAX_FAILURES", 3) or 3
            ),
        )
    except Exception:
        return None


def new_request_handler(config_data: Dict[str, Any]):
    proxy_pool = runtime_proxy_pool(config_data)
    return create_request_handler_from_config(
        proxy_pool=proxy_pool,
        base_url=str(config_data.get("BASE_URL", "https://javdb.com")),
        cf_bypass_service_port=int(
            config_data.get("CF_BYPASS_SERVICE_PORT", 8000) or 8000
        ),
        cf_bypass_port_map=config_data.get("CF_BYPASS_PORT_MAP", {}) or {},
        cf_bypass_enabled=bool(config_data.get("CF_BYPASS_ENABLED", True)),
        javdb_session_cookie=str(
            config_data.get("JAVDB_SESSION_COOKIE", "") or ""
        ),
        proxy_http=config_data.get("PROXY_HTTP"),
        proxy_https=config_data.get("PROXY_HTTPS"),
        proxy_modules=config_data.get("PROXY_MODULES", ["spider"]) or ["spider"],
        proxy_mode=str(config_data.get("PROXY_MODE", "pool")),
    )


def simple_fetch_javdb_html(
    cfg: Dict[str, Any],
    url: str,
    use_cookie: bool = True,
) -> str:
    validate_javdb_url_or_422(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://javdb.com/",
    }
    cookie = str(cfg.get("JAVDB_SESSION_COOKIE", "") or "").strip()
    if use_cookie and cookie:
        headers["Cookie"] = f"_jdb_session={cookie}"

    current_url = url
    timeout = urllib3.Timeout(connect=10, read=20)
    max_redirects = 3
    for _ in range(max_redirects + 1):
        parsed, hostname, resolved_ip = _resolve_public_target_or_422(current_url)
        scheme = parsed.scheme.lower()
        if scheme != "https":
            raise HTTPException(status_code=422, detail="url must use https")
        port = parsed.port or (443 if scheme == "https" else 80)
        path_query = parsed.path or "/"
        if parsed.query:
            path_query += f"?{parsed.query}"
        default_port = 443 if scheme == "https" else 80
        headers["Host"] = hostname if port == default_port else f"{hostname}:{port}"
        pool: Any = urllib3.HTTPSConnectionPool(
            host=resolved_ip,
            port=port,
            assert_hostname=hostname,
            server_hostname=hostname,
            cert_reqs="CERT_REQUIRED",
        )

        resp = pool.request(
            "GET",
            path_query,
            headers=headers,
            timeout=timeout,
            redirect=False,
            retries=False,
            preload_content=True,
        )
        if 300 <= resp.status < 400:
            location = resp.headers.get("Location", "")
            if not location:
                raise ValueError(f"redirect without location ({resp.status})")
            next_url = urljoin(current_url, location)
            validate_javdb_url_or_422(next_url)
            current_url = next_url
            continue
        if resp.status >= 400:
            raise ValueError(f"http error {resp.status}")
        html = (resp.data or b"").decode("utf-8", errors="ignore")
        break
    else:
        raise ValueError("too many redirects")

    if not html.strip():
        raise ValueError("empty html")
    return html


def fetch_javdb_html(
    url: str,
    use_proxy: bool = True,
    use_cookie: bool = True,
) -> str:
    validate_javdb_url_or_422(url)
    cfg = config_service.load_runtime_config()
    # request.py's get_page fails closed (ProxyExhaustedError) rather than
    # exposing the real exit IP when a proxy is demanded. Honour that contract
    # here: when a proxy is required, never fall back to the direct simple-fetch
    # path, and refuse outright if the pool failed to construct.
    proxy_required = use_proxy and str(cfg.get("PROXY_MODE", "pool")) in {"pool", "single"}
    errors: list[str] = []
    try:
        handler = new_request_handler(cfg)
        if proxy_required and handler.proxy_pool is None:
            raise ProxyExhaustedError(
                proxy_name="none",
                reason="proxy pool unavailable; refusing direct fallback",
            )
        html = handler.get_page(
            url=url,
            use_proxy=use_proxy,
            use_cookie=use_cookie,
            module_name="spider",
            max_retries=3,
            use_cf_bypass=False,
        )
        if html:
            if javdb_html_looks_like_error_blob(html):
                context.logger.warning(
                    "JavDB fetch returned short error-like HTML; retrying with simple fetch"
                )
                errors.append("request_handler: error-like response")
            else:
                return html
        else:
            errors.append("request_handler returned empty")
    except Exception as exc:
        errors.append(f"request_handler: {type(exc).__name__}")

    if proxy_required:
        context.logger.warning(
            "JavDB proxy fetch failed; refusing direct fallback: %s", "; ".join(errors)
        )
        raise HTTPException(
            status_code=502, detail="Proxy fetch failed; refusing direct fallback"
        )

    try:
        html_simple = simple_fetch_javdb_html(cfg, url, use_cookie=use_cookie)
        if javdb_html_looks_like_error_blob(html_simple):
            context.logger.warning(
                "simple JavDB fetch returned short error-like HTML; treating as failure"
            )
            errors.append("simple_fetch: error-like response")
        else:
            return html_simple
    except Exception as exc:
        errors.append(f"simple_fetch: {type(exc).__name__}")

    context.logger.warning("Failed to fetch JavDB HTML: %s", "; ".join(errors))
    raise HTTPException(status_code=502, detail="Failed to fetch target page")
