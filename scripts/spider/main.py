"""Main entry point for the spider."""

import os
import sys
import time
import logging
import requests
from datetime import datetime

from utils.logging_config import get_logger
from utils.history_manager import load_parsed_movies_history, validate_history_file
from utils.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials
from utils.filename_helper import generate_output_csv_name

import scripts.spider.state as state
from scripts.spider.config_loader import (
    BASE_URL,
    REPORTS_DIR, DAILY_REPORT_DIR, AD_HOC_DIR, PARSED_MOVIES_CSV,
    CF_BYPASS_ENABLED, CF_BYPASS_SERVICE_PORT,
    PROXY_MODE, PROXY_POOL, PROXY_MODULES,
    MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX,
    PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS,
    JAVDB_SESSION_COOKIE,
    GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH,
    ENABLE_DEDUP, RCLONE_INVENTORY_CSV, DEDUP_CSV,
)
from scripts.spider.dedup_checker import (
    load_rclone_inventory,
    should_skip_from_rclone,
    check_dedup_upgrade,
    append_dedup_record,
)
from scripts.spider.cli import parse_arguments, OUTPUT_CSV
from scripts.spider.sleep_manager import movie_sleep_mgr
from scripts.spider.index_fetcher import fetch_all_index_pages
from scripts.spider.parallel import process_detail_entries_parallel
from scripts.spider.sequential import process_phase_entries_sequential
from scripts.spider.report import generate_summary_report

logger = get_logger(__name__)


