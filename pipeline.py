import subprocess
import smtplib
import logging
import os
import sys
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from email.mime.base import MIMEBase
from email import encoders
import argparse

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
    
    PIPELINE_LOG_FILE = 'logs/pipeline_run_and_notify.log'
    SPIDER_LOG_FILE = 'logs/Javdb_Spider.log'
    UPLOADER_LOG_FILE = 'logs/qbtorrent_uploader.log'
    DAILY_REPORT_DIR = 'Daily Report'
    AD_HOC_DIR = 'Ad Hoc'
    
    # PikPak bridge fallback values
    PIKPAK_LOG_FILE = 'logs/pikpak_bridge.log'

os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# --- LOGGING SETUP ---
from utils.logging_config import setup_logging, get_logger
setup_logging(PIPELINE_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import PikPak bridge functionality
from scripts.pikpak_bridge import pikpak_bridge

# --- FILE PATHS ---
today_str = datetime.now().strftime('%Y%m%d')
csv_path = os.path.join(DAILY_REPORT_DIR, f'Javdb_TodayTitle_{today_str}.csv')
spider_log_path = SPIDER_LOG_FILE
uploader_log_path = UPLOADER_LOG_FILE


def mask_sensitive_info(text):
    """Mask sensitive information in text to prevent exposure in logs"""
    if not text:
        return text
    
    # Mask GitHub personal access tokens (ghp_xxxxxxxxxx) - do this first
    text = re.sub(r'ghp_[a-zA-Z0-9]{35,}', 'ghp_***MASKED***', text)
    
    # Mask other potential GitHub tokens (gho_, ghr_, ghs_)
    text = re.sub(r'gh[o-r-s]_[a-zA-Z0-9]{35,}', 'gh*_***MASKED***', text)
    
    # Mask email passwords in SMTP URLs (but exclude GitHub URLs)
    # This regex matches username:password@ but only if it's not a GitHub URL
    def mask_email_password(match):
        username, password, domain = match.groups()
        if 'github.com' in domain:
            # Don't mask GitHub URLs as they're handled above
            return match.group(0)
        return f"{username}:***MASKED***@{domain}"
    
    text = re.sub(r'([a-zA-Z0-9._%+-]+):([^@]+)@([^/\s]+)', mask_email_password, text)
    
    # Mask qBittorrent passwords
    text = re.sub(r'password["\']?\s*[:=]\s*["\']?([^"\s]+)["\']?', r'password:***MASKED***', text)
    
    # Mask SMTP passwords
    text = re.sub(r'SMTP_PASSWORD["\']?\s*[:=]\s*["\']?([^"\s]+)["\']?', r'SMTP_PASSWORD:***MASKED***', text)
    
    return text


def safe_log_info(message):
    """Log message with sensitive information masked"""
    masked_message = mask_sensitive_info(message)
    logger.info(masked_message)


def safe_log_warning(message):
    """Log warning message with sensitive information masked"""
    masked_message = mask_sensitive_info(message)
    logger.warning(masked_message)


def safe_log_error(message):
    """Log error message with sensitive information masked"""
    masked_message = mask_sensitive_info(message)
    logger.error(masked_message)


# --- PIPELINE EXECUTION ---
def run_script(script_path, args=None):
    cmd = ['python3', script_path]
    if args:
        cmd += args
    logger.info(f'Running: {" ".join(cmd)}')

    # Run with real-time output
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    # Print output in real-time (but don't log to pipeline log since sub-scripts have their own logging)
    output_lines = []
    if process.stdout:
        for line in iter(process.stdout.readline, ''):
            if line:
                print(line.rstrip())  # Print to console only
                output_lines.append(line)
                # Don't log to pipeline log - sub-scripts have their own logging

        process.stdout.close()

    return_code = process.wait()

    if return_code != 0:
        logger.error(f'Script {script_path} failed with return code {return_code}')
        raise RuntimeError(f'Script {script_path} failed with return code {return_code}')
    
    return ''.join(output_lines)


def run_pikpak_bridge(days=3, dry_run=False, batch_mode=True, use_proxy=False):
    """Run PikPak Bridge to handle old torrents"""
    try:
        mode_str = "batch mode" if batch_mode else "individual mode"
        logger.info(f"Running PikPak Bridge with {days} days threshold, dry_run={dry_run}, using {mode_str}")
        pikpak_bridge(days, dry_run, batch_mode, use_proxy)
        logger.info("PikPak Bridge completed successfully")
    except Exception as e:
        logger.error(f"PikPak Bridge failed: {e}")
        raise


def get_log_summary(log_path, lines=200):
    if not os.path.exists(log_path):
        return f'Log file not found: {log_path}'
    with open(log_path, 'r', encoding='utf-8') as f:
        log_lines = f.readlines()
    return ''.join(log_lines[-lines:])


def extract_spider_summary(log_path):
    """
    Extract relevant summary section from spider log.
    Specifically looks for 'SUMMARY REPORT' and stops before 'PROXY POOL STATISTICS'.
    """
    if not os.path.exists(log_path):
        return f'Log file not found: {log_path}'
        
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Find the last occurrence of SUMMARY REPORT
        summary_start_marker = "SUMMARY REPORT"
        proxy_stats_marker = "PROXY POOL STATISTICS"
        
        start_idx = content.rfind(summary_start_marker)
        
        if start_idx != -1:
            # Find the start of the line containing SUMMARY REPORT (to include the separator line before it)
            # Go back to find the "=====" line before SUMMARY REPORT
            separator_search_start = max(0, start_idx - 100)
            pre_context = content[separator_search_start:start_idx]
            last_separator = pre_context.rfind("=====")
            
            if last_separator != -1:
                real_start_idx = separator_search_start + last_separator
                # Back up to the start of that line
                line_start = content.rfind('\n', 0, real_start_idx)
                if line_start != -1:
                    start_idx = line_start + 1
                else:
                    start_idx = real_start_idx
            
            # Determine end index
            end_idx = content.find(proxy_stats_marker, start_idx)
            
            if end_idx != -1:
                # Found proxy stats, cut off there
                # Also remove the separator line before proxy stats if possible
                separator_search_end = end_idx
                pre_proxy_context = content[start_idx:separator_search_end]
                last_separator_before_proxy = pre_proxy_context.rfind("=====")
                
                if last_separator_before_proxy != -1:
                    # Cut before the separator line of Proxy Stats
                    # Find start of that separator line
                    sep_line_start = pre_proxy_context.rfind('\n', 0, last_separator_before_proxy)
                    if sep_line_start != -1:
                        # Extract content
                        return content[start_idx : start_idx + sep_line_start + 1]
                
                return content[start_idx:end_idx]
            else:
                # No proxy stats found, take everything until end
                return content[start_idx:]
        
        # Fallback if no specific summary found
        return get_log_summary(log_path, lines=50)
        
    except Exception as e:
        logger.warning(f"Failed to extract spider summary: {e}")
        return get_log_summary(log_path, lines=50)


def analyze_spider_log(log_path):
    """
    Analyze spider log to detect critical errors
    Returns: (is_critical_error, error_message)
    """
    if not os.path.exists(log_path):
        return True, "Spider log file not found"
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    import re
    
    # Check for explicit proxy ban detection (highest priority)
    if 'CRITICAL: PROXY BAN DETECTED DURING THIS RUN' in log_content:
        return True, "Proxy ban detected - one or more proxies were blocked by JavDB"
    
    # Check for fallback mechanism failures (no movie list after all retries)
    fallback_failures = log_content.count('No movie list found after all fallback attempts')
    if fallback_failures >= 3:
        return True, f"CF bypass and proxy fallback failed - movie list not found on {fallback_failures} pages"
    
    # Check for proxy being marked as banned during fetch
    # Matches the log message: "Proxy '...' failed both Direct and CF modes. Marking BANNED and switching..."
    proxy_ban_markers = log_content.count('Marking BANNED and switching')
    if proxy_ban_markers > 0:
        return True, f"Proxy marked as banned during fetch ({proxy_ban_markers} times) - proxy IP may be blocked"
    
    # First check if we got any results at all
    total_entries_match = re.search(r'Total entries found: (\d+)', log_content)
    if total_entries_match:
        total_entries = int(total_entries_match.group(1))
        if total_entries > 0:
            # We successfully got some entries, so JavDB is accessible
            return False, None
    
    # Check if we successfully processed any pages
    if 'Successfully fetched URL:' in log_content:
        # We fetched at least some pages successfully
        return False, None
    
    # Check for movie list issues (phase structure problems)
    # Search case-insensitively to catch both "No movie list found" and "no movie list found"
    no_movie_list_count = len(re.findall(r'no movie list found', log_content, re.IGNORECASE))
    if no_movie_list_count >= 3:
        # Multiple pages had no movie list - likely a proxy/CF issue
        return True, f"Cannot retrieve movie list from JavDB - {no_movie_list_count} pages failed (check CF bypass service or proxy)"
    
    # Count consecutive fetch errors at the start of each phase
    phase1_errors = 0
    phase2_errors = 0
    current_phase = None
    
    lines = log_content.split('\n')
    for line in lines:
        # Detect phase changes
        if 'PHASE 1:' in line:
            current_phase = 1
        elif 'PHASE 2:' in line:
            current_phase = 2
        elif 'OVERALL SUMMARY' in line:
            break
        
        # Count errors (both 403 Forbidden and 500 Server Error)
        if 'Error fetching' in line and ('403 Client Error: Forbidden' in line or '500 Server Error' in line):
            if current_phase == 1:
                phase1_errors += 1
            elif current_phase == 2:
                phase2_errors += 1
        elif ('Successfully fetched URL' in line) or ('Found' in line and 'entries' in line):
            # Reset errors if we see successful page fetch or found entries
            if current_phase == 1:
                phase1_errors = 0
            elif current_phase == 2:
                phase2_errors = 0
    
    # If both phases have consistent errors at the start, main site is unreachable
    if phase1_errors >= 3 and phase2_errors >= 3:
        # Determine error type
        if '403 Client Error: Forbidden' in log_content:
            return True, "Cannot access JavDB - 403 Forbidden (proxy blocked or requires authentication)"
        else:
            return True, "Cannot access JavDB main site - all pages failed with 500 errors (check proxy configuration)"
    
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
            # Check if it's a widespread issue or just specific pages
            error_count = log_content.count(pattern)
            if error_count >= 3:
                return True, f"Critical network error: {message}"
    
    return False, None


def analyze_uploader_log(log_path):
    """
    Analyze uploader log to detect critical errors
    Returns: (is_critical_error, error_message)
    """
    if not os.path.exists(log_path):
        return True, "Uploader log file not found"
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    # Critical errors for qBittorrent uploader
    critical_patterns = [
        "Cannot connect to qBittorrent",
        "Failed to login to qBittorrent",
        "Connection refused",
        "Network is unreachable"
    ]
    
    for pattern in critical_patterns:
        if pattern in log_content:
            return True, f"Cannot access qBittorrent: {pattern}"
    
    # Check if we attempted to add torrents but all failed
    if 'Starting to add' in log_content and 'Failed to add:' in log_content:
        import re
        match = re.search(r'Successfully added: (\d+)', log_content)
        if match and int(match.group(1)) == 0:
            failed_match = re.search(r'Failed to add: (\d+)', log_content)
            if failed_match and int(failed_match.group(1)) > 0:
                return True, "All torrent additions failed"
    
    return False, None


def analyze_pikpak_log(log_path):
    """
    Analyze PikPak log to detect critical errors
    Returns: (is_critical_error, error_message)
    """
    if not os.path.exists(log_path):
        # PikPak is optional, so missing log is not critical
        return False, None
    
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
    
    # Critical errors for PikPak
    critical_patterns = [
        "qBittorrent login failed",
        "Failed to login qBittorrent",
        "Connection refused"
    ]
    
    for pattern in critical_patterns:
        if pattern in log_content:
            return True, f"Cannot access qBittorrent in PikPak bridge: {pattern}"
    
    # PikPak API errors are not critical (PikPak service issue, not our setup)
    return False, None


def extract_spider_statistics(log_path):
    """
    Extract key statistics from spider log for email report.
    Returns: dict with phase 1, phase 2, and overall statistics
    """
    stats = {
        'phase1': {'found': 0, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0},
        'phase2': {'found': 0, 'processed': 0, 'skipped_session': 0, 'skipped_history': 0},
        'overall': {'total_found': 0, 'successfully_processed': 0, 'skipped_session': 0, 'skipped_history': 0}
    }
    
    if not os.path.exists(log_path):
        return stats
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract phase 1 statistics
        phase1_found_pattern = r'\[Page \d+\] Found (\d+) entries for phase 1'
        phase1_matches = re.findall(phase1_found_pattern, content)
        stats['phase1']['found'] = sum(int(m) for m in phase1_matches)
        
        phase1_completed = re.search(r'Phase 1 completed: (\d+) entries processed', content)
        if phase1_completed:
            stats['phase1']['processed'] = int(phase1_completed.group(1))
        
        # Extract phase 2 statistics
        phase2_found_pattern = r'\[Page \d+\] Found (\d+) entries for phase 2'
        phase2_matches = re.findall(phase2_found_pattern, content)
        stats['phase2']['found'] = sum(int(m) for m in phase2_matches)
        
        phase2_completed = re.search(r'Phase 2 completed: (\d+) entries processed', content)
        if phase2_completed:
            stats['phase2']['processed'] = int(phase2_completed.group(1))
        
        # Extract overall statistics
        total_found = re.search(r'Total entries found: (\d+)', content)
        if total_found:
            stats['overall']['total_found'] = int(total_found.group(1))
        
        successfully_processed = re.search(r'Successfully processed: (\d+)', content)
        if successfully_processed:
            stats['overall']['successfully_processed'] = int(successfully_processed.group(1))
        
        skipped_session = re.search(r'Skipped already parsed in this session: (\d+)', content)
        if skipped_session:
            stats['overall']['skipped_session'] = int(skipped_session.group(1))
        
        skipped_history = re.search(r'Skipped already parsed in previous runs: (\d+)', content)
        if skipped_history:
            stats['overall']['skipped_history'] = int(skipped_history.group(1))
        
        # Distribute skipped counts between phases based on found entries
        total_skipped_session = stats['overall']['skipped_session']
        total_skipped_history = stats['overall']['skipped_history']
        total_found = stats['phase1']['found'] + stats['phase2']['found']
        
        if total_found > 0:
            # Distribute proportionally
            phase1_ratio = stats['phase1']['found'] / total_found
            stats['phase1']['skipped_session'] = int(total_skipped_session * phase1_ratio)
            stats['phase1']['skipped_history'] = int(total_skipped_history * phase1_ratio)
            
            stats['phase2']['skipped_session'] = total_skipped_session - stats['phase1']['skipped_session']
            stats['phase2']['skipped_history'] = total_skipped_history - stats['phase1']['skipped_history']
        
        return stats
        
    except Exception as e:
        logger.warning(f"Failed to extract spider statistics: {e}")
        return stats


def extract_uploader_statistics(log_path):
    """
    Extract key statistics from uploader log for email report.
    Returns: dict with upload statistics
    """
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
        
        # Extract statistics
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
    """
    Extract key statistics from PikPak log for email report.
    Returns: dict with PikPak statistics
    """
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
        
        # Extract threshold
        threshold = re.search(r'older than (\d+) days', content)
        if threshold:
            stats['threshold_days'] = int(threshold.group(1))
        
        # Extract statistics
        total = re.search(r'Found (\d+) torrents', content)
        if total:
            stats['total_torrents'] = int(total.group(1))
        
        filtered = re.search(r'Filtered (\d+) torrents older than', content)
        if filtered:
            stats['filtered_old'] = int(filtered.group(1))
        
        # Count successful additions to PikPak
        added_pattern = r'Successfully added to PikPak'
        stats['added_to_pikpak'] = len(re.findall(added_pattern, content))
        
        # Count removals from qBittorrent
        removed_pattern = r'Removed from qBittorrent'
        stats['removed_from_qb'] = len(re.findall(removed_pattern, content))
        
        # Count failures
        failed_pattern = r'Failed to (add|remove)'
        stats['failed'] = len(re.findall(failed_pattern, content, re.IGNORECASE))
        
        return stats
        
    except Exception as e:
        logger.warning(f"Failed to extract PikPak statistics: {e}")
        return stats


def format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary):
    """
    Format a mobile-friendly email report.
    Returns: formatted email body string
    """
    body = f"""
═══════════════════════════════
JavDB Pipeline Report
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
═══════════════════════════════

📊 SPIDER STATISTICS
───────────────────────────────

Phase 1 (Subtitle + Today/Yesterday)
  Found: {spider_stats['phase1']['found']}
  Processed: {spider_stats['phase1']['processed']}
  Skipped (Session): {spider_stats['phase1']['skipped_session']}
  Skipped (History): {spider_stats['phase1']['skipped_history']}

Phase 2 (Rate>4.0, Comments>85)
  Found: {spider_stats['phase2']['found']}
  Processed: {spider_stats['phase2']['processed']}
  Skipped (Session): {spider_stats['phase2']['skipped_session']}
  Skipped (History): {spider_stats['phase2']['skipped_history']}

Overall Summary
  Total Found: {spider_stats['overall']['total_found']}
  Processed: {spider_stats['overall']['successfully_processed']}
  Skipped (Session): {spider_stats['overall']['skipped_session']}
  Skipped (History): {spider_stats['overall']['skipped_history']}

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
  Regular (NoSub): {uploader_stats['no_subtitle']}

───────────────────────────────
🔄 PIKPAK BRIDGE
───────────────────────────────

Cleanup (>{pikpak_stats['threshold_days']} days)
  Scanned: {pikpak_stats['total_torrents']}
  Filtered: {pikpak_stats['filtered_old']}
  Added to PikPak: {pikpak_stats['added_to_pikpak']}
  Removed from QB: {pikpak_stats['removed_from_qb']}
  Failed: {pikpak_stats['failed']}

───────────────────────────────
🚦 PROXY STATUS
───────────────────────────────

{ban_summary}

═══════════════════════════════
End of Report
═══════════════════════════════
"""
    return body


