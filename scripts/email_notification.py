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
    DAILY_REPORT_DIR = 'Daily Report'
    AD_HOC_DIR = 'Ad Hoc'
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


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Email Notification for JavDB Pipeline')
    parser.add_argument('--csv-path', type=str, help='Path to the CSV file to attach')
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
        'phase1': {'discovered': 0, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'failed': 0},
        'phase2': {'discovered': 0, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'failed': 0},
        'overall': {'total_discovered': 0, 'successfully_processed': 0, 'skipped_session': 0, 'skipped_history': 0, 'failed': 0}
    }
    
    if not os.path.exists(log_path):
        return stats
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract phase 1 statistics from new format (with failed):
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
        
        if phase1_with_failed:
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
        
        # Extract phase 2 statistics from new format (with failed)
        phase2_with_failed = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) failed',
            content
        )
        phase2_new = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\)',
            content
        )
        phase2_old = re.search(r'Phase 2 completed: (\d+) found, (\d+) skipped.*?, (\d+) written to CSV', content)
        
        if phase2_with_failed:
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
        
        failed = re.search(r'Failed to fetch/parse: (\d+)', content)
        if failed:
            stats['overall']['failed'] = int(failed.group(1))
        
        # If overall total_discovered wasn't found, calculate from phase totals
        if stats['overall']['total_discovered'] == 0:
            stats['overall']['total_discovered'] = (
                stats['overall']['successfully_processed'] + 
                stats['overall']['skipped_session'] + 
                stats['overall']['skipped_history'] +
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
        
        total = re.search(r'Total torrents found: (\d+)', content)
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


def format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary,
                        show_spider=True, show_uploader=True, show_pikpak=True):
    """
    Format a mobile-friendly email report.
    Only includes sections for components that ran successfully.
    """
    sections = []
    
    # Header
    sections.append(f"""
═══════════════════════════════
JavDB Pipeline Report
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
═══════════════════════════════""")
    
    # Spider section
    # Note: Statistics are for MOVIES (unique pages), not individual torrent links
    if show_spider:
        # Calculate totals for verification (include failed count)
        p1_total = spider_stats['phase1']['processed'] + spider_stats['phase1']['skipped_session'] + spider_stats['phase1']['skipped_history'] + spider_stats['phase1']['failed']
        p2_total = spider_stats['phase2']['processed'] + spider_stats['phase2']['skipped_session'] + spider_stats['phase2']['skipped_history'] + spider_stats['phase2']['failed']
        overall_total = spider_stats['overall']['successfully_processed'] + spider_stats['overall']['skipped_session'] + spider_stats['overall']['skipped_history'] + spider_stats['overall']['failed']
        
        sections.append(f"""
📊 SPIDER STATISTICS (Movies)
───────────────────────────────

Phase 1 (Subtitle + Today/Yesterday)
  Discovered: {spider_stats['phase1']['discovered'] or p1_total}
  Processed:  {spider_stats['phase1']['processed']}
  Skipped (Session): {spider_stats['phase1']['skipped_session']}
  Skipped (History): {spider_stats['phase1']['skipped_history']}
  Failed: {spider_stats['phase1']['failed']}

Phase 2 (Rate>4.0, Comments>85)
  Discovered: {spider_stats['phase2']['discovered'] or p2_total}
  Processed:  {spider_stats['phase2']['processed']}
  Skipped (Session): {spider_stats['phase2']['skipped_session']}
  Skipped (History): {spider_stats['phase2']['skipped_history']}
  Failed: {spider_stats['phase2']['failed']}

Overall Summary
  Total Discovered: {spider_stats['overall']['total_discovered'] or overall_total}
  Processed:  {spider_stats['overall']['successfully_processed']}
  Skipped (Session): {spider_stats['overall']['skipped_session']}
  Skipped (History): {spider_stats['overall']['skipped_history']}
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
    
    # Determine CSV path
    today_str = datetime.now().strftime('%Y%m%d')
    if args.csv_path:
        csv_path = args.csv_path
    else:
        csv_path = os.path.join(DAILY_REPORT_DIR, f'Javdb_TodayTitle_{today_str}.csv')
    
    # Convert log files to txt for attachment
    txt_attachments = []
    log_files = [SPIDER_LOG_FILE, UPLOADER_LOG_FILE, PIKPAK_LOG_FILE, PIPELINE_LOG_FILE, EMAIL_NOTIFICATION_LOG_FILE]
    
    for log_file in log_files:
        txt_path = convert_log_to_txt(log_file)
        if txt_path:
            txt_attachments.append(txt_path)
    
    # Add CSV if exists
    attachments = txt_attachments.copy()
    if os.path.exists(csv_path):
        attachments.insert(0, csv_path)
    
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
    
    # Send email based on status
    if not has_critical_errors:
        body = format_email_report(
            final_spider_stats, final_uploader_stats, final_pikpak_stats, ban_summary,
            show_spider=spider_log_exists,
            show_uploader=uploader_log_exists,
            show_pikpak=pikpak_log_exists
        )
        subject = f'✓ SUCCESS - JavDB Pipeline Report {today_str}'
    else:
        error_details = "\n".join([f"  • {error}" for error in pipeline_errors])
        stats_report = format_email_report(
            final_spider_stats, final_uploader_stats, final_pikpak_stats, ban_summary,
            show_spider=spider_log_exists and not spider_critical,
            show_uploader=uploader_log_exists and not uploader_critical,
            show_pikpak=pikpak_log_exists and not pikpak_critical
        )
        
        body = f"""
═══════════════════════════════
⚠️  PIPELINE FAILED  ⚠️
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
═══════════════════════════════

🚨 CRITICAL ERRORS

{error_details}

Check attached logs for details.

{stats_report}
"""
        subject = f'✗ FAILED - JavDB Pipeline Report {today_str}'
    
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

