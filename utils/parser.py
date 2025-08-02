import re
import logging
import time
from bs4 import BeautifulSoup
from bs4.element import Tag

# Import configuration
try:
    from config import DETAIL_PAGE_SLEEP, PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS, LOG_LEVEL
except ImportError:
    # Fallback values if config.py doesn't exist
    DETAIL_PAGE_SLEEP = 5
    PHASE2_MIN_RATE = 4.0
    PHASE2_MIN_COMMENTS = 100
    LOG_LEVEL = 'INFO'

from utils.logging_config import get_logger, setup_logging
setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)

def extract_video_code(a):
    """Extract video code from movie item with improved robustness"""
    video_title_div = a.find('div', class_='video-title')
    if video_title_div:
        # Try to extract video code from <strong> tag first (most reliable)
        strong_tag = video_title_div.find('strong')
        if strong_tag:
            video_code = strong_tag.get_text(strip=True)
            logger.debug(f"Extracted video code from <strong> tag: {video_code}")
            return video_code
        else:
            # Fallback to full text
            video_code = video_title_div.get_text(strip=True)
            logger.debug(f"Extracted video code from full text: {video_code}")
            return video_code
    logger.warning("No video-title div found")
    return ''

def parse_index(html_content, page_num, phase=1, disable_new_releases_filter=False):
    """Parse the index page to extract entries with required tags"""
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
            logger.debug(f'[Page {page_num}] Div {i+1} classes: {div.get("class")}')
        
        return results
    
    logger.debug(f"[Page {page_num}] Found movie list container")
    
    logger.debug(f"[Page {page_num}] Parsing index page for phase {phase}...")
    if disable_new_releases_filter:
        logger.info(f"[Page {page_num}] New releases filter disabled - will process all entries")
    
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
        
        # Phase 1: Check if both required tags are present
        if phase == 1:
            # If new releases filter is disabled, only check for subtitle tag
            if disable_new_releases_filter:
                has_subtitle = ('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)
                if has_subtitle:
                    href = a.get('href', '')
                    video_code = extract_video_code(a)
                    
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
                # Original logic: check for both subtitle and new releases tags
                if (('含中字磁鏈' in tags and ('今日新種' in tags or '昨日新種' in tags)) or 
                    ('含中字磁链' in tags and ('今日新种' in tags or '昨日新种' in tags)) or 
                    ('CnSub DL' in tags and ('Today' in tags or 'Yesterday' in tags))):
                    
                    href = a.get('href', '')
                    video_code = extract_video_code(a)
                    
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
                            logger.debug(f"[Page {page_num}] Found entry (filter disabled): {video_code} ({href}) - Rate: {rate}, Comments: {comment_number}")
                            
                            results.append({
                                'href': href,
                                'video_code': video_code,
                                'page': page_num,
                                'actor': '',  # Will be filled from detail page
                                'rate': rate,
                                'comment_number': comment_number
                            })
                        else:
                            logger.debug(f"[Page {page_num}] Skipped entry (filtered): {video_code} - Rate: {rate}, Comments: {comment_number}")
                    except (ValueError, TypeError):
                        logger.debug(f"[Page {page_num}] Skipped entry (invalid data): {video_code} - Rate: {rate}, Comments: {comment_number}")
            else:
                # Original logic: check for new releases tags only
                if (('今日新種' in tags or '昨日新種' in tags) or 
                    ('今日新种' in tags or '昨日新种' in tags) or 
                    ('Today' in tags or 'Yesterday' in tags)):
                    # Skip if it also has subtitle tag (already processed in phase 1)
                    if not (('含中字磁鏈' in tags or '含中字磁链' in tags or 'CnSub DL' in tags)):
                        href = a.get('href', '')
                        video_code = extract_video_code(a)
                        
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
                            
                            if comment_num > PHASE2_MIN_COMMENTS and rate_num > PHASE2_MIN_RATE:
                                logger.debug(f"[Page {page_num}] Found entry: {video_code} ({href}) - Rate: {rate}, Comments: {comment_number}")
                                
                                results.append({
                                    'href': href,
                                    'video_code': video_code,
                                    'page': page_num,
                                    'actor': '',  # Will be filled from detail page
                                    'rate': rate,
                                    'comment_number': comment_number
                                })
                            else:
                                logger.debug(f"[Page {page_num}] Skipped entry (filtered): {video_code} - Rate: {rate}, Comments: {comment_number}")
                        except (ValueError, TypeError):
                            logger.debug(f"[Page {page_num}] Skipped entry (invalid data): {video_code} - Rate: {rate}, Comments: {comment_number}")
    
    logger.info(f"[Page {page_num}] Found {len(results)} entries for phase {phase}")
    return results

def parse_detail(html_content, index=None):
    """Parse the detail page to extract magnet links and actor information"""
    # Wait to be respectful to JavDB website and avoid DDoS protection
    time.sleep(DETAIL_PAGE_SLEEP)
    
    soup = BeautifulSoup(html_content, 'html.parser')
    magnets = []
    actor_info = ''
    video_code = ''
    
    prefix = f"[{index}]" if index is not None else ""
    
    # Extract movie code from the copy button
    copy_button = soup.find('a', class_='button is-white copy-to-clipboard')
    if copy_button:
        video_code = copy_button.get('data-clipboard-text', '')
        logger.debug(f"{prefix} Found video code: {video_code}")
    else:
        logger.warning(f"{prefix} No copy button found for video code")
    
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
        logger.warning(f"{prefix} No magnets content found in detail page")
        return magnets, actor_info, video_code
    
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
    return magnets, actor_info, video_code
