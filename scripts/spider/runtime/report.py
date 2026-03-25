"""Summary reporting for spider results."""

import os
import sys
import logging

from utils.infra.logging_config import get_logger

import scripts.spider.runtime.state as state
from scripts.spider.runtime.config import (
    PROXY_MODE, REPORTS_DIR, PARSED_MOVIES_CSV,
    INCLUDE_DOWNLOADED_IN_REPORT,
)

logger = get_logger(__name__)


def log_phase_summary(phase_name: str, phase_rows: list) -> None:
    """Log torrent type statistics for a single processing phase."""
    logger.info("=" * 30)
    logger.info(f"{phase_name} SUMMARY")
    logger.info("=" * 30)
    logger.info(f"{phase_name} entries found: {len(phase_rows)}")
    if phase_rows:
        n = len(phase_rows)
        sub = sum(1 for r in phase_rows if r['subtitle'])
        hsub = sum(1 for r in phase_rows if r['hacked_subtitle'])
        hnosub = sum(1 for r in phase_rows if r['hacked_no_subtitle'])
        nosub = sum(1 for r in phase_rows if r['no_subtitle'])
        logger.info(f"  - Subtitle torrents: {sub} ({sub / n * 100:.1f}%)")
        logger.info(f"  - Hacked subtitle torrents: {hsub} ({hsub / n * 100:.1f}%)")
        logger.info(f"  - Hacked no-subtitle torrents: {hnosub} ({hnosub / n * 100:.1f}%)")
        logger.info(f"  - No-subtitle torrents: {nosub} ({nosub / n * 100:.1f}%)")
    else:
        logger.info(f"  - No entries found in {phase_name}")


def generate_summary_report(
    *, phase_mode, parse_all, start_page, end_page, max_consecutive_empty,
    phase1_rows, phase2_rows, rows,
    use_history_for_loading, ignore_history,
    skipped_history_count, failed_count, no_new_torrents_count,
    csv_path, dry_run, use_history_for_saving,
    use_proxy, any_proxy_banned, any_proxy_banned_phase2,
    dedup_csv_path=None,
) -> None:
    """Log the final summary report, proxy stats, and check exit conditions."""
    logger.info("=" * 75)
    logger.info("SUMMARY REPORT")
    logger.info("=" * 75)
    if parse_all:
        logger.info(f"Pages processed: {start_page} to last page with results")
    else:
        logger.info(f"Pages processed: {start_page} to {end_page}")

    logger.info(f"Tolerance mechanism: Stops after {max_consecutive_empty} consecutive pages with no HTML content")

    if phase_mode in ['1', 'all']:
        log_phase_summary("PHASE 1", phase1_rows)
    if phase_mode in ['2', 'all']:
        log_phase_summary("PHASE 2", phase2_rows)

    total_discovered = len(rows) + skipped_history_count + no_new_torrents_count + failed_count
    logger.info("=" * 30)
    logger.info("OVERALL SUMMARY")
    logger.info("=" * 30)
    logger.info(f"Total movies discovered: {total_discovered}")
    logger.info(f"Successfully processed: {len(rows)}")
    if use_history_for_loading and not ignore_history:
        logger.info(f"Skipped already parsed in previous runs: {skipped_history_count}")
    elif ignore_history:
        logger.info("History reading was disabled (--ignore-history), but results will still be saved to history")
    logger.info(f"No new torrents to download: {no_new_torrents_count}")
    logger.info(f"Failed to fetch/parse: {failed_count}")
    logger.info(f"Current parsed links in memory: {len(state.parsed_links)}")

    if rows:
        n = len(rows)
        sub = sum(1 for r in rows if r['subtitle'])
        hsub = sum(1 for r in rows if r['hacked_subtitle'])
        hnosub = sum(1 for r in rows if r['hacked_no_subtitle'])
        nosub = sum(1 for r in rows if r['no_subtitle'])
        logger.info(f"Overall subtitle torrents: {sub} ({sub / n * 100:.1f}%)")
        logger.info(f"Overall hacked subtitle torrents: {hsub} ({hsub / n * 100:.1f}%)")
        logger.info(f"Overall hacked no-subtitle torrents: {hnosub} ({hnosub / n * 100:.1f}%)")
        logger.info(f"Overall no-subtitle torrents: {nosub} ({nosub / n * 100:.1f}%)")

    if not dry_run:
        logger.info(f"Results saved to: {csv_path}")
        if use_history_for_saving:
            logger.info(f"History saved to: {os.path.join(REPORTS_DIR, PARSED_MOVIES_CSV)}")
        print(f"SPIDER_OUTPUT_CSV={csv_path}")
        if dedup_csv_path:
            print(f"SPIDER_DEDUP_CSV={dedup_csv_path}")
    logger.info("=" * 75)

    if use_proxy and PROXY_MODE in ('pool', 'single') and state.global_proxy_pool is not None:
        logger.info("")
        state.global_proxy_pool.log_statistics(level=logging.INFO)
        logger.info("")
        logger.info("=" * 75)
        logger.info("PROXY BAN STATUS")
        logger.info("=" * 75)
        ban_summary = state.global_proxy_pool.get_ban_summary(include_ip=False)
        logger.info(ban_summary)
        logger.info("=" * 75)

    if state.proxy_ban_html_files:
        logger.info("")
        logger.info("=" * 75)
        logger.info("PROXY BAN HTML FILES")
        logger.info("=" * 75)
        logger.info(f"Saved {len(state.proxy_ban_html_files)} proxy ban HTML file(s) for debugging:")
        for html_file in state.proxy_ban_html_files:
            logger.info(f"  - {html_file}")
        logger.info("=" * 75)
        print(f"PROXY_BAN_HTML_FILES={','.join(state.proxy_ban_html_files)}")

    proxies_were_banned = False
    if phase_mode in ['1', 'all']:
        proxies_were_banned = proxies_were_banned or any_proxy_banned
    if phase_mode in ['2', 'all']:
        proxies_were_banned = proxies_were_banned or any_proxy_banned_phase2

    if proxies_were_banned:
        logger.error("=" * 75)
        logger.error("CRITICAL: PROXY BAN DETECTED DURING THIS RUN")
        logger.error("=" * 75)
        logger.error("One or more proxies were marked as BANNED due to failure to retrieve movie list.")
        logger.error("This indicates the proxy IP may be blocked by JavDB.")
        logger.error("Please check proxy ban status and consider using different proxies.")
        sys.exit(2)

    if len(rows) == 0 and use_proxy:
        logger.warning("=" * 75)
        logger.warning("WARNING: No entries found while using proxy")
        logger.warning("=" * 75)
        logger.warning("This might indicate proxy issues or CF bypass service problems.")
