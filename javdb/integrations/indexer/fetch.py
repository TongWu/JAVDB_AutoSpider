"""Source-agnostic HTML fetch for external indexers (ADR-054 WS3)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from javdb.infra.request import create_request_handler_from_config
from javdb.proxy.pool import create_proxy_pool_from_config

DEFAULT_SOURCE_TIMEOUT_SECONDS = 10.0


def _proxy_pool(config: Dict[str, Any]):
    raw = config.get("PROXY_POOL", [])
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return create_proxy_pool_from_config(
            raw,
            max_failures=int(config.get("PROXY_POOL_MAX_FAILURES", 3) or 3),
        )
    except (TypeError, ValueError):
        return None


def _proxy_modules(config: Dict[str, Any]) -> list[str]:
    raw = config.get("PROXY_MODULES", ["spider"]) or ["spider"]
    modules = [v for v in raw if isinstance(v, str)] if isinstance(raw, (list, tuple)) else ["spider"]
    if "all" not in modules and "indexer" not in modules:
        modules.append("indexer")
    return modules


def _handler(config: Dict[str, Any]):
    return create_request_handler_from_config(
        proxy_pool=_proxy_pool(config),
        cf_bypass_enabled=False,
        proxy_http=config.get("PROXY_HTTP"),
        proxy_https=config.get("PROXY_HTTPS"),
        proxy_modules=_proxy_modules(config),
        proxy_mode=str(config.get("PROXY_MODE", "pool")),
    )


def _timeout_seconds(config: Dict[str, Any], timeout: Optional[float]) -> float:
    if timeout is None:
        raw = config.get("MAGNET_SOURCE_TIMEOUT_SECONDS", DEFAULT_SOURCE_TIMEOUT_SECONDS)
    else:
        raw = timeout
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SOURCE_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_SOURCE_TIMEOUT_SECONDS
    return value


def fetch_source_html(
    url: str,
    config: Dict[str, Any],
    *,
    use_proxy: bool = True,
    max_retries: int = 2,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """Fetch an external-indexer page. Returns HTML or ``None`` on failure."""
    return _handler(config).get_page(
        url,
        use_proxy=use_proxy,
        use_cookie=False,
        module_name="indexer",
        max_retries=max_retries,
        use_cf_bypass=False,
        validate_html=False,
        timeout=_timeout_seconds(config, timeout),
    )
