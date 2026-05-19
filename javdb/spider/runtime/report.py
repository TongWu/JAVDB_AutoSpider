"""Summary reporting for spider results."""

import os
import sys
import logging

from javdb.infra.logging import (
    get_logger,
    log_section,
    log_summary_block,
)

import javdb.spider.runtime.state as state
from javdb.spider.runtime.config import (
    PROXY_MODE, REPORTS_DIR, PARSED_MOVIES_CSV,
    INCLUDE_DOWNLOADED_IN_REPORT,
)

logger = get_logger(__name__)


def _torrent_type_pairs(rows):
    """Render torrent-type counts as ordered (label, value) pairs.

    Centralised so the per-phase block and the overall block stay in
    sync (and so future torrent types only need to be added once).
    """
    n = len(rows)
    if n == 0:
        return []
    sub = sum(1 for r in rows if r['subtitle'])
    hsub = sum(1 for r in rows if r['hacked_subtitle'])
    hnosub = sum(1 for r in rows if r['hacked_no_subtitle'])
    nosub = sum(1 for r in rows if r['no_subtitle'])
    pct = lambda v: f"{v} ({v / n * 100:.1f}%)"
    return [
        ('subtitle',           pct(sub)),
        ('hacked_subtitle',    pct(hsub)),
        ('hacked_no_subtitle', pct(hnosub)),
        ('no_subtitle',        pct(nosub)),
    ]


def log_phase_summary(phase_name: str, phase_rows: list) -> None:
    """Log torrent-type statistics for a single processing phase."""
    pairs = [('entries', str(len(phase_rows)))]
    pairs.extend(_torrent_type_pairs(phase_rows))
    log_summary_block(
        logger,
        f"{phase_name} SUMMARY",
        pairs,
        emoji='🎬',
    )


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
    if phase_mode in ['1', 'all']:
        log_phase_summary("PHASE 1", phase1_rows)
    if phase_mode in ['2', 'all']:
        log_phase_summary("PHASE 2", phase2_rows)

    total_discovered = len(rows) + skipped_history_count + no_new_torrents_count + failed_count

    overall_pairs = []
    if parse_all:
        overall_pairs.append(('pages', f"{start_page} to last page with results"))
    else:
        overall_pairs.append(('pages', f"{start_page}-{end_page}"))
    overall_pairs.extend([
        ('found',   total_discovered),
        ('parsed',  len(rows)),
        ('skipped', skipped_history_count if (use_history_for_loading and not ignore_history) else 0),
        ('no_new',  no_new_torrents_count),
        ('failed',  failed_count),
    ])
    overall_pairs.extend(_torrent_type_pairs(rows))
    if not dry_run:
        overall_pairs.append(('csv', csv_path))
        if use_history_for_saving:
            overall_pairs.append(('history', os.path.join(REPORTS_DIR, PARSED_MOVIES_CSV)))

    log_summary_block(logger, "OVERALL SUMMARY", overall_pairs, emoji='📊')

    if not dry_run:
        # Machine-readable footer for the GitHub Actions workflow / shell
        # parent.  Kept as plain ``print`` (not logging) so it stays on
        # stdout regardless of the log style and is easy for ``$(...)``
        # capture in shell.  The ``SPIDER_STAT_*`` lines are consumed by
        # the workflow's step-summary writer to populate the run's
        # Markdown summary panel.
        print(f"SPIDER_OUTPUT_CSV={csv_path}")
        if dedup_csv_path:
            print(f"SPIDER_DEDUP_CSV={dedup_csv_path}")
        page_range = (
            f"{start_page}-*" if parse_all else f"{start_page}-{end_page}"
        )
        print(f"SPIDER_STAT_PAGES={page_range}")
        print(f"SPIDER_STAT_FOUND={total_discovered}")
        print(f"SPIDER_STAT_PARSED={len(rows)}")
        print(f"SPIDER_STAT_SKIPPED={skipped_history_count}")
        print(f"SPIDER_STAT_FAILED={failed_count}")
        print(f"SPIDER_STAT_NO_NEW={no_new_torrents_count}")

    if ignore_history:
        logger.info("History reading was disabled (--ignore-history); results still saved to history")
    logger.debug("Current parsed links in memory: %d", len(state.parsed_links))

    if use_proxy and PROXY_MODE in ('pool', 'single') and state.global_proxy_pool is not None:
        # ``log_statistics`` itself emits a one-line INFO summary;
        # per-proxy detail rows are at DEBUG so a healthy run prints a
        # single proxy-pool row.  Operators who want forensic detail
        # flip ``LOG_LEVEL=DEBUG`` and get every proxy on its own line.
        state.global_proxy_pool.log_statistics(level=logging.INFO)

        ban_summary = state.global_proxy_pool.get_ban_summary(include_ip=False)
        if ban_summary and ban_summary.strip() and 'No proxies' not in ban_summary:
            log_section(logger, "PROXY BAN STATUS", emoji='🛡')
            logger.info(ban_summary)

    if state.proxy_ban_html_files:
        log_section(logger, "PROXY BAN HTML FILES", emoji='🛡')
        logger.info("Saved %d proxy ban HTML file(s) for debugging:", len(state.proxy_ban_html_files))
        for html_file in state.proxy_ban_html_files:
            logger.info("  - %s", html_file)
        print(f"PROXY_BAN_HTML_FILES={','.join(state.proxy_ban_html_files)}")

    proxies_were_banned = False
    if phase_mode in ['1', 'all']:
        proxies_were_banned = proxies_were_banned or any_proxy_banned
    if phase_mode in ['2', 'all']:
        proxies_were_banned = proxies_were_banned or any_proxy_banned_phase2

    if proxies_were_banned:
        log_section(logger, "PROXY BAN DETECTED DURING THIS RUN", emoji='⚠', level=logging.WARNING)
        logger.warning(
            "One or more proxies were marked BANNED — IP may be blocked by JavDB; check ban status."
        )
        if len(rows) > 0:
            logger.info(
                "Spider completed successfully with %d results despite proxy ban(s) — "
                "remaining workers finished the job.",
                len(rows),
            )
        else:
            logger.error(
                "Spider produced NO results and proxy ban(s) were detected — all proxies may be blocked."
            )
            sys.exit(2)

    if len(rows) == 0 and use_proxy:
        log_section(logger, "WARNING: No entries found while using proxy", emoji='⚠', level=logging.WARNING)
        logger.warning("This might indicate proxy issues or CF bypass service problems.")
