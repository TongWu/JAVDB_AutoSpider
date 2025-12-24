import re
import logging
import time
from bs4 import BeautifulSoup
from bs4.element import Tag

# Import configuration
try:
    from config import DETAIL_PAGE_SLEEP, PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS, LOG_LEVEL, IGNORE_RELEASE_DATE_FILTER
except ImportError:
    # Fallback values if config.py doesn't exist
    DETAIL_PAGE_SLEEP = 5
    PHASE2_MIN_RATE = 4.0
    PHASE2_MIN_COMMENTS = 100
    LOG_LEVEL = 'INFO'
    IGNORE_RELEASE_DATE_FILTER = False

from utils.logging_config import get_logger, setup_logging

setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)


def extract_video_code(a):
    """Extract video code from movie item with improved robustness
    
    Returns:
        video_code: The extracted video code, or empty string if not found or invalid.
                   Video codes without '-' are considered invalid and will return empty string.
    """
    video_title_div = a.find('div', class_='video-title')
    if video_title_div:
        # Try to extract video code from <strong> tag first (most reliable)
        strong_tag = video_title_div.find('strong')
        if strong_tag:
            video_code = strong_tag.get_text(strip=True)
        else:
            # Fallback to full text
            video_code = video_title_div.get_text(strip=True)
        
        # Validate video code: must contain '-' (e.g., "ABC-123")
        # Video codes without '-' are typically invalid or special entries
        if '-' not in video_code:
            logger.debug(f"Skipping invalid video code (no '-'): {video_code}")
            return ''
        
        logger.debug(f"Extracted video code: {video_code}")
        return video_code
    logger.warning("No video-title div found")
    return ''


