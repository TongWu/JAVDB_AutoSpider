import requests
import csv
import time
import re
import logging
import os
import argparse
import sys
from bs4 import BeautifulSoup
from bs4.element import Tag
from urllib.parse import urljoin
from datetime import datetime

# Import utility functions
from utils.history_manager import load_parsed_movies_history, save_parsed_movie_to_history, should_process_movie, \
    determine_torrent_types, get_missing_torrent_types, validate_history_file
from utils.parser import parse_index, parse_detail
from utils.magnet_extractor import extract_magnets

# Import unified configuration
try:
    from config import (
        BASE_URL, START_PAGE, END_PAGE,
        DAILY_REPORT_DIR, AD_HOC_DIR, PARSED_MOVIES_CSV,
        SPIDER_LOG_FILE, LOG_LEVEL, DETAIL_PAGE_SLEEP, PAGE_SLEEP, MOVIE_SLEEP,
        JAVDB_SESSION_COOKIE, PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    BASE_URL = 'https://javdb.com'
    START_PAGE = 1
    END_PAGE = 20
    DAILY_REPORT_DIR = 'Daily Report'
    AD_HOC_DIR = 'Ad Hoc'
    PARSED_MOVIES_CSV = 'parsed_movies_history.csv'
    SPIDER_LOG_FILE = 'logs/Javdb_Spider.log'
    LOG_LEVEL = 'INFO'
    DETAIL_PAGE_SLEEP = 5
    PAGE_SLEEP = 2
    MOVIE_SLEEP = 1
    JAVDB_SESSION_COOKIE = None
    PHASE2_MIN_RATE = 4.0
    PHASE2_MIN_COMMENTS = 100

os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# Configure logging
from utils.logging_config import setup_logging, get_logger
setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Global set to track parsed links
parsed_links = set()

# Generate output CSV filename
OUTPUT_CSV = f'Javdb_TodayTitle_{datetime.now().strftime("%Y%m%d")}.csv'


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='JavDB Spider - Extract torrent links from javdb.com')

    parser.add_argument('--dry-run', action='store_true',
                        help='Print items that would be written without changing CSV file')

    parser.add_argument('--output-file', type=str,
                        help='Specify output CSV file name (without changing directory)')

    parser.add_argument('--start-page', type=int, default=START_PAGE,
                        help=f'Starting page number (default: {START_PAGE})')

    parser.add_argument('--end-page', type=int, default=END_PAGE,
                        help=f'Ending page number (default: {END_PAGE})')

    parser.add_argument('--all', action='store_true',
                        help='Parse all pages until an empty page is found (ignores --end-page)')

    parser.add_argument('--ignore-history', action='store_true',
                        help='Ignore history file and scrape all pages from start to end')

    parser.add_argument('--url', type=str,
                        help='Custom URL to scrape (add ?page=x for pages)')

    parser.add_argument('--phase', choices=['1', '2', 'all'], default='all',
                        help='Which phase to run: 1 (subtitle+today), 2 (today only), all (default)')

    parser.add_argument('--ignore-release-date', action='store_true',
                        help='Ignore today/yesterday tags and download all entries matching phase criteria (subtitle for phase1, quality for phase2)')

    return parser.parse_args()


def ensure_daily_report_dir():
    """Ensure the Daily Report directory exists"""
    if not os.path.exists(DAILY_REPORT_DIR):
        os.makedirs(DAILY_REPORT_DIR)
        logger.info(f"Created directory: {DAILY_REPORT_DIR}")


