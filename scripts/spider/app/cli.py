"""Command-line argument parsing for the spider."""

import argparse
from datetime import datetime

from scripts.spider.runtime.config import PAGE_START, PAGE_END

OUTPUT_CSV = f'Javdb_TodayTitle_{datetime.now().strftime("%Y%m%d")}.csv'


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='JavDB Spider - Extract torrent links from javdb.com',
    )

    parser.add_argument('--dry-run', action='store_true',
                        help='Print items that would be written without changing CSV file')
    parser.add_argument('--output-file', type=str,
                        help='Specify output CSV file name (without changing directory)')
    parser.add_argument('--start-page', type=int, default=PAGE_START,
                        help=f'Starting page number (default: {PAGE_START})')
    parser.add_argument('--end-page', type=int, default=PAGE_END,
                        help=f'Ending page number (default: {PAGE_END})')
    parser.add_argument('--all', action='store_true',
                        help='Parse all pages until an empty page is found (ignores --end-page)')
    parser.add_argument('--ignore-history', action='store_true',
                        help='Ignore history file for reading (scrape all pages) but still save to history')
    parser.add_argument('--use-history', action='store_true',
                        help='Enable history filter for ad-hoc mode (by default, ad-hoc mode ignores history for reading)')
    parser.add_argument('--url', type=str,
                        help='Custom URL to scrape (add ?page=x for pages)')
    parser.add_argument('--phase', choices=['1', '2', 'all'], default='all',
                        help='Which phase to run: 1 (subtitle+today), 2 (today only), all (default)')
    parser.add_argument('--ignore-release-date', action='store_true',
                        help='Ignore today/yesterday tags and download all entries matching phase criteria')
    parser.add_argument('--use-proxy', action='store_true',
                        help='Enable proxy for all HTTP requests (proxy settings from config.py)')
    parser.add_argument('--always-bypass-time', type=int, nargs='?', const=0, default=None,
                        help='Minutes to keep using CF bypass after fallback success (0 or no value = whole session)')
    parser.add_argument('--from-pipeline', action='store_true',
                        help='Running from pipeline.py - use GIT_USERNAME for commits')
    parser.add_argument('--max-movies-phase1', type=int, default=None,
                        help='Limit the number of movies to process in phase 1 (for testing purposes)')
    parser.add_argument('--max-movies-phase2', type=int, default=None,
                        help='Limit the number of movies to process in phase 2 (for testing purposes)')
    parser.add_argument('--sequential', action='store_true',
                        help='Force sequential detail processing even in proxy pool mode')
    parser.add_argument('--no-rclone-filter', action='store_true',
                        help='Disable rclone inventory filter (do not skip entries already in rclone inventory)')
    parser.add_argument('--disable-all-filters', action='store_true',
                        help='Disable all filters (history, rclone inventory, release date) — process every entry from index')
    parser.add_argument('--enable-dedup', action='store_true',
                        help='Enable rclone dedup detection (compare against rclone_inventory.csv)')
    parser.add_argument('--enable-redownload', action='store_true',
                        help='Enable torrent re-download when a same-category torrent is significantly larger')
    parser.add_argument('--redownload-threshold', type=float, default=None,
                        help='Size increase threshold for re-download (default: 0.30 = 30%%)')

    return parser.parse_args()