def parse_index(html_content, page_num, phase=1, disable_new_releases_filter=False, is_adhoc_mode=False):
    """Parse the index page to extract entries with required tags.
    
    Args:
        html_content: HTML content to parse
        page_num: Current page number
        phase: 1 for subtitle entries, 2 for non-subtitle entries
        disable_new_releases_filter: If True, disable release date filter but keep other filters
        is_adhoc_mode: If True, disable ALL filters and process ALL entries (for custom URL mode)
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    results = []

    # Check for age verification modal and log it
    age_modal = soup.find('div', class_='modal is-active over18-modal')
    if age_modal:
        logger.debug(f'[Page {page_num}] Age verification modal detected, but continuing with parsing')
    else:
        logger.debug(f'[Page {page_num}] No age verification modal found')

    movie_list = soup.find('div', class_='movie-list h cols-4 vcols-8')
    if not movie_list:
        logger.warning(f'[Page {page_num}] No movie list found!')

        # Add more detailed debugging information
        logger.debug(f'[Page {page_num}] HTML content length: {len(html_content)}')
        title_tag = soup.find('title')
        page_title = title_tag.get_text() if title_tag else "No title"
        logger.debug(f'[Page {page_num}] Page title: {page_title}')

        # Look for all div elements to see if there are other possible containers
        all_divs = soup.find_all('div')
        movie_related_divs = []
        for div in all_divs:
            classes = div.get('class', [])
            if any('movie' in str(c).lower() or 'list' in str(c).lower() for c in classes):
                movie_related_divs.append(div)

        logger.debug(f'[Page {page_num}] Found {len(movie_related_divs)} divs with movie/list related classes')
        for i, div in enumerate(movie_related_divs[:5]):  # Only show first 5
            logger.debug(f'[Page {page_num}] Div {i + 1} classes: {div.get("class")}')

        return results

    logger.debug(f"[Page {page_num}] Found movie list container")

    logger.debug(f"[Page {page_num}] Parsing index page for phase {phase}...")
    # if is_adhoc_mode:
    #     logger.info(f"[Page {page_num}] AD HOC MODE - all filters disabled, processing all entries")
    # elif disable_new_releases_filter:
    #     logger.info(f"[Page {page_num}] New releases filter disabled - will process all entries")
    # elif IGNORE_RELEASE_DATE_FILTER:
    #     logger.info(f"[Page {page_num}] Release date filter ignored - processing all subtitle entries")

    for item in movie_list.find_all('div', class_='item'):
        a = item.find('a', class_='box')
        if not a:
            continue

        tags_div = a.find('div', class_='tags has-addons')
        if not tags_div or not isinstance(tags_div, Tag):
            continue

        tags = []
        for span in tags_div.find_all('span', class_='tag'):
            if isinstance(span, Tag):
                tags.append(span.get_text(strip=True))

        logger.debug(f"[Page {page_num}] Found tags: {tags}")

        # AD HOC MODE: Disable release date filter but keep phase separation
        # Phase 1: entries with subtitle tag, Phase 2: entries without subtitle tag
        # Note: Entries without magnet tags are always filtered out (no magnet = no download available)
        if is_adhoc_mode:
            has_subtitle = ('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)
            has_magnet = ('含磁鏈' in tags or '含磁链' in tags or 'DL' in tags or has_subtitle)
            
            # Skip entries without magnet tags - no magnet link means nothing to download
            if not has_magnet:
                logger.debug(f"[Page {page_num}] Skipping entry without magnet link (no magnet tag in HTML)")
                continue
            
            # Phase 1: Process entries WITH subtitle tag
            if phase == 1 and has_subtitle:
                href = a.get('href', '')
                video_code = extract_video_code(a)
                
                # Skip entries with invalid video code (no '-')
                if not video_code:
                    continue

                # Extract rating information
                rate = ''
                score_div = a.find('div', class_='score')
                if score_div:
                    value_span = score_div.find('span', class_='value')
                    if value_span:
                        score_text = value_span.get_text(strip=True)
                        rate_match = re.search(r'(\d+\.?\d*)分', score_text)
                        if rate_match:
                            rate = rate_match.group(1)

                # Extract comment number
                comment_number = ''
                if score_div:
                    value_span = score_div.find('span', class_='value')
                    if value_span:
                        score_text = value_span.get_text(strip=True)
                        comment_match = re.search(r'由(\d+)人評價', score_text)
                        if comment_match:
                            comment_number = comment_match.group(1)

                logger.debug(f"[Page {page_num}] Found entry (adhoc P1): {video_code} ({href})")

                results.append({
                    'href': href,
                    'video_code': video_code,
                    'page': page_num,
                    'actor': '',  # Will be filled from detail page
                    'rate': rate,
                    'comment_number': comment_number
                })
            # Phase 2: Process entries WITHOUT subtitle tag
            elif phase == 2 and not has_subtitle:
                href = a.get('href', '')
                video_code = extract_video_code(a)
                
                # Skip entries with invalid video code (no '-')
                if not video_code:
                    continue

                # Extract rating information
                rate = ''
                score_div = a.find('div', class_='score')
                if score_div:
                    value_span = score_div.find('span', class_='value')
                    if value_span:
                        score_text = value_span.get_text(strip=True)
                        rate_match = re.search(r'(\d+\.?\d*)分', score_text)
                        if rate_match:
                            rate = rate_match.group(1)

                # Extract comment number
                comment_number = ''
                if score_div:
                    value_span = score_div.find('span', class_='value')
                    if value_span:
                        score_text = value_span.get_text(strip=True)
                        comment_match = re.search(r'由(\d+)人評價', score_text)
                        if comment_match:
                            comment_number = comment_match.group(1)

                logger.debug(f"[Page {page_num}] Found entry (adhoc P2): {video_code} ({href})")

                results.append({
                    'href': href,
                    'video_code': video_code,
                    'page': page_num,
                    'actor': '',  # Will be filled from detail page
                    'rate': rate,
                    'comment_number': comment_number
                })
            continue

        # Phase 1: Check if both required tags are present
        if phase == 1:
            # If new releases filter is disabled, only check for subtitle tag
            if disable_new_releases_filter:
                has_subtitle = ('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)
                if has_subtitle:
                    href = a.get('href', '')
                    video_code = extract_video_code(a)
                    
                    # Skip entries with invalid video code (no '-')
                    if not video_code:
                        continue

                    # Extract rating information
                    rate = ''
                    score_div = a.find('div', class_='score')
                    if score_div:
                        value_span = score_div.find('span', class_='value')
                        if value_span:
                            score_text = value_span.get_text(strip=True)
                            # Extract rating number (e.g., "4.47分" -> "4.47")
                            rate_match = re.search(r'(\d+\.?\d*)分', score_text)
                            if rate_match:
                                rate = rate_match.group(1)

                    # Extract comment number
                    comment_number = ''
                    if score_div:
                        value_span = score_div.find('span', class_='value')
                        if value_span:
                            score_text = value_span.get_text(strip=True)
                            # Extract comment number (e.g., "由595人評價" -> "595")
                            comment_match = re.search(r'由(\d+)人評價', score_text)
                            if comment_match:
                                comment_number = comment_match.group(1)

                    logger.debug(f"[Page {page_num}] Found entry (filter disabled): {video_code} ({href})")

                    results.append({
                        'href': href,
                        'video_code': video_code,
                        'page': page_num,
                        'actor': '',  # Will be filled from detail page
                        'rate': rate,
                        'comment_number': comment_number
                    })
            else:
                # Check for subtitle tags and optionally release date tags
                has_subtitle = ('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)
                has_release_date = (('今日新種' in tags or '昨日新種' in tags) or 
                                  ('今日新种' in tags or '昨日新种' in tags) or 
                                  ('Today' in tags or 'Yesterday' in tags))
                
                # If IGNORE_RELEASE_DATE_FILTER is True, only check for subtitle tags
                # If IGNORE_RELEASE_DATE_FILTER is False, check for both subtitle and release date tags
                if has_subtitle and (IGNORE_RELEASE_DATE_FILTER or has_release_date):

                    href = a.get('href', '')
                    video_code = extract_video_code(a)
                    
                    # Skip entries with invalid video code (no '-')
                    if not video_code:
                        continue

                    # Extract rating information
                    rate = ''
                    score_div = a.find('div', class_='score')
                    if score_div:
                        value_span = score_div.find('span', class_='value')
                        if value_span:
                            score_text = value_span.get_text(strip=True)
                            # Extract rating number (e.g., "4.47分" -> "4.47")
                            rate_match = re.search(r'(\d+\.?\d*)分', score_text)
                            if rate_match:
                                rate = rate_match.group(1)

                    # Extract comment number
                    comment_number = ''
                    if score_div:
                        value_span = score_div.find('span', class_='value')
                        if value_span:
                            score_text = value_span.get_text(strip=True)
                            # Extract comment number (e.g., "由595人評價" -> "595")
                            comment_match = re.search(r'由(\d+)人評價', score_text)
                            if comment_match:
                                comment_number = comment_match.group(1)

                    logger.debug(f"[Page {page_num}] Found entry: {video_code} ({href})")

                    results.append({
                        'href': href,
                        'video_code': video_code,
                        'page': page_num,
                        'actor': '',  # Will be filled from detail page
                        'rate': rate,
                        'comment_number': comment_number
                    })

        # Phase 2: Check if only "今日新種" or "昨日新種" tag is present
        elif phase == 2:
            # If new releases filter is disabled, process all entries without subtitle tag
            if disable_new_releases_filter:
                # Skip if it has subtitle tag (already processed in phase 1)
                if not (('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)):
                    href = a.get('href', '')
                    video_code = extract_video_code(a)
                    
                    # Skip entries with invalid video code (no '-')
                    if not video_code:
                        continue

                    # Extract rating information
                    rate = ''
                    score_div = a.find('div', class_='score')
                    if score_div:
                        value_span = score_div.find('span', class_='value')
                        if value_span:
                            score_text = value_span.get_text(strip=True)
                            # Extract rating number (e.g., "4.47分" -> "4.47")
                            rate_match = re.search(r'(\d+\.?\d*)分', score_text)
                            if rate_match:
                                rate = rate_match.group(1)

                    # Extract comment number
                    comment_number = ''
                    if score_div:
                        value_span = score_div.find('span', class_='value')
                        if value_span:
                            score_text = value_span.get_text(strip=True)
                            # Extract comment number (e.g., "由595人評價" -> "595")
                            comment_match = re.search(r'由(\d+)人評價', score_text)
                            if comment_match:
                                comment_number = comment_match.group(1)

                    # Filter phase 2 entries using configurable thresholds
                    try:
                        comment_num = int(comment_number) if comment_number else 0
                        rate_num = float(rate) if rate else 0

                        if comment_num >= PHASE2_MIN_COMMENTS and rate_num >= PHASE2_MIN_RATE:
                            logger.debug(
                                f"[Page {page_num}] Found entry (filter disabled): {video_code} ({href}) - Rate: {rate}, Comments: {comment_number}")

                            results.append({
                                'href': href,
                                'video_code': video_code,
                                'page': page_num,
                                'actor': '',  # Will be filled from detail page
                                'rate': rate,
                                'comment_number': comment_number
                            })
                        else:
                            logger.debug(
                                f"[Page {page_num}] Skipped entry (filtered): {video_code} - Rate: {rate}, Comments: {comment_number}")
                    except (ValueError, TypeError):
                        logger.debug(
                            f"[Page {page_num}] Skipped entry (invalid data): {video_code} - Rate: {rate}, Comments: {comment_number}")
            else:
                # Check for release date tags (only for entries without subtitle tags)
                has_release_date = (('今日新種' in tags or '昨日新種' in tags) or
                                  ('今日新种' in tags or '昨日新种' in tags) or
                                  ('Today' in tags or 'Yesterday' in tags))
                
                # If IGNORE_RELEASE_DATE_FILTER is True, process all entries without subtitle tags
                # If IGNORE_RELEASE_DATE_FILTER is False, only process entries with release date tags
                if IGNORE_RELEASE_DATE_FILTER or has_release_date:
                    # Skip if it also has subtitle tag (already processed in phase 1)
                    if not (('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)):
                        href = a.get('href', '')
                        video_code = extract_video_code(a)
                        
                        # Skip entries with invalid video code (no '-')
                        if not video_code:
                            continue

                        # Extract rating information
                        rate = ''
                        score_div = a.find('div', class_='score')
                        if score_div:
                            value_span = score_div.find('span', class_='value')
                            if value_span:
                                score_text = value_span.get_text(strip=True)
                                # Extract rating number (e.g., "4.47分" -> "4.47")
                                rate_match = re.search(r'(\d+\.?\d*)分', score_text)
                                if rate_match:
                                    rate = rate_match.group(1)

                        # Extract comment number
                        comment_number = ''
                        if score_div:
                            value_span = score_div.find('span', class_='value')
                            if value_span:
                                score_text = value_span.get_text(strip=True)
                                # Extract comment number (e.g., "由595人評價" -> "595")
                                comment_match = re.search(r'由(\d+)人評價', score_text)
                                if comment_match:
                                    comment_number = comment_match.group(1)

                        # Filter phase 2 entries using configurable thresholds
                        try:
                            comment_num = int(comment_number) if comment_number else 0
                            rate_num = float(rate) if rate else 0

                            if comment_num >= PHASE2_MIN_COMMENTS and rate_num >= PHASE2_MIN_RATE:
                                logger.debug(
                                    f"[Page {page_num}] Found entry: {video_code} ({href}) - Rate: {rate}, Comments: {comment_number}")

                                results.append({
                                    'href': href,
                                    'video_code': video_code,
                                    'page': page_num,
                                    'actor': '',  # Will be filled from detail page
                                    'rate': rate,
                                    'comment_number': comment_number
                                })
                            else:
                                logger.debug(
                                    f"[Page {page_num}] Skipped entry (filtered): {video_code} - Rate: {rate}, Comments: {comment_number}")
                        except (ValueError, TypeError):
                            logger.debug(
                                f"[Page {page_num}] Skipped entry (invalid data): {video_code} - Rate: {rate}, Comments: {comment_number}")

    logger.debug(f"[Page {page_num}] Found {len(results)} entries for phase {phase}")
    return results


def parse_detail(html_content, index=None, skip_sleep=False):
    """Parse the detail page to extract magnet links and actor information
    
    Note: video_code is extracted from the index/catalog page, not from detail page.
    
    Args:
        html_content: HTML content of the detail page
        index: Index number for logging prefix
        skip_sleep: If True, skip the sleep delay (used during fallback retries)
    
    Returns:
        tuple: (magnets, actor_info, parse_success)
            - magnets: List of magnet link dictionaries
            - actor_info: Actor name string
            - parse_success: True if magnets_content was found
    """
    # Wait to be respectful to JavDB website and avoid DDoS protection
    if not skip_sleep:
        time.sleep(DETAIL_PAGE_SLEEP)

    soup = BeautifulSoup(html_content, 'html.parser')
    magnets = []
    actor_info = ''
    parse_success = True  # Track if parsing found expected elements

    prefix = f"[{index}]" if index is not None else ""

    # Extract actor information from the detail page
    video_meta_panel = soup.find('div', class_='video-meta-panel')
    if video_meta_panel:
        # Look for the actor panel block
        for panel_block in video_meta_panel.find_all('div', class_='panel-block'):
            strong_tag = panel_block.find('strong')
            if strong_tag and '演員:' in strong_tag.get_text():
                value_span = panel_block.find('span', class_='value')
                if value_span and isinstance(value_span, Tag):
                    # Extract all actor links
                    actor_links = value_span.find_all('a')
                    if actor_links:
                        # Get the first actor (usually the main actress)
                        first_actor = actor_links[0].get_text(strip=True)
                        actor_info = first_actor
                        logger.debug(f"{prefix} Found actor: {actor_info}")
                break

    magnets_content = soup.find('div', id='magnets-content')
    if not magnets_content:
        logger.debug(f"{prefix} No magnets content found in detail page")
        parse_success = False
        return magnets, actor_info, parse_success

    for item in magnets_content.find_all('div', class_=re.compile(r'item columns is-desktop')):
        magnet_name_div = item.find('div', class_='magnet-name')
        if not magnet_name_div:
            continue

        magnet_a = magnet_name_div.find('a')
        if not magnet_a:
            continue

        magnet_href = magnet_a.get('href', '')
        name_span = magnet_a.find('span', class_='name')
        name = name_span.get_text(strip=True) if name_span else ''

        # Extract size information
        size = ''
        meta_span = magnet_a.find('span', class_='meta')
        if meta_span:
            meta_text = meta_span.get_text(strip=True)
            # Extract size from meta text (e.g., "4.94GB, 1個文件" -> "4.94GB")
            size_match = re.search(r'([\d.]+(?:GB|MB|KB))', meta_text)
            if size_match:
                size = size_match.group(1)

        # Extract timestamp information
        timestamp = ''
        # Look for time span in the item structure (it's in a separate div, not inside the magnet_a)
        time_span = item.find('span', class_='time')
        if time_span:
            timestamp = time_span.get_text(strip=True)

        tags_div = magnet_a.find('div', class_='tags')
        tags = []
        if tags_div and isinstance(tags_div, Tag):
            for span in tags_div.find_all('span', class_='tag'):
                if isinstance(span, Tag):
                    tags.append(span.get_text(strip=True))

        logger.debug(f"{prefix} Found magnet: {name} with tags: {tags}, size: {size}, time: {timestamp}")

        magnets.append({
            'href': magnet_href,
            'name': name,
            'tags': tags,
            'size': size,
            'timestamp': timestamp
        })

    logger.debug(f"{prefix} Found {len(magnets)} magnet links")
    return magnets, actor_info, parse_success