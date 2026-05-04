"""Spider runtime orchestration service."""

import os
import sys

import logging
import requests
from datetime import datetime

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.history_manager import load_parsed_movies_history, validate_history_file
from packages.python.javdb_platform.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials
from packages.python.javdb_core.filename_helper import generate_output_csv_name
from packages.python.javdb_platform.path_helper import ensure_dated_dir
from packages.python.javdb_platform.csv_writer import set_active_session
from packages.python.javdb_platform.proxy_policy import (
    describe_proxy_override,
    resolve_proxy_override,
    should_proxy_module,
)

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_spider.runtime.config import (
    BASE_URL,
    REPORTS_DIR, DAILY_REPORT_DIR, AD_HOC_DIR, PARSED_MOVIES_CSV,
    CF_BYPASS_ENABLED, CF_BYPASS_SERVICE_PORT,
    PROXY_MODE, PROXY_POOL, PROXY_MODULES,
    PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS,
    JAVDB_SESSION_COOKIE,
    GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH,
    RCLONE_INVENTORY_CSV, DEDUP_CSV, DEDUP_DIR,
    ENABLE_REDOWNLOAD, REDOWNLOAD_SIZE_THRESHOLD,
)
from packages.python.javdb_spider.services.dedup import (
    load_rclone_inventory,
    should_skip_from_rclone,
    check_dedup_upgrade,
    append_dedup_record,
)
from packages.python.javdb_spider.app.cli import parse_arguments, OUTPUT_CSV
from packages.python.javdb_spider.runtime.sleep import movie_sleep_mgr
from packages.python.javdb_spider.fetch.index import fetch_all_index_pages
from packages.python.javdb_spider.detail.parallel_mode import build_parallel_detail_backend
from packages.python.javdb_spider.detail.runner import process_detail_entries
from packages.python.javdb_spider.detail.sequential_mode import build_sequential_detail_backend
from packages.python.javdb_spider.runtime.report import generate_summary_report
from packages.python.javdb_spider.fetch.fallback import AdhocLoginFailedError

logger = get_logger(__name__)


def create_detail_backend(
    *,
    use_parallel: bool,
    use_cookie: bool,
    is_adhoc_mode: bool,
    session,
    use_proxy: bool,
    use_cf_bypass: bool,
):
    """Create the detail backend chosen by the current runtime mode."""

    if use_parallel:
        return build_parallel_detail_backend(
            use_cookie=use_cookie,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        )

    return build_sequential_detail_backend(
        session,
        use_cookie=use_cookie,
        is_adhoc_mode=is_adhoc_mode,
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
    )


