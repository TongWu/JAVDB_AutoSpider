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
from utils.spider_gateway import create_gateway


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

    gw = create_gateway(
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
        use_cookie=args.use_cookie,
    )
    logger.info("Fetching: %s (use_proxy=%s, use_cf_bypass=%s)", url, use_proxy, use_cf_bypass)

    html = gw.fetch_html(url)
    if html is None:
        logger.error("Failed to fetch URL (no response)")
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