def main():
    args = parse_arguments()

    start_page = args.start_page
    end_page = args.end_page
    phase_mode = args.phase
    custom_url = args.url
    dry_run = args.dry_run
    ignore_history = args.ignore_history
    use_history = args.use_history
    parse_all = args.all
    ignore_release_date = args.ignore_release_date
    use_proxy = args.use_proxy
    use_cf_bypass = False
    max_movies_phase1 = args.max_movies_phase1
    max_movies_phase2 = args.max_movies_phase2
    sequential = args.sequential
    enable_dedup = args.enable_dedup or ENABLE_DEDUP

    ban_log_file = os.path.join(REPORTS_DIR, 'proxy_bans.csv')
    state.setup_proxy_pool(ban_log_file, use_proxy)
    state.initialize_request_handler()

    # Determine output directory and filename
    if args.url:
        output_dated_dir = state.ensure_report_dated_dir(AD_HOC_DIR)
        if args.output_file:
            output_csv = args.output_file
        else:
            output_csv = generate_output_csv_name(custom_url, use_proxy=use_proxy)
        csv_path = os.path.join(output_dated_dir, output_csv)
        use_history_for_loading = use_history
        use_history_for_saving = True
    else:
        output_dated_dir = state.ensure_report_dated_dir(DAILY_REPORT_DIR)
        output_csv = args.output_file if args.output_file else OUTPUT_CSV
        csv_path = os.path.join(output_dated_dir, output_csv)
        use_history_for_loading = True
        use_history_for_saving = True

    logger.info("Starting JavDB spider...")
    logger.info(f"Arguments: start_page={start_page}, end_page={end_page}, phase={phase_mode}")
    if custom_url:
        logger.info(f"Custom URL: {custom_url}")
        if use_history:
            logger.info("AD HOC MODE: History filter ENABLED (--use-history) - will skip entries already in history")
        else:
            logger.info("AD HOC MODE: Will process all entries (history filter disabled by default)")
    if dry_run:
        logger.info("DRY RUN MODE: No CSV file will be written")
    if ignore_history and not custom_url:
        logger.info("IGNORE HISTORY: Will scrape all pages without checking history (but still save to history)")
    if parse_all:
        logger.info("PARSE ALL MODE: Will continue until empty page is found")
    if ignore_release_date:
        logger.info("IGNORE RELEASE DATE: Will process all entries regardless of today/yesterday tags")

    if use_proxy:
        logger.info("MODE: Proxy (CF bypass available as automatic fallback)")
    else:
        logger.info("MODE: Direct (CF bypass available as automatic fallback)")
    if CF_BYPASS_ENABLED:
        logger.info(f"CF Bypass: Enabled as fallback (service port: {CF_BYPASS_SERVICE_PORT})")
    else:
        logger.info("CF Bypass: Globally disabled via CF_BYPASS_ENABLED=False in config.py")

    if use_proxy:
        if state.global_proxy_pool is not None:
            stats = state.global_proxy_pool.get_statistics()
            if PROXY_MODE == 'pool':
                logger.info(f"PROXY POOL MODE: {stats['total_proxies']} proxies configured with automatic failover")
            elif PROXY_MODE == 'single':
                logger.info("SINGLE PROXY MODE: Using main proxy only (no automatic failover)")
                if stats['total_proxies'] > 0:
                    main_proxy_name = stats['proxies'][0]['name']
                    logger.info(f"Main proxy: {main_proxy_name}")
            if not PROXY_MODULES:
                logger.warning("PROXY ENABLED: But PROXY_MODULES is empty - no modules will use proxy")
            elif 'all' in PROXY_MODULES:
                logger.info("PROXY ENABLED: Using proxy for ALL modules")
            else:
                logger.info(f"PROXY ENABLED: Using proxy for modules {PROXY_MODULES}")
        else:
            logger.warning("PROXY ENABLED: But no proxy configured in config.py")

    use_parallel = (
        use_proxy
        and not sequential
        and PROXY_MODE == 'pool'
        and PROXY_POOL
        and len(PROXY_POOL) > 1
    )
    if use_parallel:
        logger.info(f"PARALLEL MODE: {len(PROXY_POOL)} workers (one per proxy) for detail page processing")
    elif use_proxy and PROXY_MODE == 'pool' and sequential:
        logger.info("SEQUENTIAL MODE: Parallel disabled by --sequential flag")

    state.ensure_reports_dir()

    history_file = os.path.join(REPORTS_DIR, PARSED_MOVIES_CSV)
    parsed_movies_history_phase1 = {}
    parsed_movies_history_phase2 = {}

    if use_history_for_loading:
        if os.path.exists(history_file):
            logger.info("Validating history file integrity...")
            if not validate_history_file(history_file):
                logger.warning("History file validation failed - duplicates may be present")
        if not os.path.exists(history_file):
            with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('href,phase,video_code,parsed_date,torrent_type\n')
            logger.info(f"Created new history file: {history_file}")
        if ignore_history:
            parsed_movies_history_phase1 = {}
            parsed_movies_history_phase2 = {}
        else:
            parsed_movies_history_phase1 = load_parsed_movies_history(history_file, phase=1)
            parsed_movies_history_phase2 = load_parsed_movies_history(history_file, phase=None)
    else:
        if use_history_for_saving and not os.path.exists(history_file):
            with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('href,phase,video_code,parsed_date,torrent_type\n')
            logger.info(f"Created new history file for ad hoc mode: {history_file}")

    # Load rclone inventory as additional skip data source
    rclone_inventory_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)
    dedup_csv_path = os.path.join(REPORTS_DIR, DEDUP_CSV)
    rclone_inventory = {}
    if os.path.exists(rclone_inventory_path):
        rclone_inventory = load_rclone_inventory(rclone_inventory_path)
        logger.info(f"Loaded rclone inventory: {len(rclone_inventory)} unique video codes")
    else:
        logger.info(f"Rclone inventory not found ({rclone_inventory_path}) – rclone skip/dedup disabled")

    if enable_dedup:
        logger.info("DEDUP MODE: Enabled – will detect upgrade opportunities against rclone inventory")
    else:
        logger.info("DEDUP MODE: Disabled")

    session = requests.Session()
    logger.info("Initialized requests session")

    all_index_results_phase1 = []
    rows = []
    phase1_rows = []
    phase2_rows = []
    fieldnames = [
        'href', 'video_code', 'page', 'actor', 'rate', 'comment_number',
        'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
        'size_hacked_subtitle', 'size_hacked_no_subtitle',
        'size_subtitle', 'size_no_subtitle',
    ]

    max_consecutive_empty = 3
    any_proxy_banned = False
    any_proxy_banned_phase2 = False

    skipped_history_count = 0
    failed_count = 0
    no_new_torrents_count = 0
    total_entries_phase1 = 0

    # ======================================================================
    # Fetch all index pages
    # ======================================================================
    idx_result = fetch_all_index_pages(
        session=session, start_page=start_page, end_page=end_page,
        parse_all=parse_all, phase_mode=phase_mode, custom_url=custom_url,
        ignore_release_date=ignore_release_date, use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass, max_consecutive_empty=max_consecutive_empty,
        output_csv=output_csv, output_dated_dir=output_dated_dir,
        csv_path=csv_path, user_specified_output=bool(args.output_file),
        parsed_movies_history_phase1=parsed_movies_history_phase1,
        parsed_movies_history_phase2=parsed_movies_history_phase2,
    )
    all_index_results_phase1 = idx_result['all_index_results_phase1']
    all_index_results_phase2 = idx_result['all_index_results_phase2']
    any_proxy_banned = idx_result['any_proxy_banned']
    use_proxy = idx_result['use_proxy']
    use_cf_bypass = idx_result['use_cf_bypass']
    csv_path = idx_result['csv_path']

    # ======================================================================
    # Process Phase 1 entries
    # ======================================================================
    if phase_mode in ['1', 'all']:
        logger.info("=" * 75)
        original_count_phase1 = len(all_index_results_phase1)
        if max_movies_phase1 is not None and max_movies_phase1 > 0 and original_count_phase1 > max_movies_phase1:
            logger.info(f"PHASE 1: Discovered {original_count_phase1} entries, limiting to {max_movies_phase1} (--max-movies-phase1)")
            all_index_results_phase1 = all_index_results_phase1[:max_movies_phase1]

        if custom_url is not None:
            logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} entries with subtitle (AD HOC MODE)")
        else:
            logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} entries with subtitle")
        logger.info("=" * 75)

        total_entries_phase1 = len(all_index_results_phase1)

        if use_parallel:
            p1_result = process_detail_entries_parallel(
                entries=all_index_results_phase1, phase=1,
                history_data=parsed_movies_history_phase1,
                history_file=history_file, csv_path=csv_path,
                fieldnames=fieldnames, dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                ban_log_file=ban_log_file,
                rclone_inventory=rclone_inventory,
                enable_dedup=enable_dedup,
                dedup_csv_path=dedup_csv_path,
            )
        else:
            p1_result = process_phase_entries_sequential(
                entries=all_index_results_phase1, phase=1,
                history_data=parsed_movies_history_phase1,
                history_file=history_file, csv_path=csv_path,
                fieldnames=fieldnames, dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                session=session, use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
                rclone_inventory=rclone_inventory,
                enable_dedup=enable_dedup,
                dedup_csv_path=dedup_csv_path,
            )
            use_proxy = p1_result['use_proxy']
            use_cf_bypass = p1_result['use_cf_bypass']

        phase1_rows = p1_result['rows']
        rows.extend(phase1_rows)
        skipped_history_count += p1_result['skipped_history']
        failed_count += p1_result['failed']
        no_new_torrents_count += p1_result['no_new_torrents']

    # ======================================================================
    # Process Phase 2 entries
    # ======================================================================
    if phase_mode in ['2', 'all']:
        if phase_mode == 'all':
            if total_entries_phase1 > 0:
                t = movie_sleep_mgr.get_sleep_time()
                logger.info(f"Phase transition cooldown: {t}s before Phase 2")
                time.sleep(t)
            else:
                logger.info("Phase 1 had no entries to process, skipping phase transition cooldown")

        logger.info("=" * 75)
        original_count_phase2 = len(all_index_results_phase2)
        if max_movies_phase2 is not None and max_movies_phase2 > 0 and original_count_phase2 > max_movies_phase2:
            logger.info(f"PHASE 2: Discovered {original_count_phase2} entries, limiting to {max_movies_phase2} (--max-movies-phase2)")
            all_index_results_phase2 = all_index_results_phase2[:max_movies_phase2]

        if custom_url is not None:
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} entries (AD HOC MODE - all filters disabled)")
        else:
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} entries (rate > {PHASE2_MIN_RATE}, comments > {PHASE2_MIN_COMMENTS})")
        logger.info("=" * 75)

        if use_parallel:
            p2_result = process_detail_entries_parallel(
                entries=all_index_results_phase2, phase=2,
                history_data=parsed_movies_history_phase2,
                history_file=history_file, csv_path=csv_path,
                fieldnames=fieldnames, dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                ban_log_file=ban_log_file,
                rclone_inventory=rclone_inventory,
                enable_dedup=enable_dedup,
                dedup_csv_path=dedup_csv_path,
            )
        else:
            p2_result = process_phase_entries_sequential(
                entries=all_index_results_phase2, phase=2,
                history_data=parsed_movies_history_phase2,
                history_file=history_file, csv_path=csv_path,
                fieldnames=fieldnames, dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                session=session, use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
                rclone_inventory=rclone_inventory,
                enable_dedup=enable_dedup,
                dedup_csv_path=dedup_csv_path,
            )
            use_proxy = p2_result['use_proxy']
            use_cf_bypass = p2_result['use_cf_bypass']

        phase2_rows = p2_result['rows']
        rows.extend(phase2_rows)
        skipped_history_count += p2_result['skipped_history']
        failed_count += p2_result['failed']
        no_new_torrents_count += p2_result['no_new_torrents']

    if not dry_run:
        logger.info(f"CSV file written incrementally to: {csv_path}")

    generate_summary_report(
        phase_mode=phase_mode, parse_all=parse_all,
        start_page=start_page, end_page=end_page,
        max_consecutive_empty=max_consecutive_empty,
        phase1_rows=phase1_rows, phase2_rows=phase2_rows, rows=rows,
        use_history_for_loading=use_history_for_loading,
        ignore_history=ignore_history,
        skipped_history_count=skipped_history_count,
        failed_count=failed_count,
        no_new_torrents_count=no_new_torrents_count,
        csv_path=csv_path, dry_run=dry_run,
        use_history_for_saving=use_history_for_saving,
        use_proxy=use_proxy,
        any_proxy_banned=any_proxy_banned,
        any_proxy_banned_phase2=any_proxy_banned_phase2,
    )

    from_pipeline = args.from_pipeline if hasattr(args, 'from_pipeline') else False

    if not dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing spider results...")
        flush_log_handlers()
        files_to_commit = [REPORTS_DIR, 'logs/']
        commit_message = f"Auto-commit: Spider results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        git_commit_and_push(
            files_to_add=files_to_commit,
            commit_message=commit_message,
            from_pipeline=from_pipeline,
            git_username=GIT_USERNAME,
            git_password=GIT_PASSWORD,
            git_repo_url=GIT_REPO_URL,
            git_branch=GIT_BRANCH,
        )
    elif not dry_run:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")
