"""
Email Notification Script for JAVDB AutoSpider

This script handles email notifications for pipeline results.
It can be run standalone or called from pipeline.py.

Features:
- Analyzes spider, uploader, and pikpak logs for errors
- Sends email with formatted report
- Converts log files to .txt before attaching
- Commits pipeline log after sending
"""

import smtplib
import logging
import os
import sys
import re
import shutil
import argparse
from datetime import datetime
from email.message import EmailMessage

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Import unified configuration
try:
    from config import (
        GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH,
        SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO,
        PIPELINE_LOG_FILE, SPIDER_LOG_FILE, UPLOADER_LOG_FILE,
        DAILY_REPORT_DIR, AD_HOC_DIR, LOG_LEVEL,
        PIKPAK_LOG_FILE
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    GIT_USERNAME = 'your_github_username'
    GIT_PASSWORD = 'your_github_password_or_token'
    GIT_REPO_URL = 'https://github.com/your_username/your_repo_name.git'
    GIT_BRANCH = 'main'
    
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_USER = 'your_email@gmail.com'
    SMTP_PASSWORD = 'your_email_password'
    EMAIL_FROM = 'your_email@gmail.com'
    EMAIL_TO = 'your_email@gmail.com'
    
    PIPELINE_LOG_FILE = 'logs/pipeline.log'
    SPIDER_LOG_FILE = 'logs/spider.log'
    UPLOADER_LOG_FILE = 'logs/qb_uploader.log'
    DAILY_REPORT_DIR = 'reports/DailyReport'
    AD_HOC_DIR = 'reports/AdHoc'
    LOG_LEVEL = 'INFO'
    PIKPAK_LOG_FILE = 'logs/pikpak_bridge.log'

# Import EMAIL_NOTIFICATION_LOG_FILE with fallback
try:
    from config import EMAIL_NOTIFICATION_LOG_FILE
except ImportError:
    EMAIL_NOTIFICATION_LOG_FILE = 'logs/email_notification.log'

# --- LOGGING SETUP ---
from utils.logging_config import setup_logging, get_logger
setup_logging(EMAIL_NOTIFICATION_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import masking utilities
from utils.masking import mask_email, mask_server, mask_full

# Import git helper
from utils.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials

# Import path helper for dated subdirectories
from utils.path_helper import get_dated_report_path, find_latest_report_in_dated_dirs


def extract_adhoc_info_from_csv(csv_path):
    """
    Extract Ad-Hoc mode information from CSV filename.
    
    Expected format: Javdb_AdHoc_{type}_{name}_{date}.csv
    Examples:
    - Javdb_AdHoc_actors_森日向子_20251224.csv -> (actors, 森日向子)
    - Javdb_AdHoc_makers_MOODYZ_20251224.csv -> (makers, MOODYZ)
    - Javdb_AdHoc_video_codes_MIDA_20251224.csv -> (video_codes, MIDA)
    
    Returns:
        tuple: (url_type, display_name) or (None, None) if not parseable
    """
    if not csv_path:
        return None, None
    
    filename = os.path.basename(csv_path)
    
    # Check if it's an Ad-Hoc file
    if not filename.startswith('Javdb_AdHoc_'):
        return None, None
    
    # Remove prefix and extension
    # Javdb_AdHoc_actors_森日向子_20251224.csv -> actors_森日向子_20251224
    without_prefix = filename.replace('Javdb_AdHoc_', '').replace('.csv', '')
    
    # Split and extract parts
    # actors_森日向子_20251224 -> ['actors', '森日向子', '20251224']
    # video_codes_MIDA_20251224 -> ['video', 'codes', 'MIDA', '20251224']
    #   (multi-part types like video_codes get split into multiple parts)
    parts = without_prefix.split('_')
    
    if len(parts) < 3:
        return None, None
    
    # Handle url_type which might be multi-part (e.g., video_codes)
    # The date is always the last part (8 digits)
    date_part = parts[-1]
    if not (len(date_part) == 8 and date_part.isdigit()):
        return None, None
    
    # Known multi-part types
    multi_part_types = ['video_codes']
    
    url_type = None
    display_name = None
    
    # Check if it's a multi-part type
    for multi_type in multi_part_types:
        if without_prefix.startswith(multi_type + '_'):
            url_type = multi_type
            # Extract name between type and date
            name_parts = parts[2:-1]  # Skip first two parts (video, codes) and last (date)
            display_name = '_'.join(name_parts) if name_parts else None
            break
    
    # If not a multi-part type, assume single part type
    if url_type is None:
        url_type = parts[0]
        # Name is everything between type and date
        name_parts = parts[1:-1]
        display_name = '_'.join(name_parts) if name_parts else None
    
    return url_type, display_name


def format_adhoc_info(url_type, display_name):
    """
    Format Ad-Hoc information for display in email.
    
    Returns:
        str: Formatted string like "Actor: 森日向子" or "Video Code: MIDA"
    """
    type_labels = {
        'actors': 'Actor',
        'makers': 'Maker',
        'video_codes': 'Video Code',
        'series': 'Series',
        'directors': 'Director',
        'labels': 'Label',
    }
    
    label = type_labels.get(url_type, url_type.replace('_', ' ').title() if url_type else 'Unknown')
    name = display_name if display_name else 'Unknown'
    
    return f"{label}: {name}"


def find_latest_adhoc_csv(adhoc_dir):
    """
    Find the most recently created/modified Ad-Hoc CSV file.
    
    This function uses wildcard patterns (not date-specific) to handle 
    cross-midnight scenarios where spider runs before midnight but 
    email notification runs after midnight.
    
    Args:
        adhoc_dir: Base Ad-Hoc directory (e.g., reports/AdHoc)
    
    Returns:
        str: Full path to the latest CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent AdHoc CSV file
    # Pattern: Javdb_AdHoc_*.csv (any date)
    adhoc_pattern = 'Javdb_AdHoc_*.csv'
    
    latest_file = find_latest_report_in_dated_dirs(adhoc_dir, adhoc_pattern)
    
    if latest_file:
        logger.info(f"Found Ad-Hoc CSV: {latest_file}")
        return latest_file
    
    # Fallback: try to find any CSV file (legacy pattern)
    legacy_pattern = 'Javdb_*.csv'
    latest_legacy = find_latest_report_in_dated_dirs(adhoc_dir, legacy_pattern)
    
    if latest_legacy:
        logger.info(f"Found Ad-Hoc CSV (legacy pattern): {latest_legacy}")
        return latest_legacy
    
    logger.warning(f"No Ad-Hoc CSV files found in {adhoc_dir}")
    return None


def find_latest_daily_csv(daily_dir):
    """
    Find the most recently created/modified Daily CSV file.
    
    This function uses wildcard patterns (not date-specific) to handle 
    cross-midnight scenarios where spider runs before midnight but 
    email notification runs after midnight.
    
    Args:
        daily_dir: Base Daily Report directory (e.g., reports/DailyReport)
    
    Returns:
        str: Full path to the latest CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent Daily CSV file
    # Pattern: Javdb_TodayTitle_*.csv (any date)
    daily_pattern = 'Javdb_TodayTitle_*.csv'
    
    latest_file = find_latest_report_in_dated_dirs(daily_dir, daily_pattern)
    
    if latest_file:
        logger.info(f"Found Daily CSV: {latest_file}")
        return latest_file
    
    logger.warning(f"No Daily CSV files found in {daily_dir}")
    return None


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Email Notification for JavDB Pipeline')
    parser.add_argument('--csv-path', type=str, help='Path to the CSV file to attach')
    parser.add_argument('--mode', type=str, choices=['daily', 'adhoc'], default='daily',
                        help='Pipeline mode: daily or adhoc (default: daily)')
    parser.add_argument('--dry-run', action='store_true', help='Print email content without sending')
    parser.add_argument('--from-pipeline', action='store_true', 
                        help='Running from pipeline.py - use GIT_USERNAME for commits')
    return parser.parse_args()


def analyze_spider_log(log_path):
    """
    Analyze spider log to detect critical errors
    Returns: (is_critical_error, error_message, log_exists)
    """
    if not os.path.exists(log_path):
        return False, "Spider log file not found (script may not have run)", False
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    # Check for explicit proxy ban detection (highest priority)
    if 'CRITICAL: PROXY BAN DETECTED DURING THIS RUN' in log_content:
        return True, "Proxy ban detected - one or more proxies were blocked by JavDB", True
    
    # Check for fallback mechanism failures (no movie list after all retries)
    fallback_failures = log_content.count('No movie list found after all fallback attempts')
    if fallback_failures >= 3:
        return True, f"CF bypass and proxy fallback failed - movie list not found on {fallback_failures} pages", True
    
    # Check for proxy being marked as banned during fetch
    proxy_ban_markers = log_content.count('Marking BANNED and switching')
    if proxy_ban_markers > 0:
        return True, f"Proxy marked as banned during fetch ({proxy_ban_markers} times) - proxy IP may be blocked", True
    
    # First check if we got any results at all
    total_entries_match = re.search(r'Total entries found: (\d+)', log_content)
    if total_entries_match:
        total_entries = int(total_entries_match.group(1))
        if total_entries > 0:
            return False, None, True
    
    # Check if we successfully processed any pages
    if 'Successfully fetched URL:' in log_content:
        return False, None, True
    
    # Check for movie list issues
    no_movie_list_count = len(re.findall(r'no movie list found', log_content, re.IGNORECASE))
    if no_movie_list_count >= 3:
        return True, f"Cannot retrieve movie list from JavDB - {no_movie_list_count} pages failed", True
    
    # Count consecutive fetch errors
    phase1_errors = 0
    phase2_errors = 0
    current_phase = None
    
    lines = log_content.split('\n')
    for line in lines:
        if 'PHASE 1:' in line:
            current_phase = 1
        elif 'PHASE 2:' in line:
            current_phase = 2
        elif 'OVERALL SUMMARY' in line:
            break
        
        if 'Error fetching' in line and ('403 Client Error: Forbidden' in line or '500 Server Error' in line):
            if current_phase == 1:
                phase1_errors += 1
            elif current_phase == 2:
                phase2_errors += 1
        elif ('Successfully fetched URL' in line) or ('Found' in line and 'entries' in line):
            if current_phase == 1:
                phase1_errors = 0
            elif current_phase == 2:
                phase2_errors = 0
    
    if phase1_errors >= 3 and phase2_errors >= 3:
        if '403 Client Error: Forbidden' in log_content:
            return True, "Cannot access JavDB - 403 Forbidden (proxy blocked or requires authentication)", True
        else:
            return True, "Cannot access JavDB main site - all pages failed with 500 errors", True
    
    # Check for other critical network errors
    critical_patterns = [
        ("Cannot connect to JavDB", "Cannot connect to JavDB"),
        ("Connection refused", "Connection refused to JavDB"),
        ("Connection timeout", "Connection timeout to JavDB"),
        ("Network is unreachable", "Network unreachable"),
        ("Max retries exceeded", "Max retries exceeded"),
    ]
    
    for pattern, message in critical_patterns:
        if pattern in log_content:
            error_count = log_content.count(pattern)
            if error_count >= 3:
                return True, f"Critical network error: {message}", True
    
    return False, None, True


def analyze_uploader_log(log_path):
    """
    Analyze uploader log to detect critical errors
    Returns: (is_critical_error, error_message, log_exists)
    """
    if not os.path.exists(log_path):
        return False, "Uploader log file not found (script may not have run)", False
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    critical_patterns = [
        "Cannot connect to qBittorrent",
        "Failed to login to qBittorrent",
        "Connection refused",
        "Network is unreachable"
    ]
    
    for pattern in critical_patterns:
        if pattern in log_content:
            return True, f"Cannot access qBittorrent: {pattern}", True
    
    # Check if we attempted to add torrents but all failed
    if 'Starting to add' in log_content and 'Failed to add:' in log_content:
        match = re.search(r'Successfully added: (\d+)', log_content)
        if match and int(match.group(1)) == 0:
            failed_match = re.search(r'Failed to add: (\d+)', log_content)
            if failed_match and int(failed_match.group(1)) > 0:
                return True, "All torrent additions failed", True
    
    return False, None, True


def analyze_pikpak_log(log_path):
    """
    Analyze PikPak log to detect critical errors
    Returns: (is_critical_error, error_message, log_exists)
    """
    if not os.path.exists(log_path):
        # PikPak is optional, so missing log is not critical
        return False, None, False
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    critical_patterns = [
        "qBittorrent login failed",
        "Failed to login qBittorrent",
        "Connection refused"
    ]
    
    for pattern in critical_patterns:
        if pattern in log_content:
            return True, f"Cannot access qBittorrent in PikPak bridge: {pattern}", True
    
    return False, None, True


def analyze_pipeline_log(log_path):
    """
    Analyze pipeline log to detect script execution failures.
    This catches cases where sub-scripts fail to start or crash early.
    Returns: (is_critical_error, error_message, log_exists)
    """
    if not os.path.exists(log_path):
        # Pipeline log missing is not critical in GitHub Actions context
        return False, "Pipeline log file not found", False
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    # Check for script execution failures
    script_failures = []
    
    # Pattern: "Script scripts/xxx.py failed with return code X"
    failure_pattern = r'Script (scripts/\w+\.py) failed with return code (\d+)'
    failures = re.findall(failure_pattern, log_content)
    for script, code in failures:
        script_name = os.path.basename(script).replace('.py', '')
        script_failures.append(f"{script_name} (exit code {code})")
    
    # Check for "PIPELINE EXECUTION ERROR" marker
    if 'PIPELINE EXECUTION ERROR' in log_content:
        if script_failures:
            return True, f"Pipeline scripts failed: {', '.join(script_failures)}", True
        return True, "Pipeline execution error detected", True
    
    # Check for IndentationError, SyntaxError, etc. in the output
    syntax_errors = [
        (r'IndentationError:', 'Syntax error (IndentationError)'),
        (r'SyntaxError:', 'Syntax error (SyntaxError)'),
        (r'ModuleNotFoundError:', 'Missing module dependency'),
        (r'ImportError:', 'Import error'),
    ]
    
    for pattern, message in syntax_errors:
        if re.search(pattern, log_content):
            return True, message, True
    
    return False, None, True


def extract_spider_statistics(log_path):
    """
    Extract key statistics from spider log for email report.
    
    Statistics terminology:
    - "movies" = unique movie pages discovered
    - "processed" = movies successfully parsed and written to CSV
    - "skipped" = movies skipped (either in this session or from history)
    
    Note: Each movie can have multiple torrent links (subtitle, no_subtitle, etc.)
    """
    stats = {
        'phase1': {'discovered': None, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'phase2': {'discovered': None, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'overall': {'total_discovered': None, 'successfully_processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0}
    }
    
    if not os.path.exists(log_path):
        return stats
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract phase 1 statistics from newest format (with no_new_torrents):
        # "Phase 1 completed: X movies discovered, Y processed, Z skipped (session), W skipped (history), N no new torrents, F failed"
        phase1_with_no_new = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) no new torrents, (\d+) failed',
            content
        )
        # Fallback to format with failed but no no_new_torrents:
        # "Phase 1 completed: X movies discovered, Y processed, Z skipped (session), W skipped (history), F failed"
        phase1_with_failed = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) failed',
            content
        )
        # Fallback to intermediate format (without failed):
        # "Phase 1 completed: X movies discovered, Y processed, Z skipped (session), W skipped (history)"
        phase1_new = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\)',
            content
        )
        # Fallback to old format: "Phase 1 completed: X found, Y skipped (history), Z written to CSV"
        phase1_old = re.search(r'Phase 1 completed: (\d+) found, (\d+) skipped.*?, (\d+) written to CSV', content)
        
        if phase1_with_no_new:
            stats['phase1']['discovered'] = int(phase1_with_no_new.group(1))
            stats['phase1']['processed'] = int(phase1_with_no_new.group(2))
            stats['phase1']['skipped_session'] = int(phase1_with_no_new.group(3))
            stats['phase1']['skipped_history'] = int(phase1_with_no_new.group(4))
            stats['phase1']['no_new_torrents'] = int(phase1_with_no_new.group(5))
            stats['phase1']['failed'] = int(phase1_with_no_new.group(6))
        elif phase1_with_failed:
            stats['phase1']['discovered'] = int(phase1_with_failed.group(1))
            stats['phase1']['processed'] = int(phase1_with_failed.group(2))
            stats['phase1']['skipped_session'] = int(phase1_with_failed.group(3))
            stats['phase1']['skipped_history'] = int(phase1_with_failed.group(4))
            stats['phase1']['failed'] = int(phase1_with_failed.group(5))
        elif phase1_new:
            stats['phase1']['discovered'] = int(phase1_new.group(1))
            stats['phase1']['processed'] = int(phase1_new.group(2))
            stats['phase1']['skipped_session'] = int(phase1_new.group(3))
            stats['phase1']['skipped_history'] = int(phase1_new.group(4))
        elif phase1_old:
            stats['phase1']['discovered'] = int(phase1_old.group(1))
            stats['phase1']['processed'] = int(phase1_old.group(3))
            stats['phase1']['skipped_history'] = int(phase1_old.group(2))
        
        # Extract phase 2 statistics from newest format (with no_new_torrents)
        phase2_with_no_new = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) no new torrents, (\d+) failed',
            content
        )
        # Fallback to format with failed but no no_new_torrents
        phase2_with_failed = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) failed',
            content
        )
        phase2_new = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\)',
            content
        )
        phase2_old = re.search(r'Phase 2 completed: (\d+) found, (\d+) skipped.*?, (\d+) written to CSV', content)
        
        if phase2_with_no_new:
            stats['phase2']['discovered'] = int(phase2_with_no_new.group(1))
            stats['phase2']['processed'] = int(phase2_with_no_new.group(2))
            stats['phase2']['skipped_session'] = int(phase2_with_no_new.group(3))
            stats['phase2']['skipped_history'] = int(phase2_with_no_new.group(4))
            stats['phase2']['no_new_torrents'] = int(phase2_with_no_new.group(5))
            stats['phase2']['failed'] = int(phase2_with_no_new.group(6))
        elif phase2_with_failed:
            stats['phase2']['discovered'] = int(phase2_with_failed.group(1))
            stats['phase2']['processed'] = int(phase2_with_failed.group(2))
            stats['phase2']['skipped_session'] = int(phase2_with_failed.group(3))
            stats['phase2']['skipped_history'] = int(phase2_with_failed.group(4))
            stats['phase2']['failed'] = int(phase2_with_failed.group(5))
        elif phase2_new:
            stats['phase2']['discovered'] = int(phase2_new.group(1))
            stats['phase2']['processed'] = int(phase2_new.group(2))
            stats['phase2']['skipped_session'] = int(phase2_new.group(3))
            stats['phase2']['skipped_history'] = int(phase2_new.group(4))
        elif phase2_old:
            stats['phase2']['discovered'] = int(phase2_old.group(1))
            stats['phase2']['processed'] = int(phase2_old.group(3))
            stats['phase2']['skipped_history'] = int(phase2_old.group(2))
        
        # Extract overall statistics from new format:
        # "Total movies discovered: X"
        total_discovered_new = re.search(r'Total movies discovered: (\d+)', content)
        total_discovered_old = re.search(r'Total entries found: (\d+)', content)
        if total_discovered_new:
            stats['overall']['total_discovered'] = int(total_discovered_new.group(1))
        elif total_discovered_old:
            stats['overall']['total_discovered'] = int(total_discovered_old.group(1))
        
        successfully_processed = re.search(r'Successfully processed: (\d+)', content)
        if successfully_processed:
            stats['overall']['successfully_processed'] = int(successfully_processed.group(1))
        
        skipped_session = re.search(r'Skipped already parsed in this session: (\d+)', content)
        if skipped_session:
            stats['overall']['skipped_session'] = int(skipped_session.group(1))
        
        skipped_history = re.search(r'Skipped already parsed in previous runs: (\d+)', content)
        if skipped_history:
            stats['overall']['skipped_history'] = int(skipped_history.group(1))
        
        no_new_torrents = re.search(r'No new torrents to download: (\d+)', content)
        if no_new_torrents:
            stats['overall']['no_new_torrents'] = int(no_new_torrents.group(1))
        
        failed = re.search(r'Failed to fetch/parse: (\d+)', content)
        if failed:
            stats['overall']['failed'] = int(failed.group(1))
        
        # If overall total_discovered wasn't found (is None), calculate from phase totals
        if stats['overall']['total_discovered'] is None:
            stats['overall']['total_discovered'] = (
                stats['overall']['successfully_processed'] + 
                stats['overall']['skipped_session'] + 
                stats['overall']['skipped_history'] +
                stats['overall']['no_new_torrents'] +
                stats['overall']['failed']
            )
        
        return stats
        
    except Exception as e:
        logger.warning(f"Failed to extract spider statistics: {e}")
        return stats