def get_page(url, session=None, use_cookie=False):
    """Fetch a webpage with proper headers and age verification bypass"""
    if session is None:
        session = requests.Session()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',  # 移除br压缩
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    # Only add cookie if use_cookie is True and JAVDB_SESSION_COOKIE is configured
    if use_cookie and JAVDB_SESSION_COOKIE:
        headers['Cookie'] = f'_jdb_session={JAVDB_SESSION_COOKIE}'

    try:
        logger.debug(f"Fetching URL: {url}")
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        logger.debug(f"Successfully fetched URL: {url}")
        
        html_content = response.text
        
        # Check for age verification modal and bypass if needed
        soup = BeautifulSoup(html_content, 'html.parser')
        age_modal = soup.find('div', class_='modal is-active over18-modal')
        
        if age_modal:
            logger.debug("Age verification modal detected, attempting to bypass...")
            
            # Find age verification link
            age_links = age_modal.find_all('a', href=True)
            for link in age_links:
                if 'over18' in link.get('href', ''):
                    age_url = urljoin(BASE_URL, link.get('href'))
                    logger.debug(f"Found age verification link: {age_url}")
                    
                    # Access age verification link
                    age_response = session.get(age_url, headers=headers, timeout=30)
                    if age_response.status_code == 200:
                        logger.debug("Successfully bypassed age verification")
                        # Re-fetch the original page
                        final_response = session.get(url, headers=headers, timeout=30)
                        if final_response.status_code == 200:
                            return final_response.text
                        else:
                            logger.warning(f"Failed to get final page after age verification: {final_response.status_code}")
                            return final_response.text
                    else:
                        logger.warning(f"Failed to bypass age verification: {age_response.status_code}")
                        break
            
            logger.warning("Could not find or access age verification link")
        else:
            logger.debug("No age verification modal detected")
        
        return html_content
        
    except requests.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


def get_page_url(page_num, phase=1, custom_url=None):
    """Generate URL for a specific page number and phase"""
    if custom_url:
        # If custom URL is provided, just add page parameter
        if page_num == 1:
            return custom_url
        else:
            separator = '&' if '?' in custom_url else '?'
            return f"{custom_url}{separator}page={page_num}"

    if BASE_URL.endswith('.com'):
        return f'{BASE_URL}/?page={page_num}'
    else:
        return f'{BASE_URL}&page={page_num}'


def write_csv(rows, csv_path, fieldnames, dry_run=False, append_mode=False):
    """Write results to CSV file or print if dry-run"""
    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(rows)} entries to {csv_path}")
        logger.info("[DRY RUN] Sample entries:")
        for i, row in enumerate(rows[:3]):  # Show first 3 entries
            logger.info(f"[DRY RUN] Entry {i + 1}: {row['video_code']} (Page {row['page']})")
        if len(rows) > 3:
            logger.info(f"[DRY RUN] ... and {len(rows) - 3} more entries")
        return

    # Determine if we need to write header (only if file doesn't exist or not in append mode)
    write_header = not os.path.exists(csv_path) or not append_mode
    
    mode = 'a' if append_mode else 'w'
    logger.debug(f"[FINISH] Writing results to {csv_path} (mode: {mode})")
    
    with open(csv_path, mode, newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def should_include_torrent_in_csv(href, history_data, magnet_links):
    """Check if torrent categories should be included in CSV based on history"""
    if not history_data or href not in history_data:
        # New movie, include all found torrents
        return True

    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)

    # Check if any current torrent types are not in history
    for torrent_type in current_torrent_types:
        if torrent_type not in history_torrent_types:
            return True

    # All current torrent types already exist in history
    return False