def get_proxy_ban_summary():
    """Get proxy ban summary for email notification"""
    try:
        from utils.proxy_ban_manager import get_ban_manager
        ban_manager = get_ban_manager()
        # Include IP information in email
        return ban_manager.get_ban_summary(include_ip=True)
    except Exception as e:
        logger.warning(f"Failed to get proxy ban summary: {e}")
        return "Proxy ban information not available."


def send_email(subject, body, attachments=None):
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

    logger.info('Connecting to SMTP server...')
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    logger.info('Email sent successfully.')


def git_add_commit_push(step):
    """Commit and push Daily Report and logs files to GitHub"""
    try:
        safe_log_info(f"Step {step}: Committing and pushing files to GitHub...")

        # Get current branch name (use current branch instead of hardcoded GIT_BRANCH)
        try:
            result = subprocess.run(['git', 'branch', '--show-current'], capture_output=True, text=True, check=True)
            current_branch = result.stdout.strip()
            if not current_branch:
                # Fallback to GIT_BRANCH if we can't determine current branch
                current_branch = GIT_BRANCH
                safe_log_warning(f"Could not determine current branch, using default: {GIT_BRANCH}")
            else:
                safe_log_info(f"Current branch: {current_branch}")
        except subprocess.CalledProcessError:
            current_branch = GIT_BRANCH
            safe_log_warning(f"Could not determine current branch, using default: {GIT_BRANCH}")

        # Configure git with credentials
        subprocess.run(['git', 'config', 'user.name', GIT_USERNAME], check=True)
        subprocess.run(['git', 'config', 'user.email', f'{GIT_USERNAME}@users.noreply.github.com'], check=True)

        # Pull latest changes from remote to avoid push conflicts
        safe_log_info(f"Pulling latest changes from remote repository (branch: {current_branch})...")
        try:
            # Use git pull with credentials in URL to avoid authentication issues
            remote_url_with_auth = GIT_REPO_URL.replace('https://', f'https://{GIT_USERNAME}:{GIT_PASSWORD}@')
            subprocess.run(['git', 'pull', remote_url_with_auth, current_branch], check=True)
            safe_log_info("✓ Successfully pulled latest changes from remote")
        except subprocess.CalledProcessError as e:
            # Mask the command that contains sensitive information
            masked_cmd = mask_sensitive_info(str(e.cmd)) if hasattr(e, 'cmd') else str(e)
            safe_log_warning(f"Pull failed (this might be normal for new repos): Command {masked_cmd} returned non-zero exit status {e.returncode}")
            # Continue with commit/push even if pull fails (e.g., new repository)

        # Add all files in Daily Report and logs folders
        safe_log_info("Adding files to git...")
        subprocess.run(['git', 'add', DAILY_REPORT_DIR], check=True)
        subprocess.run(['git', 'add', AD_HOC_DIR], check=True)
        subprocess.run(['git', 'add', 'logs/'], check=True)

        # Check if there are any changes to commit
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            safe_log_info(f"No changes to commit - files are already up to date")
            return True

        # Commit with timestamp
        commit_message = f"Auto-commit: JavDB pipeline {step} results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        safe_log_info(f"Commit changes for {step}")
        subprocess.run(['git', 'add', 'logs/'], check=True)
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)

        # Push to remote repository
        remote_url_with_auth = GIT_REPO_URL.replace('https://', f'https://{GIT_USERNAME}:{GIT_PASSWORD}@')
        subprocess.run(['git', 'push', remote_url_with_auth, current_branch], check=True)

        return True

    except subprocess.CalledProcessError as e:
        # Mask the command that contains sensitive information
        masked_cmd = mask_sensitive_info(str(e.cmd)) if hasattr(e, 'cmd') else str(e)
        safe_log_error(f"Git operation failed: Command {masked_cmd} returned non-zero exit status {e.returncode}")
        
        # Mask output if it contains sensitive information
        if hasattr(e, 'output') and e.output:
            masked_output = mask_sensitive_info(e.output)
            safe_log_error(f"Command output: {masked_output}")
        else:
            safe_log_error("Command output: No output available")
        return False
    except Exception as e:
        safe_log_error(f"Unexpected error during git operations: {e}")
        return False