def extract_uploader_statistics(log_path):
    """Extract key statistics from uploader log for email report."""
    stats = {
        'total': 0,
        'success': 0,
        'failed': 0,
        'hacked_sub': 0,
        'hacked_nosub': 0,
        'subtitle': 0,
        'no_subtitle': 0,
        'success_rate': 0.0
    }
    
    if not os.path.exists(log_path):
        return stats
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        total = re.search(r'Total torrents in CSV: (\d+)', content)
        if total:
            stats['total'] = int(total.group(1))
        
        success = re.search(r'Successfully added: (\d+)', content)
        if success:
            stats['success'] = int(success.group(1))
        
        failed = re.search(r'Failed to add: (\d+)', content)
        if failed:
            stats['failed'] = int(failed.group(1))
        
        hacked_sub = re.search(r'Hacked subtitle torrents: (\d+)', content)
        if hacked_sub:
            stats['hacked_sub'] = int(hacked_sub.group(1))
        
        hacked_nosub = re.search(r'Hacked no subtitle torrents: (\d+)', content)
        if hacked_nosub:
            stats['hacked_nosub'] = int(hacked_nosub.group(1))
        
        subtitle = re.search(r'Subtitle torrents: (\d+)', content)
        if subtitle:
            stats['subtitle'] = int(subtitle.group(1))
        
        no_subtitle = re.search(r'No subtitle torrents: (\d+)', content)
        if no_subtitle:
            stats['no_subtitle'] = int(no_subtitle.group(1))
        
        success_rate = re.search(r'Success rate: ([\d.]+)%', content)
        if success_rate:
            stats['success_rate'] = float(success_rate.group(1))
        
        return stats
        
    except Exception as e:
        logger.warning(f"Failed to extract uploader statistics: {e}")
        return stats