def _main():
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
    proxy_override = resolve_proxy_override(args.use_proxy, args.no_proxy)
    use_proxy = should_proxy_module('spider', proxy_override, PROXY_MODULES, proxy_mode=PROXY_MODE)
    use_cf_bypass = False
    always_bypass_time = args.always_bypass_time
    max_movies_phase1 = args.max_movies_phase1
    max_movies_phase2 = args.max_movies_phase2
    sequential = args.sequential
    enable_dedup = args.enable_dedup
    rclone_filter = not args.no_rclone_filter
    enable_redownload = args.enable_redownload or ENABLE_REDOWNLOAD
    redownload_threshold = args.redownload_threshold if args.redownload_threshold is not None else REDOWNLOAD_SIZE_THRESHOLD

    if always_bypass_time is not None and always_bypass_time < 0:
        logger.error("--always-bypass-time must be >= 0")
        sys.exit(2)

    state.always_bypass_time = always_bypass_time

    if args.disable_all_filters:
        ignore_history = True
        use_history = False
        ignore_release_date = True
        rclone_filter = False

    state.setup_proxy_pool(use_proxy)
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
        use_history_for_loading = not args.disable_all_filters
        use_history_for_saving = True

    logger.info("Starting JavDB spider...")
    if args.disable_all_filters:
        logger.info("⚠️  ALL FILTERS DISABLED: history, rclone inventory, release date filters are all bypassed")
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

    logger.info(f"Proxy policy for spider: {describe_proxy_override(proxy_override)}")
    if use_proxy:
        logger.info("MODE: Proxy (CF bypass available as automatic fallback)")
    else:
        logger.info("MODE: Direct (CF bypass available as automatic fallback)")
    if CF_BYPASS_ENABLED:
        logger.info(f"CF Bypass: Enabled as fallback (service port: {CF_BYPASS_SERVICE_PORT})")
        if always_bypass_time is None:
            logger.info("CF Bypass sticky mode: disabled (always direct-first)")
        elif always_bypass_time == 0:
            logger.info("CF Bypass sticky mode: enabled for full runtime when fallback succeeds")
        else:
            logger.info(
                f"CF Bypass sticky mode: enabled for {always_bypass_time} minute(s) when fallback succeeds"
            )
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
    if enable_dedup:
        dedup_dated_dir = ensure_dated_dir(DEDUP_DIR)
        dedup_filename = f"Dedup_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        dedup_csv_path = os.path.join(dedup_dated_dir, dedup_filename)
    else:
        dedup_csv_path = os.path.join(REPORTS_DIR, DEDUP_CSV)
    rclone_inventory = {}
    if os.path.exists(rclone_inventory_path):
        rclone_inventory = load_rclone_inventory(rclone_inventory_path)
        logger.info(f"Loaded rclone inventory: {len(rclone_inventory)} unique video codes")
    else:
        logger.info(f"Rclone inventory not found ({rclone_inventory_path}) – rclone skip/dedup disabled")

    if rclone_filter:
        logger.info("RCLONE FILTER: Enabled - will skip entries already in rclone inventory with 中字")
    else:
        logger.info("RCLONE FILTER: Disabled - all entries will be processed regardless of rclone inventory")

    if enable_dedup:
        logger.info("DEDUP MODE: Enabled – will detect upgrade opportunities against rclone inventory")
    else:
        logger.info("DEDUP MODE: Disabled")

    if enable_redownload:
        logger.info(f"RE-DOWNLOAD (洗版): Enabled – threshold {redownload_threshold * 100:.0f}%")
    else:
        logger.info("RE-DOWNLOAD (洗版): Disabled")

    session = requests.Session()
    logger.info("Initialized requests session")

    all_index_results_phase1 = []
    rows = []
    phase1_rows = []
    phase2_rows = []
    fieldnames = [
        'href', 'video_code', 'page', 'actor', 'rate', 'comment_number',
        'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
    ]

    max_consecutive_empty = 3
    any_proxy_banned = False
    any_proxy_banned_phase2 = False

    skipped_history_count = 0
    failed_count = 0
    failed_movies_list = []
    no_new_torrents_count = 0
    total_entries_phase1 = 0

    # ======================================================================
    # Fetch all index pages
    # ======================================================================
    try:
        idx_result = fetch_all_index_pages(
            session=session, start_page=start_page, end_page=end_page,
            parse_all=parse_all, phase_mode=phase_mode, custom_url=custom_url,
            ignore_release_date=ignore_release_date, use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass, max_consecutive_empty=max_consecutive_empty,
            output_csv=output_csv, output_dated_dir=output_dated_dir,
            csv_path=csv_path, user_specified_output=bool(args.output_file),
            parsed_movies_history_phase1=parsed_movies_history_phase1,
            parsed_movies_history_phase2=parsed_movies_history_phase2,
            use_parallel=use_parallel,
        )
    except AdhocLoginFailedError as e:
        logger.error(f"ADHOC SPIDER FAILED: Login failed during index page fetch — {e}")
        logger.error("Aborting spider run. Please check your session cookie or login credentials.")
        sys.exit(1)
    all_index_results_phase1 = idx_result['all_index_results_phase1']
    all_index_results_phase2 = idx_result['all_index_results_phase2']
    any_proxy_banned = idx_result['any_proxy_banned']
    use_proxy = idx_result['use_proxy']
    use_cf_bypass = idx_result['use_cf_bypass']
    csv_path = idx_result['csv_path']

    # Create a report session in SQLite (when enabled)
    _session_id = None
    try:
        from packages.python.javdb_platform.config_helper import use_sqlite as _use_sqlite
        if _use_sqlite():
            from packages.python.javdb_platform.db import init_db, db_create_report_session
            from packages.python.javdb_core.url_helper import detect_url_type, extract_url_identifier
            init_db()
            report_type = 'adhoc' if custom_url else 'daily'
            report_date = datetime.now().strftime('%Y%m%d')
            url_type = None
            display_name = None
            if custom_url:
                try:
                    url_type = detect_url_type(custom_url)
                    display_name = extract_url_identifier(custom_url)
                except Exception:
                    pass
            _session_id = db_create_report_session(
                report_type=report_type,
                report_date=report_date,
                csv_filename=os.path.basename(csv_path),
                url_type=url_type,
                display_name=display_name,
                url=custom_url,
                start_page=start_page,
            )
            set_active_session(_session_id)
            # Tag every history / dedup / align write that follows in this
            # process with this session id so a downstream rollback can
            # surgically undo just our rows (X3 hybrid strategy).
            try:
                from packages.python.javdb_platform.db import (
                    set_active_session_id as _set_active_session_id,
                )
                _set_active_session_id(_session_id)
            except Exception as _e:
                logger.warning(
                    f"Could not propagate session_id to db audit context: {_e}"
                )
            logger.info(f"Created report session: id={_session_id}")
    except Exception as e:
        logger.warning(f"Failed to create report session: {e}")

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

        p1_backend = create_detail_backend(
            use_parallel=use_parallel,
            use_cookie=custom_url is not None,
            is_adhoc_mode=custom_url is not None,
            session=session,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        )
        p1_result = process_detail_entries(
            backend=p1_backend,
            entries=all_index_results_phase1,
            phase=1,
            history_data=parsed_movies_history_phase1,
            history_file=history_file,
            csv_path=csv_path,
            fieldnames=fieldnames,
            dry_run=dry_run,
            use_history_for_saving=use_history_for_saving,
            is_adhoc_mode=custom_url is not None,
            rclone_inventory=rclone_inventory,
            rclone_filter=rclone_filter,
            enable_dedup=enable_dedup,
            dedup_csv_path=dedup_csv_path,
            enable_redownload=enable_redownload,
            redownload_threshold=redownload_threshold,
            include_recent_release_filters=use_parallel,
            log_duplicate_skips=not use_parallel,
        )
        use_proxy = p1_result['use_proxy']
        use_cf_bypass = p1_result['use_cf_bypass']

        phase1_rows = p1_result['rows']
        rows.extend(phase1_rows)
        skipped_history_count += p1_result['skipped_history']
        failed_count += p1_result['failed']
        failed_movies_list.extend(p1_result.get('failed_movies', []))
        no_new_torrents_count += p1_result['no_new_torrents']

    # ======================================================================
    # Process Phase 2 entries
    # ======================================================================
    if phase_mode in ['2', 'all']:
        if phase_mode == 'all':
            if total_entries_phase1 > 0:
                t = movie_sleep_mgr.sleep()
                logger.info("Phase transition cooldown: %.1fs before Phase 2", t)
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

        p2_backend = create_detail_backend(
            use_parallel=use_parallel,
            use_cookie=custom_url is not None,
            is_adhoc_mode=custom_url is not None,
            session=session,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        )
        p2_result = process_detail_entries(
            backend=p2_backend,
            entries=all_index_results_phase2,
            phase=2,
            history_data=parsed_movies_history_phase2,
            history_file=history_file,
            csv_path=csv_path,
            fieldnames=fieldnames,
            dry_run=dry_run,
            use_history_for_saving=use_history_for_saving,
            is_adhoc_mode=custom_url is not None,
            rclone_inventory=rclone_inventory,
            rclone_filter=rclone_filter,
            enable_dedup=enable_dedup,
            dedup_csv_path=dedup_csv_path,
            enable_redownload=enable_redownload,
            redownload_threshold=redownload_threshold,
            include_recent_release_filters=use_parallel,
            log_duplicate_skips=not use_parallel,
        )
        use_proxy = p2_result['use_proxy']
        use_cf_bypass = p2_result['use_cf_bypass']

        phase2_rows = p2_result['rows']
        rows.extend(phase2_rows)
        skipped_history_count += p2_result['skipped_history']
        failed_count += p2_result['failed']
        failed_movies_list.extend(p2_result.get('failed_movies', []))
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
        dedup_csv_path=dedup_csv_path if enable_dedup else None,
    )

    # Save spider stats and end_page to SQLite (when session exists)
    if _session_id is not None:
        try:
            from packages.python.javdb_platform.db import db_save_spider_stats, get_db, REPORTS_DB_PATH
            p1_discovered = len(all_index_results_phase1) if phase_mode in ('1', 'all') else 0
            p1_processed = len(phase1_rows)
            _p1 = p1_result if 'p1_result' in locals() else {}
            p1_skipped = _p1.get('skipped_history', 0) if _p1 else 0
            p1_no_new = _p1.get('no_new_torrents', 0) if _p1 else 0
            p1_failed = _p1.get('failed', 0) if _p1 else 0

            p2_discovered = len(all_index_results_phase2) if phase_mode in ('2', 'all') else 0
            p2_processed = len(phase2_rows)
            _p2 = p2_result if 'p2_result' in locals() else {}
            p2_skipped = _p2.get('skipped_history', 0) if _p2 else 0
            p2_no_new = _p2.get('no_new_torrents', 0) if _p2 else 0
            p2_failed = _p2.get('failed', 0) if _p2 else 0

            stats = {
                'phase1_discovered': p1_discovered, 'phase1_processed': p1_processed,
                'phase1_skipped': p1_skipped, 'phase1_no_new': p1_no_new, 'phase1_failed': p1_failed,
                'phase2_discovered': p2_discovered, 'phase2_processed': p2_processed,
                'phase2_skipped': p2_skipped, 'phase2_no_new': p2_no_new, 'phase2_failed': p2_failed,
                'total_discovered': p1_discovered + p2_discovered,
                'total_processed': p1_processed + p2_processed,
                'total_skipped': skipped_history_count,
                'total_no_new': no_new_torrents_count,
                'total_failed': failed_count,
                'failed_movies': failed_movies_list,
            }
            db_save_spider_stats(_session_id, stats)

            last_page = idx_result.get('last_valid_page')
            if last_page is not None:
                with get_db(REPORTS_DB_PATH) as conn:
                    conn.execute("UPDATE ReportSessions SET EndPage=? WHERE Id=?", (last_page, _session_id))
        except Exception as e:
            logger.warning(f"Failed to save spider stats: {e}")

        print(f"SPIDER_SESSION_ID={_session_id}")

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


def main():
    try:
        return _main()
    finally:
        try:
            from packages.python.javdb_platform.db import (
                set_active_session_id as _set_active_session_id,
            )
            _set_active_session_id(None)
        except Exception as _e:
            logger.warning(
                f"Could not clear db audit session context on exit: {_e}"
            )


class SpiderRunService:
    """Application-service wrapper for the spider runtime."""

    def run(self):
        return main()


__all__ = ["SpiderRunService", "create_detail_backend", "main"]


if __name__ == "__main__":
    raise SystemExit(SpiderRunService().run())