def parse_arguments():
    """Parse command line arguments for the pipeline"""
    parser = argparse.ArgumentParser(description='JavDB Pipeline - Run spider and uploader with optional arguments')
    # Spider arguments
    parser.add_argument('--url', type=str, help='Custom URL to scrape (add ?page=x for pages)')
    parser.add_argument('--start-page', type=int, help='Starting page number')
    parser.add_argument('--end-page', type=int, help='Ending page number')
    parser.add_argument('--all', action='store_true', help='Parse all pages until an empty page is found')
    parser.add_argument('--ignore-history', action='store_true', help='Ignore history file and scrape all pages')
    parser.add_argument('--phase', choices=['1', '2', 'all'], help='Which phase to run: 1 (subtitle+today), 2 (today only), all (default)')
    parser.add_argument('--output-file', type=str, help='Specify output CSV file name')
    parser.add_argument('--dry-run', action='store_true', help='Print items that would be written without changing CSV file')
    parser.add_argument('--ignore-release-date', action='store_true', help='Ignore today/yesterday tags and download all entries matching phase criteria (subtitle for phase1, quality for phase2)')
    parser.add_argument('--use-proxy', action='store_true', help='Enable proxy for all HTTP requests (proxy settings from config.py)')
    parser.add_argument('--use-cf-bypass', action='store_true', help='Use CloudFlare5sBypass service to bypass Cloudflare protection')
    # PikPak Bridge arguments
    parser.add_argument('--pikpak-individual', action='store_true', help='Use individual mode for PikPak Bridge instead of batch mode')
    return parser.parse_args()