def extract_pikpak_statistics(log_path):
    """Extract key statistics from PikPak log for email report."""
    stats = {
        'total_torrents': 0,
        'filtered_old': 0,
        'added_to_pikpak': 0,
        'removed_from_qb': 0,
        'failed': 0,
        'threshold_days': 3
    }
    
    if not os.path.exists(log_path):
        return stats
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        threshold = re.search(r'older than (\d+) days', content)
        if threshold:
            stats['threshold_days'] = int(threshold.group(1))
        
        total = re.search(r'Found (\d+) torrents', content)
        if total:
            stats['total_torrents'] = int(total.group(1))
        
        filtered = re.search(r'Filtered (\d+) torrents older than', content)
        if filtered:
            stats['filtered_old'] = int(filtered.group(1))
        
        added_pattern = r'Successfully added to PikPak'
        stats['added_to_pikpak'] = len(re.findall(added_pattern, content))
        
        removed_pattern = r'Removed from qBittorrent'
        stats['removed_from_qb'] = len(re.findall(removed_pattern, content))
        
        failed_pattern = r'Failed to (add|remove)'
        stats['failed'] = len(re.findall(failed_pattern, content, re.IGNORECASE))
        
        return stats
        
    except Exception as e:
        logger.warning(f"Failed to extract PikPak statistics: {e}")
        return stats