def create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links, history_data):
    """Create CSV row with torrent categories, marking downloaded ones with [DOWNLOADED PREVIOUSLY]"""
    if not history_data or href not in history_data:
        # New movie, apply preference rules to current magnet links
        row = {
            'href': href,
            'video_code': entry['video_code'],
            'page': page_num,
            'actor': actor_info,
            'rate': entry['rate'],
            'comment_number': entry['comment_number'],
            'hacked_subtitle': magnet_links['hacked_subtitle'],
            'hacked_no_subtitle': '',
            'subtitle': magnet_links['subtitle'],
            'no_subtitle': '',
            'size_hacked_subtitle': magnet_links['size_hacked_subtitle'],
            'size_hacked_no_subtitle': '',
            'size_subtitle': magnet_links['size_subtitle'],
            'size_no_subtitle': ''
        }

        # Apply preference rules for new movies
        # Rule 1: If subtitle is available, ignore no_subtitle
        if magnet_links['subtitle']:
            row['no_subtitle'] = ''
            row['size_no_subtitle'] = ''
        else:
            row['no_subtitle'] = magnet_links['no_subtitle']
            row['size_no_subtitle'] = magnet_links['size_no_subtitle']

        # Rule 2: If hacked_subtitle is available, ignore hacked_no_subtitle
        if magnet_links['hacked_subtitle']:
            row['hacked_no_subtitle'] = ''
            row['size_hacked_no_subtitle'] = ''
        else:
            row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
            row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']

        return row

    # Movie exists in history - check what torrent types are missing
    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)
    
    # Get missing torrent types that should be added to CSV
    missing_types = get_missing_torrent_types(history_torrent_types, current_torrent_types)
    
    row = {
        'href': href,
        'video_code': entry['video_code'],
        'page': page_num,
        'actor': actor_info,
        'rate': entry['rate'],
        'comment_number': entry['comment_number'],
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': ''
    }

    # Add torrents that are missing from history (normal magnet links)
    if 'hacked_subtitle' in missing_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = magnet_links['hacked_subtitle']
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
        logger.debug(f"Adding missing hacked_subtitle torrent for {entry['video_code']}")
    
    if 'hacked_no_subtitle' in missing_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        logger.debug(f"Adding missing hacked_no_subtitle torrent for {entry['video_code']}")
    
    if 'subtitle' in missing_types and magnet_links['subtitle']:
        row['subtitle'] = magnet_links['subtitle']
        row['size_subtitle'] = magnet_links['size_subtitle']
        logger.debug(f"Adding missing subtitle torrent for {entry['video_code']}")
    
    if 'no_subtitle' in missing_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = magnet_links['no_subtitle']
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        logger.debug(f"Adding missing no_subtitle torrent for {entry['video_code']}")

    # Add [DOWNLOADED PREVIOUSLY] markers for torrent types that already exist in history
    if 'hacked_subtitle' in history_torrent_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
        logger.debug(f"Marking hacked_subtitle as downloaded for {entry['video_code']}")
    
    if 'hacked_no_subtitle' in history_torrent_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        logger.debug(f"Marking hacked_no_subtitle as downloaded for {entry['video_code']}")
    
    if 'subtitle' in history_torrent_types and magnet_links['subtitle']:
        row['subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_subtitle'] = magnet_links['size_subtitle']
        logger.debug(f"Marking subtitle as downloaded for {entry['video_code']}")
    
    if 'no_subtitle' in history_torrent_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        logger.debug(f"Marking no_subtitle as downloaded for {entry['video_code']}")

    return row


def extract_url_part_after_javdb(url):
    """
    Extract the part of URL after javdb.com and convert it to a filename-safe format.
    Args:
        url: The custom URL (e.g., 'https://javdb.com/actors/EvkJ')
    Returns:
        str: The extracted part converted to filename-safe format (e.g., 'actors_EvkJ')
    """
    try:
        if 'javdb.com' in url:
            domain_pos = url.find('javdb.com')
            if domain_pos != -1:
                after_domain = url[domain_pos + len('javdb.com'):]
                if after_domain.startswith('/'):
                    after_domain = after_domain[1:]
                if after_domain.endswith('/'):
                    after_domain = after_domain[:-1]
                filename_part = after_domain.replace('/', '_')
                if '?' in filename_part:
                    filename_part = filename_part.split('?')[0]
                return filename_part
    except Exception as e:
        logger.warning(f"Error extracting URL part from {url}: {e}")
    return 'custom_url'


def generate_output_csv_name(custom_url=None):
    """
    Generate the output CSV filename based on whether a custom URL is provided.
    Args:
        custom_url: Custom URL if provided, None otherwise
    Returns:
        str: The generated CSV filename
    """
    if custom_url:
        url_part = extract_url_part_after_javdb(custom_url)
        today_date = datetime.now().strftime("%Y%m%d")
        return f'Javdb_AdHoc_{url_part}_{today_date}.csv'
    else:
        return f'Javdb_TodayTitle_{datetime.now().strftime("%Y%m%d")}.csv'