def main():
    args = parse_arguments()
    is_adhoc_mode = args.url is not None
    # Determine CSV path based on mode
    if args.output_file:
        csv_filename = args.output_file
    else:
        if is_adhoc_mode:
            from scripts import spider
            csv_filename = spider.generate_output_csv_name(args.url)
        else:
            today_str = datetime.now().strftime('%Y%m%d')
            csv_filename = f'Javdb_TodayTitle_{today_str}.csv'

    if is_adhoc_mode:
        csv_path = os.path.join(AD_HOC_DIR, csv_filename)
        logger.info(f"Ad hoc mode detected. Expected CSV: {csv_path}")
    else:
        csv_path = os.path.join(DAILY_REPORT_DIR, csv_filename)
        logger.info(f"Daily mode. Expected CSV: {csv_path}")

    # Build arguments for spider
    spider_args = []
    if args.url:
        spider_args.extend(['--url', args.url])
    if args.start_page is not None:
        spider_args.extend(['--start-page', str(args.start_page)])
    if args.end_page is not None:
        spider_args.extend(['--end-page', str(args.end_page)])
    if args.all:
        spider_args.append('--all')
    if args.ignore_history:
        spider_args.append('--ignore-history')
    if args.phase:
        spider_args.extend(['--phase', args.phase])
    
    # Always pass output file explicitly to ensure consistency
    spider_args.extend(['--output-file', csv_filename])
        
    if args.dry_run:
        spider_args.append('--dry-run')
    if args.ignore_release_date:
        spider_args.append('--ignore-release-date')
    if args.use_proxy:
        spider_args.append('--use-proxy')
    if args.use_cf_bypass:
        spider_args.append('--use-cf-bypass')

    # Build arguments for uploader
    uploader_args = []
    if is_adhoc_mode:
        uploader_args.extend(['--mode', 'adhoc'])
    else:
        uploader_args.extend(['--mode', 'daily'])
    
    # Pass input file explicitly to ensure it matches Spider's output
    uploader_args.extend(['--input-file', csv_filename])
        
    if args.use_proxy:
        uploader_args.append('--use-proxy')

    pipeline_success = False
    pipeline_errors = []
    
    try:
        logger.info("=" * 60)
        logger.info("STARTING JAVDB PIPELINE")
        if is_adhoc_mode:
            logger.info("MODE: Ad Hoc")
            logger.info(f"Custom URL: {args.url}")
        else:
            logger.info("MODE: Daily")
        logger.info("=" * 60)

        # 1. Run Spider
        logger.info("Step 1: Running JavDB Spider...")
        run_script('scripts/spider.py', spider_args)
        logger.info("✓ JavDB Spider completed successfully")

        # 2. Run Uploader
        logger.info("Step 2: Running qBittorrent Uploader...")
        run_script('scripts/qb_uploader.py', uploader_args)
        logger.info("✓ qBittorrent Uploader completed successfully")

        # 3. Run PikPak Bridge to handle old torrents
        logger.info("Step 3: Running PikPak Bridge to clean up old torrents...")
        batch_mode = not args.pikpak_individual
        run_pikpak_bridge(days=3, dry_run=args.dry_run, batch_mode=batch_mode, use_proxy=args.use_proxy)
        logger.info("✓ PikPak Bridge completed successfully")

        pipeline_success = True
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED")
        logger.info("=" * 60)

    except Exception as e:
        logger.error("=" * 60)
        logger.error("PIPELINE EXECUTION ERROR")
        logger.error("=" * 60)
        logger.error(f'Error: {e}')
        pipeline_success = False
        pipeline_errors.append(f"Pipeline execution error: {e}")

    # Analyze logs for critical errors even if pipeline "succeeded"
    logger.info("Analyzing logs for critical errors...")
    
    spider_critical, spider_error = analyze_spider_log(SPIDER_LOG_FILE)
    if spider_critical:
        logger.error(f"CRITICAL ERROR in Spider: {spider_error}")
        pipeline_errors.append(f"Spider: {spider_error}")
    
    uploader_critical, uploader_error = analyze_uploader_log(UPLOADER_LOG_FILE)
    if uploader_critical:
        logger.error(f"CRITICAL ERROR in Uploader: {uploader_error}")
        pipeline_errors.append(f"Uploader: {uploader_error}")
    
    pikpak_critical, pikpak_error = analyze_pikpak_log(PIKPAK_LOG_FILE)
    if pikpak_critical:
        logger.error(f"CRITICAL ERROR in PikPak: {pikpak_error}")
        pipeline_errors.append(f"PikPak: {pikpak_error}")
    
    # Determine final status
    has_critical_errors = len(pipeline_errors) > 0
    
    if has_critical_errors:
        logger.error("=" * 60)
        logger.error("PIPELINE FAILED - CRITICAL ERRORS DETECTED")
        logger.error("=" * 60)
        for error in pipeline_errors:
            logger.error(f"  - {error}")
    else:
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED SUCCESSFULLY - NO CRITICAL ERRORS")
        logger.info("=" * 60)

    # Send email based on actual pipeline status
    today_str = datetime.now().strftime('%Y%m%d')
    if not has_critical_errors:
        # Pipeline succeeded - send detailed report with attachments
        # Extract structured statistics from logs
        spider_stats = extract_spider_statistics(SPIDER_LOG_FILE)
        uploader_stats = extract_uploader_statistics(UPLOADER_LOG_FILE)
        pikpak_stats = extract_pikpak_statistics(PIKPAK_LOG_FILE)
        ban_summary = get_proxy_ban_summary()
        
        # Format the email body with structured tables
        body = format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary)
        
        attachments = [csv_path, SPIDER_LOG_FILE, UPLOADER_LOG_FILE, PIKPAK_LOG_FILE, PIPELINE_LOG_FILE]
        try:
            send_email(
                subject=f'✓ SUCCESS - JavDB Pipeline Report {today_str}',
                body=body,
                attachments=attachments
            )
        except Exception as e:
            logger.error(f'Failed to send success email: {e}')
    else:
        # Pipeline failed - send detailed failure notification
        error_details = "\n".join([f"  • {error}" for error in pipeline_errors])
        
        # Extract structured statistics from logs
        spider_stats = extract_spider_statistics(SPIDER_LOG_FILE)
        uploader_stats = extract_uploader_statistics(UPLOADER_LOG_FILE)
        pikpak_stats = extract_pikpak_statistics(PIKPAK_LOG_FILE)
        ban_summary = get_proxy_ban_summary()
        
        # Format structured report
        stats_report = format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary)
        
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
        try:
            send_email(
                subject=f'✗ FAILED - JavDB Pipeline Report {today_str}',
                body=body,
                attachments=[SPIDER_LOG_FILE, UPLOADER_LOG_FILE, PIKPAK_LOG_FILE, PIPELINE_LOG_FILE]
            )
        except Exception as e:
            logger.error(f'Failed to send failure email: {e}')

    # Final commit for pipeline log
    logger.info("Final commit for pipeline log...")
    git_add_commit_push("pipeline_log")


if __name__ == '__main__':
    main()