def get_proxy_ban_summary():
    """Get proxy ban summary for email notification"""
    try:
        from utils.proxy_ban_manager import get_ban_manager
        ban_manager = get_ban_manager()
        return ban_manager.get_ban_summary(include_ip=True)
    except Exception as e:
        logger.warning(f"Failed to get proxy ban summary: {e}")
        return "Proxy ban information not available."


def find_proxy_ban_html_files(logs_dir='logs'):
    """
    Find all proxy ban HTML files in the logs directory.
    
    These files are created by spider.py when a proxy is banned,
    and contain the HTML response that caused the ban.
    
    Args:
        logs_dir: Directory to search for proxy ban HTML files
    
    Returns:
        list: List of file paths to proxy ban HTML files
    """
    import glob
    
    if not os.path.exists(logs_dir):
        return []
    
    pattern = os.path.join(logs_dir, 'proxy_ban_*.txt')
    files = glob.glob(pattern)
    
    if files:
        logger.info(f"Found {len(files)} proxy ban HTML file(s)")
        for f in files:
            logger.info(f"  - {f}")
    
    return files


def extract_proxy_ban_summary(html_files):
    """
    Extract a summary from proxy ban HTML files for the email body.
    
    Args:
        html_files: List of proxy ban HTML file paths
    
    Returns:
        str: Summary text for email body, or None if no files
    """
    if not html_files:
        return None
    
    summaries = []
    for filepath in html_files:
        try:
            filename = os.path.basename(filepath)
            file_size = os.path.getsize(filepath)
            
            # Read first 6 lines to get metadata (header only)
            # Header format from save_proxy_ban_html:
            #   Line 1: # Proxy Ban HTML Capture
            #   Line 2: # Proxy: {proxy_name}
            #   Line 3: # Page: {page_num}
            #   Line 4: # Timestamp: ...
            #   Line 5: # HTML Length: ...
            #   Line 6: ====...====
            with open(filepath, 'r', encoding='utf-8') as f:
                header_lines = []
                for i, line in enumerate(f):
                    if i >= 6:  # Only read first 6 lines (header only, not HTML content)
                        break
                    header_lines.append(line.rstrip())
            
            # Extract metadata from header only (avoid false matches in HTML content)
            proxy_name = "Unknown"
            page_num = "Unknown"
            for line in header_lines:
                if line.startswith("# Proxy:"):
                    proxy_name = line.replace("# Proxy:", "").strip()
                elif line.startswith("# Page:"):
                    page_num = line.replace("# Page:", "").strip()
            
            summaries.append(f"  • {filename}\n    Proxy: {proxy_name}, Page: {page_num}, Size: {file_size} bytes")
            
        except Exception as e:
            logger.warning(f"Failed to extract summary from {filepath}: {e}")
            summaries.append(f"  • {os.path.basename(filepath)} (could not read)")
    
    if summaries:
        return "Proxy Ban HTML Files Captured:\n" + "\n".join(summaries)
    return None