def main():
    # Parse command line arguments
    args = parse_arguments()

    # Update global variables based on arguments
    start_page = args.start_page
    end_page = args.end_page
    phase_mode = args.phase
    custom_url = args.url
    dry_run = args.dry_run
    ignore_history = args.ignore_history
    parse_all = args.all
    ignore_release_date = args.ignore_release_date

    # Determine output directory and filename
    if args.url:
        output_dir = AD_HOC_DIR
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.info(f"Created directory: {output_dir}")
        output_csv = args.output_file if args.output_file else OUTPUT_CSV
        csv_path = os.path.join(output_dir, output_csv)
        use_history_for_loading = False  # Don't check history for ad hoc mode
        use_history_for_saving = True    # But still record to history
    else:
        output_dir = DAILY_REPORT_DIR
        output_csv = args.output_file if args.output_file else OUTPUT_CSV
        csv_path = os.path.join(output_dir, output_csv)
        use_history_for_loading = True   # Check history for daily mode
        use_history_for_saving = True    # Record to history for daily mode

    logger.info("Starting JavDB spider...")
    logger.info(f"Arguments: start_page={start_page}, end_page={end_page}, phase={phase_mode}")
    if custom_url:
        logger.info(f"Custom URL: {custom_url}")
        logger.info("AD HOC MODE: Will process all entries (no history checking) but record to history")
    if dry_run:
        logger.info("DRY RUN MODE: No CSV file will be written")
    if ignore_history:
        logger.info("IGNORE HISTORY: Will scrape all pages without checking history")
    if parse_all:
        logger.info("PARSE ALL MODE: Will continue until empty page is found")
    if ignore_release_date:
        logger.info("IGNORE RELEASE DATE: Will process all entries regardless of today/yesterday tags")

    # Ensure Daily Report directory exists
    ensure_daily_report_dir()

    # Initialize history file path and data
    history_file = os.path.join(DAILY_REPORT_DIR, PARSED_MOVIES_CSV)
    parsed_movies_history_phase1 = {}
    parsed_movies_history_phase2 = {}

    # Only load history if use_history_for_loading is True
    if use_history_for_loading:
        # Validate history file integrity
        if os.path.exists(history_file):
            logger.info("Validating history file integrity...")
            if not validate_history_file(history_file):
                logger.warning("History file validation failed - duplicates may be present")

        # If history file does not exist, create it with header
        if not os.path.exists(history_file):
            with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('href,phase,video_code,parsed_date,torrent_type\n')
            logger.info(f"Created new history file: {history_file}")
        
        if ignore_history:
            parsed_movies_history_phase1 = {}
            parsed_movies_history_phase2 = {}
        else:
            parsed_movies_history_phase1 = load_parsed_movies_history(history_file, phase=1)
            # For phase 2, load ALL history (phase 1 and 2)
            parsed_movies_history_phase2 = load_parsed_movies_history(history_file, phase=None)
    else:
        # For ad hoc mode, ensure history file exists for saving
        if use_history_for_saving and not os.path.exists(history_file):
            with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('href,phase,video_code,parsed_date,torrent_type\n')
            logger.info(f"Created new history file for ad hoc mode: {history_file}")

    # Create session for connection reuse
    session = requests.Session()

    all_index_results = []
    rows = []
    phase1_rows = []  # Track phase 1 entries separately
    phase2_rows = []  # Track phase 2 entries separately
    subtitle_count = 0
    hacked_count = 0
    no_subtitle_count = 0
    
    # Define fieldnames for CSV
    fieldnames = ['href', 'video_code', 'page', 'actor', 'rate', 'comment_number', 'hacked_subtitle',
                  'hacked_no_subtitle', 'subtitle', 'no_subtitle', 'size_hacked_subtitle', 'size_hacked_no_subtitle',
                  'size_subtitle', 'size_no_subtitle']
    
    # Tolerance mechanism configuration
    max_consecutive_empty = 3    # Maximum tolerance for consecutive empty pages

    # Phase 1: Collect entries with both "含中字磁鏈" and "今日新種"/"昨日新種" tags
    if phase_mode in ['1', 'all']:
        logger.info("=" * 50)
        logger.info("PHASE 1: Processing entries with both subtitle and today/yesterday tags")
        logger.info("=" * 50)

        page_num = start_page
        consecutive_empty_pages = 0  # Track consecutive empty pages
        
        while True:
            page_url = get_page_url(page_num, phase=1, custom_url=custom_url)
            logger.debug(f"[Page {page_num}] Fetching: {page_url}")

            # Fetch index page
            index_html = get_page(page_url, session, use_cookie=custom_url is not None)
            if not index_html:
                logger.info(f"[Page {page_num}] no movie list found (page fetch failed or does not exist)")
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= max_consecutive_empty:
                    logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping phase 1")
                    break
                page_num += 1
                continue

            # Parse index page for phase 1
            # Disable new releases filter if: 1) custom URL is used, or 2) ignore_release_date flag is set
            page_results = parse_index(index_html, page_num, phase=1,
                                       disable_new_releases_filter=(custom_url is not None or ignore_release_date))

            if len(page_results) == 0:
                # Check if this is due to "No movie list found!" (page structure issue)
                # or due to no eligible entries (normal filtering)
                # We can't directly check the log, but we can infer from the HTML structure
                soup = BeautifulSoup(index_html, 'html.parser')
                movie_list = soup.find('div', class_='movie-list h cols-4 vcols-8')
                
                if not movie_list:
                    # No movie list found in HTML - this is a page structure issue
                    logger.info(f"[Page {page_num}] No movie list found in HTML structure (page structure issue)")
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_consecutive_empty:
                        logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping phase 1")
                        break
                else:
                    # Movie list exists but no eligible entries found (normal filtering)
                    logger.debug(f"[Page {page_num}] found 0 entries for phase 1 (page has content but no eligible entries)")
                    # Don't increment consecutive_empty_pages here - the page has content, just no eligible entries
            else:
                all_index_results.extend(page_results)
                consecutive_empty_pages = 0  # Reset counter when we find results

            # If parse_all is enabled and no results found, stop
            if parse_all and len(page_results) == 0:
                logger.info(f"[Page {page_num}] No results found, stopping phase 1")
                break

            # If not parse_all and reached end_page, stop
            if not parse_all and page_num >= end_page:
                break

            page_num += 1

            # Small delay between pages
            time.sleep(PAGE_SLEEP)

        # Process phase 1 entries
        total_entries_phase1 = len(all_index_results)

        for i, entry in enumerate(all_index_results, 1):
            href = entry['href']
            page_num = entry['page']

            # Skip if already parsed in this session
            if href in parsed_links:
                logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping already parsed in this session")
                continue

            # Add to parsed links set for this session
            parsed_links.add(href)

            detail_url = urljoin(BASE_URL, href)

            # Fetch detail page
            detail_html = get_page(detail_url, session, use_cookie=custom_url is not None)
            if not detail_html:
                logger.error(f"[{i}/{total_entries_phase1}] [Page {page_num}] Failed to fetch detail page")
                continue

            # Parse detail page
            magnets, actor_info, video_code = parse_detail(detail_html, i)
            magnet_links = extract_magnets(magnets, i)

            # Log the processing with video_code instead of href
            logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Processing {video_code}")

            # Check if we should process this movie based on history and phase rules
            should_process, history_torrent_types = should_process_movie(href, parsed_movies_history_phase1, 1,
                                                                         magnet_links)

            if not should_process:
                # Only skip if both hacked_subtitle and subtitle are present in history
                if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                    logger.debug(
                        f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
                else:
                    logger.debug(
                        f"[{i}/{total_entries_phase1}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
                continue

            # Count found torrents
            if magnet_links['subtitle']:
                subtitle_count += 1
            if magnet_links['hacked_subtitle'] or magnet_links['hacked_no_subtitle']:
                hacked_count += 1
            if magnet_links['no_subtitle']:
                no_subtitle_count += 1

            # Determine current torrent types and merge with history
            current_torrent_types = determine_torrent_types(magnet_links)
            all_torrent_types = list(
                set(history_torrent_types + current_torrent_types)) if history_torrent_types else current_torrent_types

            # Create row with video_code as title
            row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links,
                                                     parsed_movies_history_phase1)
            # Override the title with video_code
            row['video_code'] = video_code

            # Only add row if it contains new torrent categories (excluding already downloaded ones)
            has_new_torrents = any([
                row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]'
            ])

            if has_new_torrents:
                # Write to CSV immediately (before updating history)
                write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
                rows.append(row)
                phase1_rows.append(row)  # Track phase 1 entries
                logger.debug(f"[{i}/{total_entries_phase1}] [Page {page_num}] Added to CSV with new torrent categories")
                
                # Save to parsed movies history AFTER writing to CSV (only if new torrents found)
                if use_history_for_saving and not dry_run and not ignore_history:
                    # Only save new magnet links to history (exclude already downloaded ones)
                    new_magnet_links = {}
                    if row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_subtitle'] = magnet_links.get('hacked_subtitle', '')
                    if row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_no_subtitle'] = magnet_links.get('hacked_no_subtitle', '')
                    if row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['subtitle'] = magnet_links.get('subtitle', '')
                    if row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['no_subtitle'] = magnet_links.get('no_subtitle', '')
                    
                    if new_magnet_links:  # Only save if there are actually new magnet links
                        save_parsed_movie_to_history(history_file, href, 1, video_code, new_magnet_links)
            else:
                logger.debug(
                    f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipped CSV entry - all torrent categories already in history")
                # Don't update history if no new torrents were found

            # Small delay to be respectful to the server
            time.sleep(MOVIE_SLEEP)

        logger.info(f"Phase 1 completed: {len(phase1_rows)} entries processed")

    # Phase 2: Collect entries with only "今日新種"/"昨日新種" tag (filtered by quality)
    if phase_mode in ['2', 'all']:
        logger.info("=" * 50)
        logger.info(f"PHASE 2: Processing entries with only today/yesterday tag (rate > {PHASE2_MIN_RATE}, comments > {PHASE2_MIN_COMMENTS})")
        logger.info("=" * 50)

        all_index_results_phase2 = []

        page_num = start_page
        consecutive_empty_pages = 0  # Track consecutive empty pages
        
        while True:
            page_url = get_page_url(page_num, phase=2, custom_url=custom_url)
            logger.debug(f"[Page {page_num}] Fetching for phase 2: {page_url}")

            # Fetch index page
            index_html = get_page(page_url, session, use_cookie=custom_url is not None)
            if not index_html:
                logger.info(f"[Page {page_num}] no movie list found (page fetch failed or does not exist)")
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= max_consecutive_empty:
                    logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping phase 2")
                    break
                page_num += 1
                continue

            # Parse index page for phase 2
            # Disable new releases filter if: 1) custom URL is used, or 2) ignore_release_date flag is set
            page_results = parse_index(index_html, page_num, phase=2,
                                       disable_new_releases_filter=(custom_url is not None or ignore_release_date))

            if len(page_results) == 0:
                # Check if this is due to "No movie list found!" (page structure issue)
                # or due to no eligible entries (normal filtering)
                # We can't directly check the log, but we can infer from the HTML structure
                soup = BeautifulSoup(index_html, 'html.parser')
                movie_list = soup.find('div', class_='movie-list h cols-4 vcols-8')
                
                if not movie_list:
                    # No movie list found in HTML - this is a page structure issue
                    logger.info(f"[Page {page_num}] No movie list found in HTML structure (page structure issue)")
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_consecutive_empty:
                        logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping phase 2")
                        break
                else:
                    # Movie list exists but no eligible entries found (normal filtering)
                    logger.debug(f"[Page {page_num}] found 0 entries for phase 2 (page has content but no eligible entries)")
                    # Don't increment consecutive_empty_pages here - the page has content, just no eligible entries
            else:
                all_index_results_phase2.extend(page_results)
                consecutive_empty_pages = 0  # Reset counter when we find results

            # If parse_all is enabled and no results found, stop
            if parse_all and len(page_results) == 0:
                logger.info(f"[Page {page_num}] No results found, stopping phase 2")
                break

            # If not parse_all and reached end_page, stop
            if not parse_all and page_num >= end_page:
                break

            page_num += 1

            # Small delay between pages
            time.sleep(PAGE_SLEEP)

        # Process phase 2 entries
        total_entries_phase2 = len(all_index_results_phase2)

        for i, entry in enumerate(all_index_results_phase2, 1):
            href = entry['href']
            page_num = entry['page']

            # Skip if already parsed in this session
            if href in parsed_links:
                logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping already parsed in this session")
                continue

            # Add to parsed links set for this session
            parsed_links.add(href)

            detail_url = urljoin(BASE_URL, href)

            # Fetch detail page
            detail_html = get_page(detail_url, session, use_cookie=custom_url is not None)
            if not detail_html:
                logger.error(f"[{i}/{total_entries_phase2}] [Page {page_num}] Failed to fetch detail page")
                continue

            # Parse detail page
            magnets, actor_info, video_code = parse_detail(detail_html, f"P2-{i}")
            magnet_links = extract_magnets(magnets, f"P2-{i}")

            # Log the processing with video_code instead of href
            logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Processing {video_code}")

            # Check if we should process this movie based on history and phase rules
            should_process, history_torrent_types = should_process_movie(href, parsed_movies_history_phase2, 2,
                                                                         magnet_links)

            if not should_process:
                # Only skip if both hacked_subtitle and subtitle are present in history
                if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                    logger.debug(
                        f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
                else:
                    logger.debug(
                        f"[{i}/{total_entries_phase2}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
                continue

            # Count found torrents
            if magnet_links['subtitle']:
                subtitle_count += 1
            if magnet_links['hacked_subtitle'] or magnet_links['hacked_no_subtitle']:
                hacked_count += 1
            if magnet_links['no_subtitle']:
                no_subtitle_count += 1

            # Determine current torrent types and merge with history
            current_torrent_types = determine_torrent_types(magnet_links)
            all_torrent_types = list(
                set(history_torrent_types + current_torrent_types)) if history_torrent_types else current_torrent_types

            # Create row for Phase 2
            row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links,
                                                     parsed_movies_history_phase2)
            # Override the title with video_code
            row['video_code'] = video_code

            # Only add row if it contains new torrent categories (excluding already downloaded ones)
            has_new_torrents = any([
                row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]'
            ])

            if has_new_torrents:
                # Write to CSV immediately (before updating history)
                write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
                rows.append(row)
                phase2_rows.append(row)  # Track phase 2 entries
                logger.debug(f"[{i}/{total_entries_phase2}] [Page {page_num}] Added to CSV with new torrent categories")
                
                # Save to parsed movies history AFTER writing to CSV (only if new torrents found)
                if use_history_for_saving and not dry_run and not ignore_history:
                    # Only save new magnet links to history (exclude already downloaded ones)
                    new_magnet_links = {}
                    if row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_subtitle'] = magnet_links.get('hacked_subtitle', '')
                    if row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_no_subtitle'] = magnet_links.get('hacked_no_subtitle', '')
                    if row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['subtitle'] = magnet_links.get('subtitle', '')
                    if row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['no_subtitle'] = magnet_links.get('no_subtitle', '')
                    
                    if new_magnet_links:  # Only save if there are actually new magnet links
                        save_parsed_movie_to_history(history_file, href, 2, video_code, new_magnet_links)
            else:
                logger.debug(
                    f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipped CSV entry - all torrent categories already in history")
                # Don't update history if no new torrents were found

            # Small delay to be respectful to the server
            time.sleep(MOVIE_SLEEP)

        logger.info(f"Phase 2 completed: {len(phase2_rows)} entries processed")

    # CSV has been written incrementally during processing
    if not dry_run:
        logger.info(f"CSV file written incrementally to: {csv_path}")

    # Generate summary
    logger.info("=" * 50)
    logger.info("SUMMARY REPORT")
    logger.info("=" * 50)
    if parse_all:
        logger.info(f"Pages processed: {start_page} to last page with results")
    else:
        logger.info(f"Pages processed: {start_page} to {end_page}")
    
    logger.info(f"Tolerance mechanism: Stops after {max_consecutive_empty} consecutive pages with no HTML content")

    # Phase 1 Summary
    if phase_mode in ['1', 'all']:
        logger.info("=" * 30)
        logger.info("PHASE 1 SUMMARY")
        logger.info("=" * 30)
        logger.info(f"Phase 1 entries found: {len(phase1_rows)}")
        if len(phase1_rows) > 0:
            phase1_subtitle_count = sum(1 for row in phase1_rows if row['subtitle'])
            phase1_hacked_subtitle_count = sum(1 for row in phase1_rows if row['hacked_subtitle'])
            phase1_hacked_no_subtitle_count = sum(1 for row in phase1_rows if row['hacked_no_subtitle'])
            phase1_no_subtitle_count = sum(1 for row in phase1_rows if row['no_subtitle'])

            logger.info(
                f"  - Subtitle torrents: {phase1_subtitle_count} ({(phase1_subtitle_count / len(phase1_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked subtitle torrents: {phase1_hacked_subtitle_count} ({(phase1_hacked_subtitle_count / len(phase1_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked no-subtitle torrents: {phase1_hacked_no_subtitle_count} ({(phase1_hacked_no_subtitle_count / len(phase1_rows) * 100):.1f}%)")
            logger.info(
                f"  - No-subtitle torrents: {phase1_no_subtitle_count} ({(phase1_no_subtitle_count / len(phase1_rows) * 100):.1f}%)")
        else:
            logger.info("  - No entries found in Phase 1")

    # Phase 2 Summary
    if phase_mode in ['2', 'all']:
        logger.info("=" * 30)
        logger.info("PHASE 2 SUMMARY")
        logger.info("=" * 30)
        logger.info(f"Phase 2 entries found: {len(phase2_rows)}")
        if len(phase2_rows) > 0:
            phase2_subtitle_count = sum(1 for row in phase2_rows if row['subtitle'])
            phase2_hacked_subtitle_count = sum(1 for row in phase2_rows if row['hacked_subtitle'])
            phase2_hacked_no_subtitle_count = sum(1 for row in phase2_rows if row['hacked_no_subtitle'])
            phase2_no_subtitle_count = sum(1 for row in phase2_rows if row['no_subtitle'])

            logger.info(
                f"  - Subtitle torrents: {phase2_subtitle_count} ({(phase2_subtitle_count / len(phase2_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked subtitle torrents: {phase2_hacked_subtitle_count} ({(phase2_hacked_subtitle_count / len(phase2_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked no-subtitle torrents: {phase2_hacked_no_subtitle_count} ({(phase2_hacked_no_subtitle_count / len(phase2_rows) * 100):.1f}%)")
            logger.info(
                f"  - No-subtitle torrents: {phase2_no_subtitle_count} ({(phase2_no_subtitle_count / len(phase2_rows) * 100):.1f}%)")
        else:
            logger.info("  - No entries found in Phase 2")

    # Overall Summary
    logger.info("=" * 30)
    logger.info("OVERALL SUMMARY")
    logger.info("=" * 30)
    logger.info(f"Total entries found: {len(rows)}")
    logger.info(f"Successfully processed: {len(rows)}")
    logger.info(f"Skipped already parsed in this session: {len(parsed_links)}")
    if use_history_for_loading and not ignore_history:
        logger.info(f"Skipped already parsed in previous runs: {len(parsed_movies_history_phase1)}")
    elif custom_url:
        logger.info("Ad hoc mode: No history checking performed")
    logger.info(f"Current parsed links in memory: {len(parsed_links)}")

    # Overall torrent statistics
    if len(rows) > 0:
        total_subtitle_count = sum(1 for row in rows if row['subtitle'])
        total_hacked_subtitle_count = sum(1 for row in rows if row['hacked_subtitle'])
        total_hacked_no_subtitle_count = sum(1 for row in rows if row['hacked_no_subtitle'])
        total_no_subtitle_count = sum(1 for row in rows if row['no_subtitle'])

        logger.info(
            f"Overall subtitle torrents: {total_subtitle_count} ({(total_subtitle_count / len(rows) * 100):.1f}%)")
        logger.info(
            f"Overall hacked subtitle torrents: {total_hacked_subtitle_count} ({(total_hacked_subtitle_count / len(rows) * 100):.1f}%)")
        logger.info(
            f"Overall hacked no-subtitle torrents: {total_hacked_no_subtitle_count} ({(total_hacked_no_subtitle_count / len(rows) * 100):.1f}%)")
        logger.info(
            f"Overall no-subtitle torrents: {total_no_subtitle_count} ({(total_no_subtitle_count / len(rows) * 100):.1f}%)")

    if not dry_run:
        logger.info(f"Results saved to: {csv_path}")
        if use_history_for_saving:
            logger.info(f"History saved to: {os.path.join(DAILY_REPORT_DIR, PARSED_MOVIES_CSV)}")
    logger.info("=" * 50)


if __name__ == '__main__':
    main() 
