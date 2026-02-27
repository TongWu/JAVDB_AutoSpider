#!/usr/bin/env python3
"""
Standalone script: fetch a URL using the same requester as the spider (proxy pool + CF bypass)
and save the HTML to a file.

Uses config.py for PROXY_POOL, CF_*, RequestConfig, etc. Request flow matches the spider's
RequestHandler.get_page() (direct/proxy, CF bypass fallback, age verification handling).

Usage:
    python scripts/fetch_page.py --url "https://javdb.com/..."
    python scripts/fetch_page.py --url "https://javdb.com/..." -o page.html
    python scripts/fetch_page.py --url "https://javdb.com/..." --no-proxy
    python scripts/fetch_page.py --url "https://javdb.com/..." --use-cookie
"""

import argparse
import logging
import os
import re
import sys
# Add project root for imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Setup logging before importing config (config may set log level)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fetch_page")

# Import after path is set
from utils.proxy_pool import create_proxy_pool_from_config
from utils.request_handler import RequestConfig, RequestHandler


def load_config():
    """Load proxy and request config from config.py."""
    import config as cfg
    return {
        "PROXY_POOL": getattr(cfg, "PROXY_POOL", None),
        "PROXY_MODE": getattr(cfg, "PROXY_MODE", "single"),
        "PROXY_HTTP": getattr(cfg, "PROXY_HTTP", None),
        "PROXY_HTTPS": getattr(cfg, "PROXY_HTTPS", None),
        "PROXY_MODULES": getattr(cfg, "PROXY_MODULES", ["all"]),
        "PROXY_POOL_COOLDOWN_SECONDS": getattr(cfg, "PROXY_POOL_COOLDOWN_SECONDS", 300),
        "PROXY_POOL_MAX_FAILURES": getattr(cfg, "PROXY_POOL_MAX_FAILURES", 3),
        "BASE_URL": getattr(cfg, "BASE_URL", "https://javdb.com"),
        "CF_BYPASS_SERVICE_PORT": getattr(cfg, "CF_BYPASS_SERVICE_PORT", 8000),
        "CF_BYPASS_ENABLED": getattr(cfg, "CF_BYPASS_ENABLED", True),
        "CF_TURNSTILE_COOLDOWN": getattr(cfg, "CF_TURNSTILE_COOLDOWN", 30),
        "FALLBACK_COOLDOWN": getattr(cfg, "FALLBACK_COOLDOWN", 15),
        "JAVDB_SESSION_COOKIE": getattr(cfg, "JAVDB_SESSION_COOKIE", None),
        "REPORTS_DIR": getattr(cfg, "REPORTS_DIR", "reports"),
    }


def default_output_path(url: str) -> str:
    """Generate a safe filename from URL (e.g. for https://javdb.com/v/abc12 -> javdb_com_v_abc12.html)."""
    # Remove scheme and take path + query
    u = url.replace("https://", "").replace("http://", "")
    # Replace non-alnum (except -_) with _
    safe = re.sub(r"[^a-zA-Z0-9\-_.]", "_", u)
    safe = safe.strip("_") or "page"
    return f"{safe}.html"


def main():
    parser = argparse.ArgumentParser(
        description="Fetch a URL with proxy pool and CF bypass (same as spider requester), save HTML."
    )
    parser.add_argument("--url", "-u", required=True, help="URL to fetch")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output HTML file path (default: auto-generated from URL in current dir)",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Do not use proxy (direct request, still with CF bypass fallback)",
    )
    parser.add_argument(
        "--use-cookie",
        action="store_true",
        help="Send JAVDB session cookie from config",
    )
    parser.add_argument(
        "--no-cf-bypass",
        action="store_true",
        help="Do not use CF bypass service (direct request only)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    url = args.url.strip()
    use_proxy = not args.no_proxy
    use_cf_bypass = not args.no_cf_bypass

    cfg = load_config()
    reports_dir = cfg["REPORTS_DIR"]
    ban_log_file = os.path.join(reports_dir, "proxy_bans.csv")
    os.makedirs(reports_dir, exist_ok=True)

    # Initialize proxy pool (same logic as spider)
    proxy_pool = None
    if use_proxy:
        if cfg["PROXY_POOL"] and len(cfg["PROXY_POOL"]) > 0:
            if cfg["PROXY_MODE"] == "pool":
                proxy_pool = create_proxy_pool_from_config(
                    cfg["PROXY_POOL"],
                    cooldown_seconds=cfg["PROXY_POOL_COOLDOWN_SECONDS"],
                    max_failures=cfg["PROXY_POOL_MAX_FAILURES"],
                    ban_log_file=ban_log_file,
                )
                logger.info("Proxy pool initialized (pool mode)")
            else:
                proxy_pool = create_proxy_pool_from_config(
                    [cfg["PROXY_POOL"][0]],
                    cooldown_seconds=cfg["PROXY_POOL_COOLDOWN_SECONDS"],
                    max_failures=cfg["PROXY_POOL_MAX_FAILURES"],
                    ban_log_file=ban_log_file,
                )
                logger.info("Single proxy initialized")
        elif cfg["PROXY_HTTP"] or cfg["PROXY_HTTPS"]:
            legacy = {
                "name": "Legacy-Proxy",
                "http": cfg["PROXY_HTTP"],
                "https": cfg["PROXY_HTTPS"],
            }
            proxy_pool = create_proxy_pool_from_config(
                [legacy],
                cooldown_seconds=cfg["PROXY_POOL_COOLDOWN_SECONDS"],
                max_failures=cfg["PROXY_POOL_MAX_FAILURES"],
                ban_log_file=ban_log_file,
            )
            logger.info("Legacy proxy initialized")
        else:
            logger.warning("Proxy requested but no PROXY_POOL or PROXY_HTTP/HTTPS in config; falling back to direct")
            use_proxy = False

    # Request config (same as spider's initialize_request_handler)
    config = RequestConfig(
        base_url=cfg["BASE_URL"],
        cf_bypass_service_port=cfg["CF_BYPASS_SERVICE_PORT"],
        cf_bypass_enabled=cfg["CF_BYPASS_ENABLED"],
        cf_bypass_max_failures=3,
        cf_turnstile_cooldown=cfg["CF_TURNSTILE_COOLDOWN"],
        fallback_cooldown=cfg["FALLBACK_COOLDOWN"],
        javdb_session_cookie=cfg["JAVDB_SESSION_COOKIE"],
        proxy_http=cfg["PROXY_HTTP"],
        proxy_https=cfg["PROXY_HTTPS"],
        proxy_modules=cfg["PROXY_MODULES"],
        proxy_mode=cfg["PROXY_MODE"],
    )

    handler = RequestHandler(proxy_pool=proxy_pool, config=config)

    # Use module_name='spider' so PROXY_MODULES (e.g. ['spider'] or ['all']) applies
    logger.info("Fetching: %s (use_proxy=%s, use_cf_bypass=%s)", url, use_proxy, use_cf_bypass)
    html = handler.get_page(
        url,
        use_cookie=args.use_cookie,
        use_proxy=use_proxy,
        module_name="spider",
        max_retries=3,
        use_cf_bypass=use_cf_bypass,
    )

    if html is None:
        logger.error("Failed to fetch URL")
        sys.exit(1)

    out_path = args.output or default_output_path(url)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Saved %d bytes to %s", len(html), out_path)
    print(out_path)


if __name__ == "__main__":
    main()