def format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary,
                        show_spider=True, show_uploader=True, show_pikpak=True,
                        mode='daily', adhoc_info=None, proxy_ban_html_summary=None):
    """
    Format a mobile-friendly email report.
    Only includes sections for components that ran successfully.
    
    Args:
        mode: 'daily' or 'adhoc'
        adhoc_info: Formatted Ad-Hoc info string (e.g., "Actor: 森日向子")
        proxy_ban_html_summary: Summary of proxy ban HTML files captured (if any)
    """
    sections = []
    
    # Determine mode display
    if mode == 'adhoc':
        mode_display = "Ad-Hoc"
        if adhoc_info:
            mode_detail = f"Mode: {mode_display}\nTarget: {adhoc_info}"
        else:
            mode_detail = f"Mode: {mode_display}"
    else:
        mode_display = "Daily"
        mode_detail = f"Mode: {mode_display}"
    
    # Header
    sections.append(f"""
═══════════════════════════════
JavDB Pipeline Report ({mode_display})
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
═══════════════════════════════

{mode_detail}""")
    
    # Spider section
    # Note: Statistics are for MOVIES (unique pages), not individual torrent links
    if show_spider:
        # Calculate totals for verification (include failed count and no_new_torrents)
        p1_total = spider_stats['phase1']['processed'] + spider_stats['phase1']['skipped_session'] + spider_stats['phase1']['skipped_history'] + spider_stats['phase1']['failed'] + spider_stats['phase1'].get('no_new_torrents', 0)
        p2_total = spider_stats['phase2']['processed'] + spider_stats['phase2']['skipped_session'] + spider_stats['phase2']['skipped_history'] + spider_stats['phase2']['failed'] + spider_stats['phase2'].get('no_new_torrents', 0)
        overall_total = spider_stats['overall']['successfully_processed'] + spider_stats['overall']['skipped_session'] + spider_stats['overall']['skipped_history'] + spider_stats['overall']['failed'] + spider_stats['overall'].get('no_new_torrents', 0)
        
        # Use None check instead of `or` to handle 0 correctly
        # `or` treats 0 as falsy and would incorrectly fall back to calculated total
        p1_discovered = spider_stats['phase1']['discovered'] if spider_stats['phase1']['discovered'] is not None else p1_total
        p2_discovered = spider_stats['phase2']['discovered'] if spider_stats['phase2']['discovered'] is not None else p2_total
        overall_discovered = spider_stats['overall']['total_discovered'] if spider_stats['overall']['total_discovered'] is not None else overall_total
        
        sections.append(f"""
📊 SPIDER STATISTICS (Movies)
───────────────────────────────

Phase 1 (Subtitle + Today/Yesterday)
  Discovered: {p1_discovered}
  Processed:  {spider_stats['phase1']['processed']}
  Skipped (Session): {spider_stats['phase1']['skipped_session']}
  Skipped (History): {spider_stats['phase1']['skipped_history']}
  No New Torrents: {spider_stats['phase1'].get('no_new_torrents', 0)}
  Failed: {spider_stats['phase1']['failed']}

Phase 2 (Rate>4.0, Comments>85)
  Discovered: {p2_discovered}
  Processed:  {spider_stats['phase2']['processed']}
  Skipped (Session): {spider_stats['phase2']['skipped_session']}
  Skipped (History): {spider_stats['phase2']['skipped_history']}
  No New Torrents: {spider_stats['phase2'].get('no_new_torrents', 0)}
  Failed: {spider_stats['phase2']['failed']}

Overall Summary
  Total Discovered: {overall_discovered}
  Processed:  {spider_stats['overall']['successfully_processed']}
  Skipped (Session): {spider_stats['overall']['skipped_session']}
  Skipped (History): {spider_stats['overall']['skipped_history']}
  No New Torrents: {spider_stats['overall'].get('no_new_torrents', 0)}
  Failed: {spider_stats['overall']['failed']}""")
    
    # Uploader section
    if show_uploader:
        sections.append(f"""
───────────────────────────────
📤 QBITTORRENT UPLOADER
───────────────────────────────

Upload Summary
  Total: {uploader_stats['total']}
  Success: {uploader_stats['success']} ({uploader_stats['success_rate']:.1f}%)
  Failed: {uploader_stats['failed']}

Breakdown by Type
  Hacked (Sub): {uploader_stats['hacked_sub']}
  Hacked (NoSub): {uploader_stats['hacked_nosub']}
  Regular (Sub): {uploader_stats['subtitle']}
  Regular (NoSub): {uploader_stats['no_subtitle']}""")
    
    # PikPak section
    if show_pikpak:
        sections.append(f"""
───────────────────────────────
🔄 PIKPAK BRIDGE
───────────────────────────────

Cleanup (>{pikpak_stats['threshold_days']} days)
  Scanned: {pikpak_stats['total_torrents']}
  Filtered: {pikpak_stats['filtered_old']}
  Added to PikPak: {pikpak_stats['added_to_pikpak']}
  Removed from QB: {pikpak_stats['removed_from_qb']}
  Failed: {pikpak_stats['failed']}""")
    
    # Proxy ban HTML files section (only show if there are captured files)
    if proxy_ban_html_summary:
        sections.append(f"""
───────────────────────────────
📄 PROXY BAN DEBUG FILES
───────────────────────────────

{proxy_ban_html_summary}

(See attached .txt files for full HTML content)""")
    
    # Proxy status (always show)
    sections.append(f"""
───────────────────────────────
🚦 PROXY STATUS
───────────────────────────────

{ban_summary}

═══════════════════════════════
End of Report
═══════════════════════════════""")
    
    return "\n".join(sections)


