"""Log parsing, statistics extraction, and pending-mode / drift input analysis.

This module owns every read-side analysis function the email pipeline runs:
critical-error detection across spider / uploader / pikpak / pipeline logs,
statistics extraction, proxy-ban summaries, dedup statistics, workflow job
status checks, pending-mode verification record loading / evaluation, and the
dual-mode D1 drift advisory.

Extracted verbatim from the pre-split ``email.py`` during ADR-015 Phase 6.
"""

import os
import re
import json
from datetime import datetime, timezone

from javdb.infra.logging import get_logger

from javdb.integrations.notify.email._config import (
    DEDUP_LOG_FILE,
    _EMAIL_REPORTS_DIR,
    _MAX_READ_BYTES,
)

logger = get_logger(__name__)


def _read_capped(path, max_bytes=_MAX_READ_BYTES, encoding="utf-8"):
    """Read a text file, truncating to *max_bytes* to avoid OOM on huge logs."""
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding=encoding, errors="replace") as f:
            content = f.read(max_bytes)
        if size > max_bytes:
            logger.warning("Truncated %s from %d to %d bytes", path, size, max_bytes)
            content += f"\n\n... [truncated — {size - max_bytes} bytes omitted] ..."
        return content
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return ""


def analyze_spider_log(log_path):
    """
    Analyze spider log to detect critical errors
    Returns: (is_critical_error, error_message, log_exists)
    """
    if not os.path.exists(log_path):
        return False, "Spider log file not found (script may not have run)", False

    log_content = _read_capped(log_path)

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

    log_content = _read_capped(log_path)

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

    log_content = _read_capped(log_path)

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

    log_content = _read_capped(log_path)

    # Check for script execution failures
    script_failures = []

    failure_pattern = r'Script (scripts/[\w./]+?) failed with return code (\d+)'
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
    - "skipped" = movies skipped by history rules

    Note: Each movie can have multiple torrent links (subtitle, no_subtitle, etc.)
    """
    stats = {
        'phase1': {'discovered': None, 'processed': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'phase2': {'discovered': None, 'processed': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'overall': {'total_discovered': None, 'successfully_processed': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'failed_movies': [],
    }

    if not os.path.exists(log_path):
        return stats

    try:
        content = _read_capped(log_path)

        # Extract phase 1 statistics from current format (without skipped session):
        # "Phase 1 completed: X movies discovered, Y processed, Z skipped (history), N no new torrents, F failed"
        phase1_with_no_new = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(history\), (\d+) no new torrents, (\d+) failed',
            content
        )
        # Backward-compatible format:
        # "Phase 1 completed: X movies discovered, Y processed, S skipped (session), Z skipped (history), N no new torrents, F failed"
        phase1_with_no_new_legacy = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) no new torrents, (\d+) failed',
            content
        )
        # Fallback to format with failed but no no_new_torrents:
        # "Phase 1 completed: X movies discovered, Y processed, Z skipped (history), F failed"
        phase1_with_failed = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(history\), (\d+) failed',
            content
        )
        # Backward-compatible format with skipped session:
        phase1_with_failed_legacy = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) failed',
            content
        )
        # Fallback to intermediate format (without failed):
        # "Phase 1 completed: X movies discovered, Y processed, Z skipped (history)"
        phase1_new = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(history\)',
            content
        )
        # Backward-compatible intermediate format:
        phase1_new_legacy = re.search(
            r'Phase 1 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\)',
            content
        )
        # Fallback to old format: "Phase 1 completed: X found, Y skipped (history), Z written to CSV"
        phase1_old = re.search(r'Phase 1 completed: (\d+) found, (\d+) skipped.*?, (\d+) written to CSV', content)

        if phase1_with_no_new:
            stats['phase1']['discovered'] = int(phase1_with_no_new.group(1))
            stats['phase1']['processed'] = int(phase1_with_no_new.group(2))
            stats['phase1']['skipped_history'] = int(phase1_with_no_new.group(3))
            stats['phase1']['no_new_torrents'] = int(phase1_with_no_new.group(4))
            stats['phase1']['failed'] = int(phase1_with_no_new.group(5))
        elif phase1_with_no_new_legacy:
            stats['phase1']['discovered'] = int(phase1_with_no_new_legacy.group(1))
            stats['phase1']['processed'] = int(phase1_with_no_new_legacy.group(2))
            stats['phase1']['skipped_history'] = int(phase1_with_no_new_legacy.group(4))
            stats['phase1']['no_new_torrents'] = int(phase1_with_no_new_legacy.group(5))
            stats['phase1']['failed'] = int(phase1_with_no_new_legacy.group(6))
        elif phase1_with_failed:
            stats['phase1']['discovered'] = int(phase1_with_failed.group(1))
            stats['phase1']['processed'] = int(phase1_with_failed.group(2))
            stats['phase1']['skipped_history'] = int(phase1_with_failed.group(3))
            stats['phase1']['failed'] = int(phase1_with_failed.group(4))
        elif phase1_with_failed_legacy:
            stats['phase1']['discovered'] = int(phase1_with_failed_legacy.group(1))
            stats['phase1']['processed'] = int(phase1_with_failed_legacy.group(2))
            stats['phase1']['skipped_history'] = int(phase1_with_failed_legacy.group(4))
            stats['phase1']['failed'] = int(phase1_with_failed_legacy.group(5))
        elif phase1_new:
            stats['phase1']['discovered'] = int(phase1_new.group(1))
            stats['phase1']['processed'] = int(phase1_new.group(2))
            stats['phase1']['skipped_history'] = int(phase1_new.group(3))
        elif phase1_new_legacy:
            stats['phase1']['discovered'] = int(phase1_new_legacy.group(1))
            stats['phase1']['processed'] = int(phase1_new_legacy.group(2))
            stats['phase1']['skipped_history'] = int(phase1_new_legacy.group(4))
        elif phase1_old:
            stats['phase1']['discovered'] = int(phase1_old.group(1))
            stats['phase1']['processed'] = int(phase1_old.group(3))
            stats['phase1']['skipped_history'] = int(phase1_old.group(2))

        # Extract phase 2 statistics from current format (without skipped session)
        phase2_with_no_new = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(history\), (\d+) no new torrents, (\d+) failed',
            content
        )
        # Backward-compatible format with skipped session
        phase2_with_no_new_legacy = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) no new torrents, (\d+) failed',
            content
        )
        # Fallback to format with failed but no no_new_torrents
        phase2_with_failed = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(history\), (\d+) failed',
            content
        )
        # Backward-compatible format with skipped session
        phase2_with_failed_legacy = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\), (\d+) failed',
            content
        )
        phase2_new = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(history\)',
            content
        )
        # Backward-compatible intermediate format
        phase2_new_legacy = re.search(
            r'Phase 2 completed: (\d+) movies discovered, (\d+) processed, (\d+) skipped \(session\), (\d+) skipped \(history\)',
            content
        )
        phase2_old = re.search(r'Phase 2 completed: (\d+) found, (\d+) skipped.*?, (\d+) written to CSV', content)

        if phase2_with_no_new:
            stats['phase2']['discovered'] = int(phase2_with_no_new.group(1))
            stats['phase2']['processed'] = int(phase2_with_no_new.group(2))
            stats['phase2']['skipped_history'] = int(phase2_with_no_new.group(3))
            stats['phase2']['no_new_torrents'] = int(phase2_with_no_new.group(4))
            stats['phase2']['failed'] = int(phase2_with_no_new.group(5))
        elif phase2_with_no_new_legacy:
            stats['phase2']['discovered'] = int(phase2_with_no_new_legacy.group(1))
            stats['phase2']['processed'] = int(phase2_with_no_new_legacy.group(2))
            stats['phase2']['skipped_history'] = int(phase2_with_no_new_legacy.group(4))
            stats['phase2']['no_new_torrents'] = int(phase2_with_no_new_legacy.group(5))
            stats['phase2']['failed'] = int(phase2_with_no_new_legacy.group(6))
        elif phase2_with_failed:
            stats['phase2']['discovered'] = int(phase2_with_failed.group(1))
            stats['phase2']['processed'] = int(phase2_with_failed.group(2))
            stats['phase2']['skipped_history'] = int(phase2_with_failed.group(3))
            stats['phase2']['failed'] = int(phase2_with_failed.group(4))
        elif phase2_with_failed_legacy:
            stats['phase2']['discovered'] = int(phase2_with_failed_legacy.group(1))
            stats['phase2']['processed'] = int(phase2_with_failed_legacy.group(2))
            stats['phase2']['skipped_history'] = int(phase2_with_failed_legacy.group(4))
            stats['phase2']['failed'] = int(phase2_with_failed_legacy.group(5))
        elif phase2_new:
            stats['phase2']['discovered'] = int(phase2_new.group(1))
            stats['phase2']['processed'] = int(phase2_new.group(2))
            stats['phase2']['skipped_history'] = int(phase2_new.group(3))
        elif phase2_new_legacy:
            stats['phase2']['discovered'] = int(phase2_new_legacy.group(1))
            stats['phase2']['processed'] = int(phase2_new_legacy.group(2))
            stats['phase2']['skipped_history'] = int(phase2_new_legacy.group(4))
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

        skipped_history = re.search(r'Skipped already parsed in previous runs: (\d+)', content)
        if skipped_history:
            stats['overall']['skipped_history'] = int(skipped_history.group(1))

        no_new_torrents = re.search(r'No new torrents to download: (\d+)', content)
        if no_new_torrents:
            stats['overall']['no_new_torrents'] = int(no_new_torrents.group(1))

        failed = re.search(r'Failed to fetch/parse: (\d+)', content)
        if failed:
            stats['overall']['failed'] = int(failed.group(1))

        # Newer spider logs keep phase completion lines but no longer emit the
        # legacy overall text lines parsed above. Derive the missing overall
        # fields from phase stats before falling back to the all-zero defaults.
        has_phase_stats = any(
            stats[phase]['discovered'] is not None
            for phase in ('phase1', 'phase2')
        )
        if has_phase_stats:
            phase_total_discovered = sum(
                stats[phase]['discovered'] or 0
                for phase in ('phase1', 'phase2')
            )
            if stats['overall']['total_discovered'] is None:
                stats['overall']['total_discovered'] = phase_total_discovered
            if not successfully_processed:
                stats['overall']['successfully_processed'] = sum(
                    stats[phase]['processed']
                    for phase in ('phase1', 'phase2')
                )
            if not skipped_history:
                stats['overall']['skipped_history'] = sum(
                    stats[phase]['skipped_history']
                    for phase in ('phase1', 'phase2')
                )
            if not no_new_torrents:
                stats['overall']['no_new_torrents'] = sum(
                    stats[phase].get('no_new_torrents', 0)
                    for phase in ('phase1', 'phase2')
                )
            if not failed:
                stats['overall']['failed'] = sum(
                    stats[phase]['failed']
                    for phase in ('phase1', 'phase2')
                )

        # If overall total_discovered still wasn't found, calculate from
        # whatever overall components were available.
        if stats['overall']['total_discovered'] is None:
            stats['overall']['total_discovered'] = (
                stats['overall']['successfully_processed'] +
                stats['overall']['skipped_history'] +
                stats['overall']['no_new_torrents'] +
                stats['overall']['failed']
            )

        # Parse failed movie details from log lines like:
        #   [1/50] [Page 1] Failed: ABC-123 (https://javdb.com/v/xxx)
        for m in re.finditer(r'\[Page (\d+)\] Failed: (\S+) \((https?://\S+)\)', content):
            stats['failed_movies'].append({
                'video_code': m.group(2),
                'url': m.group(3),
            })

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
        content = _read_capped(log_path)

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
        content = _read_capped(log_path)

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
        from javdb.proxy.ban_manager import get_ban_manager
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


def _is_dedup_enabled(log_path):
    """Check spider log for the last 'DEDUP MODE' marker to determine if dedup ran."""
    if not os.path.exists(log_path):
        return False
    try:
        last_enabled = None
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if 'DEDUP MODE: Enabled' in line:
                    last_enabled = True
                elif 'DEDUP MODE: Disabled' in line:
                    last_enabled = False
        return last_enabled if last_enabled is not None else False
    except Exception as e:
        logger.debug(f"Could not read spider log for dedup status: {e}")
    return False


def _is_redownload_dedup_reason(reason):
    """Return True when a dedup reason represents a redownload upgrade."""
    normalized = (reason or '').strip().lower()
    return normalized.startswith('re-download upgrade') or normalized.startswith('redownload upgrade')


def _extract_last_dedup_executor_run(log_path=None):
    """Parse the dedup executor log and return stats for the most recent run.

    The executor (``rclone_manager.py --execute``) emits a deterministic
    block per run::

        2026-04-26 00:59:08,094 - Rclone - INFO - ====...====
        2026-04-26 00:59:08,094 - Rclone - INFO - RCLONE DEDUP EXECUTOR
        ...
        2026-04-26 01:00:26,894 - Rclone - INFO - DEDUP EXECUTOR COMPLETE
        2026-04-26 01:00:26,894 - Rclone - INFO - Pending rows: 15, unique paths: 15
        2026-04-26 01:00:26,894 - Rclone - INFO - Purged: 10, failed: 5, skipped (empty path): 0

    Returns a dict with keys ``start_time``, ``end_time``, ``pending``,
    ``purged``, ``failed``, ``skipped`` for the LAST such block, or
    ``None`` when the log is missing / no completed run found.
    """
    path = log_path or DEDUP_LOG_FILE
    if not path or not os.path.exists(path):
        return None

    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        logger.debug(f"Could not read dedup executor log {path}: {e}")
        return None

    # Anchor on the LAST 'DEDUP EXECUTOR COMPLETE' line first so we never
    # surface stats from an aborted (start-only) run that left the COMPLETE
    # footer unwritten. From there we walk backwards to the matching
    # 'RCLONE DEDUP EXECUTOR' header.
    complete_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if 'DEDUP EXECUTOR COMPLETE' in lines[i]:
            complete_idx = i
            break
    if complete_idx is None:
        return None

    start_idx = None
    for i in range(complete_idx, -1, -1):
        if 'RCLONE DEDUP EXECUTOR' in lines[i]:
            start_idx = i
            break
    if start_idx is None:
        return None

    # Timestamp prefix: '2026-04-26 00:59:08,094 - ...'
    ts_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
    start_match = ts_re.match(lines[start_idx])
    if not start_match:
        return None
    start_time = start_match.group(1)

    end_match = ts_re.match(lines[complete_idx])
    end_time = end_match.group(1) if end_match else None

    block_end_idx = complete_idx + 1
    for i in range(complete_idx + 1, len(lines)):
        if 'RCLONE DEDUP EXECUTOR' in lines[i]:
            break
        block_end_idx = i + 1
        if re.search(r'Purged:\s*\d+,\s*failed:\s*\d+', lines[i]):
            break
    block = lines[start_idx:block_end_idx]
    pending = purged = failed = skipped = None
    for ln in block:
        m = re.search(r'Pending rows:\s*(\d+)', ln)
        if m:
            pending = int(m.group(1))
        m = re.search(r'Purged:\s*(\d+),\s*failed:\s*(\d+)(?:,\s*skipped\s*\(empty path\):\s*(\d+))?', ln)
        if m:
            purged = int(m.group(1))
            failed = int(m.group(2))
            skipped = int(m.group(3)) if m.group(3) is not None else 0

    if purged is None or failed is None:
        return None

    return {
        'start_time': start_time,
        'end_time': end_time or start_time,
        'pending': pending if pending is not None else (purged + failed),
        'purged': purged,
        'failed': failed,
        'skipped': skipped or 0,
    }


def extract_dedup_statistics(dedup_csv_path, session_start_time=None):
    """Extract dedup statistics for the email report.

    Reads from the SQLite DB first (authoritative source).  Falls back to
    the CSV file at *dedup_csv_path* when the DB is unavailable or empty.

    Args:
        dedup_csv_path: Path to the dedup CSV file (fallback source).
        session_start_time: ISO datetime string (e.g. '2026-03-15 00:05:00').
            When provided, only records with detect_datetime >= this value
            are included (current-session scope).  When absent, all rows are
            treated as in-session (no date filtering).

    Returns a dict with keys:
        detected, deleted, failed, redownload_detected, redownload_deleted,
        deleted_items (list of summary strings)
    Returns None when no data exists.
    """
    rows = None

    # Try DB first
    try:
        from javdb.infra.config import use_sqlite
        if use_sqlite():
            from javdb.storage.db import init_db, db_load_dedup_records
            init_db()
            db_rows = db_load_dedup_records()
            if db_rows:
                rows = []
                for r in db_rows:
                    rows.append({
                        'video_code': r.get('VideoCode', r.get('video_code', '')),
                        'existing_sensor': r.get('ExistingSensor', r.get('existing_sensor', '')),
                        'existing_subtitle': r.get('ExistingSubtitle', r.get('existing_subtitle', '')),
                        'detect_datetime': r.get('DateTimeDetected') or r.get('detect_datetime') or '',
                        'is_deleted': 'True' if r.get('IsDeleted', r.get('is_deleted')) in (1, True, 'True', '1') else 'False',
                        'delete_datetime': r.get('DateTimeDeleted') or r.get('delete_datetime') or '',
                        'deletion_reason': r.get('DeletionReason', r.get('deletion_reason', '')),
                    })
    except Exception as e:
        logger.debug(f"Could not load dedup records from DB: {e}")

    # CSV fallback
    if rows is None:
        import csv as _csv
        if not os.path.exists(dedup_csv_path):
            return None
        try:
            with open(dedup_csv_path, 'r', encoding='utf-8') as f:
                rows = list(_csv.DictReader(f))
        except Exception as e:
            logger.warning(f"Failed to read dedup CSV: {e}")
            return None

    if not rows:
        return None

    # Prefer the dedup executor's own log as the authoritative window:
    # it contains the exact pending/purged/failed counts for the LAST run
    # and a precise start timestamp.  Falling back on session_start_time
    # is unreliable because the DB retains up to 30 days of dedup history,
    # so any miss in the cutoff would inflate the counts to the entire
    # retained set (e.g. 682 detected / 677 deleted instead of 15 / 10).
    executor_run = _extract_last_dedup_executor_run()
    window_start = None
    window_end = None
    if executor_run is not None:
        window_start = executor_run['start_time']
        window_end = executor_run.get('end_time') or None
        logger.debug(
            f"Dedup executor window: start={window_start} end={window_end} "
            f"pending={executor_run['pending']} purged={executor_run['purged']} "
            f"failed={executor_run['failed']}"
        )
    else:
        # Fall back to ReportSessions cutoff, or no filter as a last resort.
        window_start = session_start_time or None
        if window_start is None:
            logger.warning(
                "Dedup executor log not found and no session_start_time provided — "
                "dedup counts will reflect ALL retained DB records, not just this run."
            )

    def _in_dedup_window(timestamp: str) -> bool:
        if window_start is None:
            return True
        if not timestamp or timestamp < window_start:
            return False
        if window_end is not None and timestamp > window_end:
            return False
        return True

    detected_session = sum(
        1 for r in rows
        if _in_dedup_window(r.get('detect_datetime', ''))
    )
    redownload_detected_session = sum(
        1 for r in rows
        if _in_dedup_window(r.get('detect_datetime', ''))
        and _is_redownload_dedup_reason(r.get('deletion_reason', ''))
    )
    deleted_session_items = []
    redownload_deleted_session = 0
    for r in rows:
        in_session = _in_dedup_window(r.get('detect_datetime', ''))
        is_redownload = _is_redownload_dedup_reason(r.get('deletion_reason', ''))
        if (in_session
                and r.get('is_deleted', 'False') == 'True'
                and _in_dedup_window(r.get('delete_datetime', ''))):
            if is_redownload:
                redownload_deleted_session += 1
            label = '[Redownload upgrade] ' if is_redownload else ''
            deleted_session_items.append(
                f"  • {label}{r.get('video_code', '?')} [{r.get('existing_sensor', '?')}-{r.get('existing_subtitle', '?')}] "
                f"-> {r.get('deletion_reason', '?')}"
            )

    # When we have authoritative executor counts, override the totals so the
    # email matches the executor's behaviour exactly.  The deleted_items list
    # has already been narrowed to the executor window above.
    if executor_run is not None:
        detected_total = executor_run['pending']
        deleted_total = executor_run['purged']
        failed_total = executor_run['failed']
    else:
        detected_total = detected_session
        deleted_total = len(deleted_session_items)
        failed_total = detected_total - deleted_total if detected_total > deleted_total else 0

    return {
        'detected': detected_total,
        'deleted': deleted_total,
        'failed': failed_total,
        'redownload_detected': redownload_detected_session,
        'redownload_deleted': redownload_deleted_session,
        'deleted_items': deleted_session_items,
    }


# =============================================================================
# Phase 2 / Phase 3 — Pending Mode Verification + Health Snapshot
# =============================================================================
#
# `pending_session_verify` records are emitted by both
# `apps.cli.db.commit_session` and `apps.cli.db.rollback` whenever a pending-
# mode session ends.  They live in `reports/D1/d1_drift.jsonl` (one
# JSON object per line, mixed with other `kind` records).  The email
# pipeline reads the file at the end of the run, isolates the verify
# records belonging to this run, and renders a "Pending Mode
# Verification" table.  Any record above the alert threshold makes the
# email subject line gain a `[PENDING-ALERT]` (soft) or
# `[PENDING-PAUSE]` (critical, ADR-006 PR-D — was `[PENDING-ROLLBACK-AUTO]`
# pre-ADR-006 when critical alerts auto-fell-back to audit mode) prefix.

# Phase 2 alert thresholds — referenced by Phase 3 with tighter cuts.
_PHASE2_PENDING_ALERT_THRESHOLDS = {
    'pending_residual_count_max': 0,
    'commit_attempts_max': 2,
    'derived_recompute_drift_max': 0,
    'd1_request_count_audit_baseline_ratio_max': 2.0,
    'worker_stage_rollback_failed_max': 0,
}

# Phase 3 thresholds — production SLO.  See plan §Phase 3.A.
_PHASE3_PENDING_ALERT_THRESHOLDS = {
    'pending_residual_count_max': 0,
    'commit_attempts_max': 1,
    'derived_recompute_drift_max': 0,
    'd1_request_count_audit_baseline_ratio_max': 1.8,
    'worker_stage_rollback_failed_max': 0,
    'cleanup_path_mismatch_count_max': 0,
    'staged_claim_orphan_count_max': 0,
}


def _resolve_pending_alert_thresholds():
    """Return the active threshold dict.

    Phase 3 default; flip to Phase 2 by setting JAVDB_PENDING_ALERT_PHASE=2
    (used by TestIngestion canary while it warms up).
    """
    raw = os.environ.get('JAVDB_PENDING_ALERT_PHASE', '').strip()
    if raw == '2':
        merged = dict(_PHASE2_PENDING_ALERT_THRESHOLDS)
        merged.update({
            # Phase 2 baseline didn't have these keys; treat as soft +inf
            # so the metric never triggers an alert before Phase 3 cuts in.
            'cleanup_path_mismatch_count_max': float('inf'),
            'staged_claim_orphan_count_max': float('inf'),
        })
        return merged
    return _PHASE3_PENDING_ALERT_THRESHOLDS


# Critical alerts trigger the auto-fallback path; soft alerts only
# annotate the subject line.  Names match the JSON field names of
# `pending_session_verify` records.
_CRITICAL_ALERT_FIELDS = (
    'pending_residual_count',
    'derived_recompute_drift',
    'cleanup_path_mismatch_count',
)


def _load_pending_verify_records(jsonl_path, run_id=None, run_attempt=None):
    """Read every ``pending_session_verify`` record from *jsonl_path*.

    When *run_id* / *run_attempt* are provided, restricts to records
    whose ``run_id`` / ``run_attempt`` match.  Returns ``[]`` on
    missing file or read error so the email pipeline never raises.
    """
    if not jsonl_path:
        return []
    if not os.path.exists(jsonl_path):
        return []
    records = []
    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = __import__('json').loads(line)
                except Exception:
                    continue
                if rec.get('kind') != 'pending_session_verify':
                    continue
                if run_id is not None:
                    if str(rec.get('run_id') or '') != str(run_id):
                        continue
                if run_attempt is not None:
                    try:
                        if int(rec.get('run_attempt') or -1) != int(run_attempt):
                            continue
                    except (TypeError, ValueError):
                        continue
                records.append(rec)
    except Exception as e:
        logger.warning('Failed to read pending_session_verify records: %s', e)
        return []
    return records


def _evaluate_pending_alerts(records, thresholds=None):
    """Return ``(alerts, has_critical)`` for *records*.

    *alerts* is a list of ``(field, value, threshold, severity)`` tuples
    where severity is ``'critical'`` or ``'soft'``.  *has_critical* is
    True iff at least one critical alert fired (drives the auto-fallback
    decision in the calling workflow).
    """
    th = thresholds or _resolve_pending_alert_thresholds()
    alerts = []
    has_critical = False
    for rec in records:
        for key, max_key in (
            ('pending_residual_count', 'pending_residual_count_max'),
            ('commit_attempts', 'commit_attempts_max'),
            ('derived_recompute_drift', 'derived_recompute_drift_max'),
            ('worker_stage_rollback_failed',
             'worker_stage_rollback_failed_max'),
            ('cleanup_path_mismatch_count',
             'cleanup_path_mismatch_count_max'),
            ('staged_claim_orphan_count', 'staged_claim_orphan_count_max'),
            ('d1_request_count_audit_baseline_ratio',
             'd1_request_count_audit_baseline_ratio_max'),
        ):
            limit = th.get(max_key)
            if limit is None:
                continue
            try:
                value = float(rec.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > limit:
                severity = (
                    'critical' if key in _CRITICAL_ALERT_FIELDS else 'soft'
                )
                alerts.append((key, value, limit, severity, rec))
                if severity == 'critical':
                    has_critical = True
        # final_status='finalizing' = commit stuck
        if rec.get('final_status') == 'finalizing':
            alerts.append((
                'final_status_finalizing', 1, 0, 'soft', rec,
            ))
    return alerts, has_critical


def _build_dual_drift_advisory(reports_dir: str) -> str:
    """Return a banner string for the email body when D1 drift was logged today.

    P0-6: in ``STORAGE_BACKEND=dual`` the application's read path goes
    to D1 first, but the email's stats are explicitly pulled from
    SQLite-local (see the ``db_get_*_local`` calls in the orchestration). A
    discrepancy between those two sources is exactly the symptom of the
    asymmetric-write drift that prompted this hardening, so when
    ``reports/D1/d1_drift.jsonl`` carries entries from today we surface
    a top-of-email banner pointing the operator at the drift log.

    Returns an empty string when the drift file is missing, empty, or
    has no records from today (UTC).
    """
    jsonl_path = os.path.join(reports_dir, 'D1', 'd1_drift.jsonl')
    if not os.path.exists(jsonl_path):
        return ''
    today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    todays_records = 0
    sample_first_sql = None
    sample_db = None
    rollback_drift_rows = 0
    failure_count_total = 0
    pending_residual_total = 0
    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    # A non-object JSON line (list/str/number) has no .get();
                    # skip it instead of raising AttributeError.
                    continue
                ts = str(rec.get('ts') or '')
                if not ts.startswith(today_utc):
                    continue
                try:
                    failure_count = int(rec.get('failure_count') or 0)
                    uncommitted_d1_writes = int(rec.get('uncommitted_d1_writes') or 0)
                    pending_residual_count = int(rec.get('pending_residual_count') or 0)
                except (TypeError, ValueError):
                    continue
                todays_records += 1
                failure_count_total += failure_count
                rollback_drift_rows += uncommitted_d1_writes
                pending_residual_total += pending_residual_count
                if sample_first_sql is None and rec.get('first_failed_sql'):
                    sample_first_sql = rec.get('first_failed_sql')
                    sample_db = rec.get('db')
    except OSError:
        return ''

    has_drift = failure_count_total > 0 or rollback_drift_rows > 0 or pending_residual_total > 0
    if todays_records == 0 or not has_drift:
        return ''

    lines = [
        '⚠️  D1 DRIFT ADVISORY  ⚠️',
        f'  - {todays_records} drift record(s) appended to d1_drift.jsonl since 00:00 UTC',
        f'  - cumulative D1 write failures today: {failure_count_total}',
        f'  - rows D1 kept after SQLite rollback today: {rollback_drift_rows}',
    ]
    if sample_first_sql:
        lines.append(
            f'  - first failed SQL (db={sample_db}): {sample_first_sql}'
        )
    lines.append(
        '  - source: stats below are read from SQLite-local (canonical); '
        'D1 may be behind. Reconcile via scripts/sync_d1_to_sqlite.py.'
    )
    return '\n'.join(lines) + '\n\n'


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