def convert_log_to_txt(log_path):
    """
    Convert log file to txt file for email attachment.
    Returns the path to the txt file.
    """
    if not os.path.exists(log_path):
        return None
    
    # Create txt path by replacing extension
    base_name = os.path.basename(log_path)
    name_without_ext = os.path.splitext(base_name)[0]
    txt_filename = f"{name_without_ext}.txt"
    txt_path = os.path.join(os.path.dirname(log_path), txt_filename)
    
    # Copy content to txt file
    shutil.copy2(log_path, txt_path)
    logger.debug(f"Converted {log_path} to {txt_path}")
    
    return txt_path


def send_email(subject, body, attachments=None, dry_run=False):
    """Send email with attachments"""
    if dry_run:
        logger.info("=" * 60)
        logger.info("[DRY RUN] Email would be sent:")
        logger.info(f"Subject: {subject}")
        logger.info(f"From: {mask_email(EMAIL_FROM)}")
        logger.info(f"To: {mask_email(EMAIL_TO)}")
        logger.info("Body:")
        logger.info(body)
        if attachments:
            logger.info(f"Attachments: {attachments}")
        logger.info("=" * 60)
        return True
    
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg.set_content(body)

    if attachments:
        for file_path in attachments:
            if not os.path.exists(file_path):
                logger.warning(f'Attachment not found: {file_path}')
                continue
            with open(file_path, 'rb') as f:
                file_data = f.read()
                file_name = os.path.basename(file_path)
                maintype = 'application'
                subtype = 'octet-stream'
                msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=file_name)

    logger.info(f'Connecting to SMTP server {mask_server(SMTP_SERVER)}:{SMTP_PORT}...')
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info(f'Email sent successfully to {mask_email(EMAIL_TO)}.')
        return True
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
        return False


def check_workflow_job_status():
    """
    Check workflow job status from environment variable or status file.
    This is used to detect failures in GitHub Actions jobs (e.g., health-check failure)
    that may not produce log files.
    
    Returns:
        tuple: (has_job_failure: bool, failed_jobs: list of str)
    """
    failed_jobs = []
    
    # Check environment variable set by workflow
    pipeline_has_failure = os.environ.get('PIPELINE_HAS_FAILURE', 'false').lower()
    if pipeline_has_failure == 'true':
        logger.info("PIPELINE_HAS_FAILURE environment variable indicates failure")
    
    # Check job status file created by workflow
    job_status_file = 'logs/job_status.txt'
    if os.path.exists(job_status_file):
        try:
            with open(job_status_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line:
                        key, value = line.split('=', 1)
                        if value.lower() in ('failure', 'cancelled'):
                            job_name = key.replace('_STATUS', '').replace('_', ' ').title()
                            failed_jobs.append(f"{job_name}: {value}")
                            logger.error(f"Job failure detected: {job_name} = {value}")
                        elif value.lower() == 'skipped':
                            # Skipped jobs indicate upstream failures
                            job_name = key.replace('_STATUS', '').replace('_', ' ').title()
                            logger.warning(f"Job skipped (upstream failure): {job_name}")
        except Exception as e:
            logger.warning(f"Failed to read job status file: {e}")
    
    has_job_failure = pipeline_has_failure == 'true' or len(failed_jobs) > 0
    return has_job_failure, failed_jobs


def main():
    args = parse_arguments()
    
    logger.info("=" * 60)
    logger.info("EMAIL NOTIFICATION SCRIPT")
    logger.info("=" * 60)
    
    # First, check workflow job status (for GitHub Actions)
    # This catches failures that don't produce log files (e.g., health-check failure)
    has_job_failure, failed_jobs = check_workflow_job_status()
    
    # Analyze logs for critical errors
    # Each analyze function returns: (is_critical_error, error_message, log_exists)
    logger.info("Analyzing logs for critical errors...")
    pipeline_errors = []
    
    # Add any job failures detected from workflow status
    if failed_jobs:
        for job_failure in failed_jobs:
            pipeline_errors.append(job_failure)
    
    # Track which components have valid logs (for dynamic email sections)
    spider_log_exists = False
    uploader_log_exists = False
    pikpak_log_exists = False
    
    # First, check pipeline log for script execution failures
    # This catches cases where sub-scripts crash before writing to their own logs
    pipeline_critical, pipeline_error, pipeline_exists = analyze_pipeline_log(PIPELINE_LOG_FILE)
    if pipeline_critical:
        logger.error(f"CRITICAL ERROR in Pipeline: {pipeline_error}")
        pipeline_errors.append(f"Pipeline: {pipeline_error}")
    elif not pipeline_exists:
        logger.warning(f"Pipeline log not found: {PIPELINE_LOG_FILE}")
    
    spider_critical, spider_error, spider_log_exists = analyze_spider_log(SPIDER_LOG_FILE)
    if spider_critical:
        logger.error(f"CRITICAL ERROR in Spider: {spider_error}")
        pipeline_errors.append(f"Spider: {spider_error}")
    elif not spider_log_exists:
        logger.warning(f"Spider log not found: {SPIDER_LOG_FILE}")
    
    uploader_critical, uploader_error, uploader_log_exists = analyze_uploader_log(UPLOADER_LOG_FILE)
    if uploader_critical:
        logger.error(f"CRITICAL ERROR in Uploader: {uploader_error}")
        pipeline_errors.append(f"Uploader: {uploader_error}")
    elif not uploader_log_exists:
        logger.warning(f"Uploader log not found: {UPLOADER_LOG_FILE}")
    
    pikpak_critical, pikpak_error, pikpak_log_exists = analyze_pikpak_log(PIKPAK_LOG_FILE)
    if pikpak_critical:
        logger.error(f"CRITICAL ERROR in PikPak: {pikpak_error}")
        pipeline_errors.append(f"PikPak: {pikpak_error}")
    elif not pikpak_log_exists:
        logger.warning(f"PikPak log not found (optional): {PIKPAK_LOG_FILE}")
    
    # Determine if we have critical errors (from logs OR from workflow job status)
    has_critical_errors = len(pipeline_errors) > 0 or has_job_failure
    
    # Extract statistics (only if log exists)
    spider_stats = extract_spider_statistics(SPIDER_LOG_FILE) if spider_log_exists else None
    uploader_stats = extract_uploader_statistics(UPLOADER_LOG_FILE) if uploader_log_exists else None
    pikpak_stats = extract_pikpak_statistics(PIKPAK_LOG_FILE) if pikpak_log_exists else None
    ban_summary = get_proxy_ban_summary()
    
    # Determine pipeline mode and CSV path
    mode = args.mode
    
    # Determine CSV path (using dated subdirectory YYYY/MM)
    # Note: We use wildcard-based discovery (not date-specific) to handle cross-midnight
    # scenarios where spider runs before midnight but email notification runs after midnight
    if args.csv_path:
        csv_path = args.csv_path
        # Auto-detect mode from CSV path if not explicitly set and path looks like adhoc
        if 'AdHoc' in csv_path or 'Javdb_AdHoc_' in csv_path:
            mode = 'adhoc'
    else:
        if mode == 'adhoc':
            # For adhoc mode without explicit path, try to find the latest adhoc CSV
            csv_path = find_latest_adhoc_csv(AD_HOC_DIR)
        else:
            # For daily mode without explicit path, try to find the latest daily CSV
            csv_path = find_latest_daily_csv(DAILY_REPORT_DIR)
    
    # Extract Ad-Hoc information from CSV filename
    adhoc_url_type, adhoc_display_name = extract_adhoc_info_from_csv(csv_path)
    adhoc_info = format_adhoc_info(adhoc_url_type, adhoc_display_name) if adhoc_url_type else None
    
    logger.info(f"Pipeline mode: {mode}")
    logger.info(f"CSV path: {csv_path}")
    if adhoc_info:
        logger.info(f"Ad-Hoc target: {adhoc_info}")
    
    # Convert log files to txt for attachment
    txt_attachments = []
    log_files = [SPIDER_LOG_FILE, UPLOADER_LOG_FILE, PIKPAK_LOG_FILE, PIPELINE_LOG_FILE, EMAIL_NOTIFICATION_LOG_FILE]
    
    for log_file in log_files:
        txt_path = convert_log_to_txt(log_file)
        if txt_path:
            txt_attachments.append(txt_path)
    
    # Find and include proxy ban HTML files (these are already .txt files)
    proxy_ban_html_files = find_proxy_ban_html_files('logs')
    proxy_ban_summary = extract_proxy_ban_summary(proxy_ban_html_files)
    
    # Add CSV if exists
    attachments = txt_attachments.copy()
    if os.path.exists(csv_path):
        attachments.insert(0, csv_path)
    
    # Add proxy ban HTML files to attachments
    for html_file in proxy_ban_html_files:
        if os.path.exists(html_file):
            attachments.append(html_file)
    
    # Prepare default stats for missing components
    default_spider_stats = {
        'phase1': {'discovered': 0, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'failed': 0},
        'phase2': {'discovered': 0, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'failed': 0},
        'overall': {'total_discovered': 0, 'successfully_processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'failed': 0}
    }
    default_uploader_stats = {
        'total': 0, 'success': 0, 'failed': 0, 'hacked_sub': 0,
        'hacked_nosub': 0, 'subtitle': 0, 'no_subtitle': 0, 'success_rate': 0.0
    }
    default_pikpak_stats = {
        'total_torrents': 0, 'filtered_old': 0, 'added_to_pikpak': 0,
        'removed_from_qb': 0, 'failed': 0, 'threshold_days': 3
    }
    
    # Use actual stats or defaults
    final_spider_stats = spider_stats if spider_stats else default_spider_stats
    final_uploader_stats = uploader_stats if uploader_stats else default_uploader_stats
    final_pikpak_stats = pikpak_stats if pikpak_stats else default_pikpak_stats
    
    # Prepare mode display for subject line
    mode_display = "Ad-Hoc" if mode == 'adhoc' else "Daily"
    
    # Prepare short adhoc info for subject (if applicable)
    adhoc_subject_suffix = ""
    if mode == 'adhoc' and adhoc_display_name:
        # Truncate display name if too long for subject
        short_name = adhoc_display_name[:20] + "..." if len(adhoc_display_name) > 20 else adhoc_display_name
        adhoc_subject_suffix = f" [{short_name}]"
    
    # Send email based on status
    if not has_critical_errors:
        body = format_email_report(
            final_spider_stats, final_uploader_stats, final_pikpak_stats, ban_summary,
            show_spider=spider_log_exists,
            show_uploader=uploader_log_exists,
            show_pikpak=pikpak_log_exists,
            mode=mode,
            adhoc_info=adhoc_info,
            proxy_ban_html_summary=proxy_ban_summary
        )
        subject = f'✓ SUCCESS - JavDB {mode_display} Report {today_str}{adhoc_subject_suffix}'
    else:
        error_details = "\n".join([f"  • {error}" for error in pipeline_errors])
        stats_report = format_email_report(
            final_spider_stats, final_uploader_stats, final_pikpak_stats, ban_summary,
            show_spider=spider_log_exists and not spider_critical,
            show_uploader=uploader_log_exists and not uploader_critical,
            show_pikpak=pikpak_log_exists and not pikpak_critical,
            mode=mode,
            adhoc_info=adhoc_info,
            proxy_ban_html_summary=proxy_ban_summary
        )
        
        # Add mode info to failure report header
        mode_info_line = f"Mode: {mode_display}"
        if adhoc_info:
            mode_info_line += f"\nTarget: {adhoc_info}"
        
        body = f"""
═══════════════════════════════
⚠️  PIPELINE FAILED ({mode_display})  ⚠️
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
═══════════════════════════════

{mode_info_line}

🚨 CRITICAL ERRORS

{error_details}

Check attached logs for details.

{stats_report}
"""
        subject = f'✗ FAILED - JavDB {mode_display} Report {today_str}{adhoc_subject_suffix}'
    
    # Send email
    email_sent = send_email(subject, body, attachments, args.dry_run)
    
    # Clean up temporary txt files
    for txt_path in txt_attachments:
        if txt_path and os.path.exists(txt_path):
            try:
                os.remove(txt_path)
                logger.debug(f"Cleaned up temporary file: {txt_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up {txt_path}: {e}")
    
    # Commit pipeline log (only if credentials are available)
    if not args.dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing pipeline log...")
        flush_log_handlers()
        
        files_to_commit = ['logs/']
        commit_message = f"Auto-commit: Pipeline notification {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        git_commit_and_push(
            files_to_add=files_to_commit,
            commit_message=commit_message,
            from_pipeline=args.from_pipeline,
            git_username=GIT_USERNAME,
            git_password=GIT_PASSWORD,
            git_repo_url=GIT_REPO_URL,
            git_branch=GIT_BRANCH
        )
    elif not args.dry_run:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")
    
    logger.info("=" * 60)
    logger.info("EMAIL NOTIFICATION COMPLETED")
    logger.info("=" * 60)
    
    # Exit with success - email notification itself succeeded
    # The email content will indicate if there were pipeline errors
    # This ensures the email notification job doesn't fail just because
    # some component scripts failed or logs are missing
    sys.exit(0)


if __name__ == '__main__':
    main()

