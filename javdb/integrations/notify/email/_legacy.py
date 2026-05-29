"""
Email Notification Script for JAVDB AutoSpider — bake legacy implementation.

This module retains the legacy CLI surface (``parse_arguments`` / ``main`` /
``__main__``) plus the end-to-end orchestration body extracted into
``run_email_notification_from_options`` during ADR-015 Phase 6. The individual
responsibilities now live in sibling submodules:

- ``log_analysis``   — log parsing, statistics, pending-mode / drift analysis
- ``report_builder`` — subject / body formatting
- ``delivery``       — SMTP send + dry-run + log-to-txt conversion

The bake CLI surface here is removed by IMP-ADR015-07. The canonical command
adapter is :mod:`apps.cli.notify.email`, which maps parsed arguments to
:class:`EmailNotificationOptions` and calls
:func:`javdb.integrations.notify.email.service.run_email_notification`.

Features:
- Analyzes spider, uploader, and pikpak logs for errors
- Sends email with formatted report
- Converts log files to .txt before attaching
- Commits pipeline log after sending
"""

import os
import sys
import argparse
import zipfile
from datetime import datetime

from javdb.infra.logging import get_logger

# Import git helper
from javdb.infra.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult

# Module-level configuration constants (shared source).
from javdb.integrations.notify.email._config import (
    GIT_USERNAME,
    GIT_PASSWORD,
    GIT_REPO_URL,
    GIT_BRANCH,
    PIPELINE_LOG_FILE,
    SPIDER_LOG_FILE,
    UPLOADER_LOG_FILE,
    DAILY_REPORT_DIR,
    AD_HOC_DIR,
    PIKPAK_LOG_FILE,
    _EMAIL_REPORTS_DIR,
    DEDUP_LOG_FILE,
    EMAIL_NOTIFICATION_LOG_FILE,
)

# Read-side analysis helpers (orchestration looks these up as module globals so
# tests that monkeypatch this module continue to work during the bake window).
from javdb.integrations.notify.email.log_analysis import (
    analyze_pikpak_log,
    analyze_pipeline_log,
    analyze_spider_log,
    analyze_uploader_log,
    check_workflow_job_status,
    extract_dedup_statistics,
    extract_pikpak_statistics,
    extract_spider_statistics,
    extract_uploader_statistics,
    find_proxy_ban_html_files,
    extract_proxy_ban_summary,
    get_proxy_ban_summary,
    _build_dual_drift_advisory,
    _evaluate_pending_alerts,
    _is_dedup_enabled,
    _load_pending_verify_records,
)

# Report formatting helpers.
from javdb.integrations.notify.email.report_builder import (
    get_report_display_datetime,
    extract_adhoc_info_from_csv,
    format_adhoc_info,
    find_latest_adhoc_csv,
    find_latest_daily_csv,
    format_email_report,
    _build_drift_diagnosis_section,
    _build_ops_diagnosis_advisory,
    _build_pending_subject_prefix,
    _drift_diagnosis_subject_prefix,
    _format_health_snapshot_section,
    _format_pending_verify_section,
    _resolve_default_health_snapshot,
    _resolve_default_verify_jsonl,
)

# Delivery helpers.
from javdb.integrations.notify.email.delivery import (
    convert_log_to_txt,
    send_email,
)

logger = get_logger(__name__)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Email Notification for JavDB Pipeline')
    parser.add_argument('--csv-path', type=str, help='Path to the CSV file to attach')
    parser.add_argument('--mode', type=str, choices=['daily', 'adhoc'], default='daily',
                        help='Pipeline mode: daily or adhoc (default: daily)')
    parser.add_argument('--dry-run', action='store_true', help='Print email content without sending')
    parser.add_argument('--from-pipeline', action='store_true',
                        help='Running from pipeline.py - use GIT_USERNAME for commits')
    parser.add_argument('--session-id', type=str, default=None,
                        help='Report session ID for fetching stats from SQLite')
    parser.add_argument(
        '--verify-jsonl',
        type=str,
        default=None,
        help=(
            'Path to reports/D1/d1_drift.jsonl. When provided, the email '
            "renders a 'Pending Mode Verification' section using the "
            'pending_session_verify records and may prefix the subject '
            "with [PENDING-ALERT] / [PENDING-PAUSE]. Defaults to "
            '$REPORTS_DIR/D1/d1_drift.jsonl when the file exists.'
        ),
    )
    parser.add_argument(
        '--health-snapshot',
        type=str,
        default=None,
        help=(
            'Path to reports/D1/pending_health_24h.json (Phase 3 Health '
            'Snapshot).  When provided, an additional 24h aggregate '
            'block is rendered after Pending Mode Verification.'
        ),
    )
    return parser.parse_args()


def run_email_notification_from_options(
    options: EmailNotificationOptions,
) -> EmailNotificationResult:
    """Run the end-to-end email notification flow for *options*.

    Extracted from the legacy ``main()`` body during ADR-015 Phase 6. Behaviour
    is unchanged: it analyses logs, loads stats from the SQLite-local mirror,
    builds the report body / subject, sends (or dry-run-fingerprints) the email,
    cleans up temporary attachments, commits the pipeline log, and returns an
    :class:`EmailNotificationResult` whose ``exit_code`` mirrors the legacy
    ``sys.exit`` contract (2 on SMTP failure outside dry-run, else 0).
    """
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

    # P0-6: stats MUST come from the canonical SQLite mirror, never from
    # D1, even in STORAGE_BACKEND=dual. The dual read-path proves that D1
    # can serve queries before cutover, but the email is a *report on
    # what this run actually did* — if D1 is behind by N rows because a
    # dual-write was asymmetric, the email would understate the result
    # and operators would never notice the drift (this is exactly the
    # 2026-05 ReportSessions/SpiderStats -1 incident). The dedicated
    # `_local` variants always open a raw sqlite3 connection regardless
    # of backend.
    _sid = None
    _db_spider_stats = None
    _db_uploader_stats = None
    _db_pikpak_stats = None
    _stats_backend_label = 'sqlite-local'
    try:
        from javdb.infra.config import use_sqlite as _use_sqlite
        if _use_sqlite():
            from javdb.storage.db import (
                init_db,
                db_get_latest_session_local,
                db_get_spider_stats_local,
                db_get_uploader_stats_local,
                db_get_pikpak_stats_local,
                current_backend as _cur_be,
            )
            init_db()
            _stats_backend_label = f"{_cur_be()} (stats forced sqlite-local)"
            _sid = options.session_id
            if _sid is None:
                latest = db_get_latest_session_local()
                if latest:
                    _sid = latest.get('Id', latest.get('id'))
                    logger.debug(f"No --session-id provided, falling back to latest session: {_sid}")
            if _sid is not None:
                _db_spider_stats = db_get_spider_stats_local(_sid)
                _db_uploader_stats = db_get_uploader_stats_local(_sid)
                _db_pikpak_stats = db_get_pikpak_stats_local(_sid)
    except Exception as e:
        logger.debug(f"SQLite stats not available: {e}")

    if _db_spider_stats:
        spider_stats = {
            'phase1': {
                'discovered': _db_spider_stats.get('Phase1Discovered', _db_spider_stats.get('phase1_discovered', 0)),
                'processed': _db_spider_stats.get('Phase1Processed', _db_spider_stats.get('phase1_processed', 0)),
                'skipped_history': _db_spider_stats.get('Phase1Skipped', _db_spider_stats.get('phase1_skipped', 0)),
                'no_new_torrents': _db_spider_stats.get('Phase1NoNew', _db_spider_stats.get('phase1_no_new', 0)),
                'failed': _db_spider_stats.get('Phase1Failed', _db_spider_stats.get('phase1_failed', 0)),
            },
            'phase2': {
                'discovered': _db_spider_stats.get('Phase2Discovered', _db_spider_stats.get('phase2_discovered', 0)),
                'processed': _db_spider_stats.get('Phase2Processed', _db_spider_stats.get('phase2_processed', 0)),
                'skipped_history': _db_spider_stats.get('Phase2Skipped', _db_spider_stats.get('phase2_skipped', 0)),
                'no_new_torrents': _db_spider_stats.get('Phase2NoNew', _db_spider_stats.get('phase2_no_new', 0)),
                'failed': _db_spider_stats.get('Phase2Failed', _db_spider_stats.get('phase2_failed', 0)),
            },
            'overall': {
                'total_discovered': _db_spider_stats.get('TotalDiscovered', _db_spider_stats.get('total_discovered', 0)),
                'successfully_processed': _db_spider_stats.get('TotalProcessed', _db_spider_stats.get('total_processed', 0)),
                'skipped_history': _db_spider_stats.get('TotalSkipped', _db_spider_stats.get('total_skipped', 0)),
                'no_new_torrents': _db_spider_stats.get('TotalNoNew', _db_spider_stats.get('total_no_new', 0)),
                'failed': _db_spider_stats.get('TotalFailed', _db_spider_stats.get('total_failed', 0)),
            },
        }
        import json as _json
        _fm_raw = _db_spider_stats.get('FailedMovies', '')
        if _fm_raw:
            try:
                spider_stats['failed_movies'] = _json.loads(_fm_raw)
            except (ValueError, TypeError):
                spider_stats['failed_movies'] = []
        else:
            spider_stats['failed_movies'] = []
        logger.info(f"Spider stats loaded from {_cur_be()} backend")
    else:
        spider_stats = extract_spider_statistics(SPIDER_LOG_FILE) if spider_log_exists else None

    if _db_uploader_stats:
        uploader_stats = {
            'total': _db_uploader_stats.get('TotalTorrents', _db_uploader_stats.get('total_torrents', 0)),
            'success': _db_uploader_stats.get('SuccessfullyAdded', _db_uploader_stats.get('successfully_added', 0)),
            'failed': _db_uploader_stats.get('FailedCount', _db_uploader_stats.get('failed_count', 0)),
            'hacked_sub': _db_uploader_stats.get('HackedSub', _db_uploader_stats.get('hacked_sub', 0)),
            'hacked_nosub': _db_uploader_stats.get('HackedNosub', _db_uploader_stats.get('hacked_nosub', 0)),
            'subtitle': _db_uploader_stats.get('SubtitleCount', _db_uploader_stats.get('subtitle_count', 0)),
            'no_subtitle': _db_uploader_stats.get('NoSubtitleCount', _db_uploader_stats.get('no_subtitle_count', 0)),
            'success_rate': _db_uploader_stats.get('SuccessRate', _db_uploader_stats.get('success_rate', 0.0)),
        }
        logger.info(f"Uploader stats loaded from {_cur_be()} backend")
    else:
        uploader_stats = extract_uploader_statistics(UPLOADER_LOG_FILE) if uploader_log_exists else None

    if _db_pikpak_stats:
        pikpak_stats = {
            'total_torrents': _db_pikpak_stats.get('TotalTorrents', _db_pikpak_stats.get('total_torrents', 0)),
            'filtered_old': _db_pikpak_stats.get('FilteredOld', _db_pikpak_stats.get('filtered_old', 0)),
            'added_to_pikpak': _db_pikpak_stats.get('UploadedCount', _db_pikpak_stats.get('uploaded_count', _db_pikpak_stats.get('SuccessfulCount', _db_pikpak_stats.get('successful_count', 0)))),
            'removed_from_qb': _db_pikpak_stats.get('SuccessfulCount', _db_pikpak_stats.get('successful_count', 0)),
            'failed': _db_pikpak_stats.get('FailedCount', _db_pikpak_stats.get('failed_count', 0)),
            'threshold_days': _db_pikpak_stats.get('ThresholdDays', _db_pikpak_stats.get('threshold_days', 3)),
        }
        logger.info(f"PikPak stats loaded from {_cur_be()} backend")
    else:
        pikpak_stats = extract_pikpak_statistics(PIKPAK_LOG_FILE) if pikpak_log_exists else None
    ban_summary = get_proxy_ban_summary()

    # Extract dedup statistics (only when dedup was enabled this session)
    dedup_csv_path = os.path.join(_EMAIL_REPORTS_DIR, 'dedup_history.csv')
    dedup_enabled = _is_dedup_enabled(SPIDER_LOG_FILE)
    dedup_stats = None
    if dedup_enabled:
        session_start_time = None
        if _sid is not None:
            try:
                from javdb.storage.db import get_db, REPORTS_DB_PATH
                with get_db(REPORTS_DB_PATH) as _conn:
                    _row = _conn.execute(
                        "SELECT DateTimeCreated FROM ReportSessions WHERE Id = ?", (_sid,)
                    ).fetchone()
                    if _row:
                        session_start_time = _row[0]
            except Exception as e:
                logger.debug(f"Could not fetch session start time: {e}")
        dedup_stats = extract_dedup_statistics(dedup_csv_path, session_start_time=session_start_time)
        if dedup_stats:
            logger.info(f"Dedup stats: detected={dedup_stats['detected']}, deleted={dedup_stats['deleted']}")
    else:
        logger.info("Dedup was not enabled this session — skipping dedup section")

    # Determine pipeline mode and CSV path
    mode = options.mode
    report_dt = get_report_display_datetime()
    today_str = report_dt.strftime('%Y%m%d')  # Subject date = workflow start when set (CI)

    # Determine CSV path (using dated subdirectory YYYY/MM)
    # Note: We use wildcard-based discovery (not date-specific) to handle cross-midnight
    # scenarios where spider runs before midnight but email notification runs after midnight
    if options.csv_path:
        csv_path = options.csv_path
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
    log_files = [SPIDER_LOG_FILE, UPLOADER_LOG_FILE, PIKPAK_LOG_FILE, PIPELINE_LOG_FILE, EMAIL_NOTIFICATION_LOG_FILE, DEDUP_LOG_FILE]

    for log_file in log_files:
        txt_path = convert_log_to_txt(log_file)
        if txt_path:
            txt_attachments.append(txt_path)

    # Find and include proxy ban HTML files (these are already .txt files)
    proxy_ban_html_files = find_proxy_ban_html_files('logs')
    proxy_ban_summary = extract_proxy_ban_summary(proxy_ban_html_files)

    # Add CSV if exists
    attachments = txt_attachments.copy()
    if csv_path and os.path.exists(csv_path):
        attachments.insert(0, csv_path)

    # Add dedup.csv if dedup was enabled and file exists
    if dedup_enabled and os.path.exists(dedup_csv_path):
        attachments.append(dedup_csv_path)

    # Add proxy ban HTML files to attachments (zip if > 3 files)
    proxy_ban_zip_path = None
    if proxy_ban_html_files:
        existing_ban_files = [f for f in proxy_ban_html_files if os.path.exists(f)]
        if len(existing_ban_files) > 3:
            proxy_ban_zip_path = os.path.join('logs', 'proxy_ban_html_files.zip')
            try:
                with zipfile.ZipFile(proxy_ban_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for html_file in existing_ban_files:
                        zf.write(html_file, os.path.basename(html_file))
                logger.info(f"Compressed {len(existing_ban_files)} proxy ban files into {proxy_ban_zip_path}")
                attachments.append(proxy_ban_zip_path)
            except Exception as e:
                logger.warning(f"Failed to create proxy ban zip file: {e} — falling back to individual attachments")
                if os.path.exists(proxy_ban_zip_path):
                    try:
                        os.remove(proxy_ban_zip_path)
                    except OSError:
                        pass
                proxy_ban_zip_path = None
                for html_file in existing_ban_files:
                    attachments.append(html_file)
        else:
            for html_file in existing_ban_files:
                attachments.append(html_file)

    # Prepare default stats for missing components
    default_spider_stats = {
        'phase1': {'discovered': 0, 'processed': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'phase2': {'discovered': 0, 'processed': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'overall': {'total_discovered': 0, 'successfully_processed': 0, 'skipped_history': 0, 'no_new_torrents': 0, 'failed': 0},
        'failed_movies': [],
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

    # End time when composing email (actual send may be moments later); match report_dt tz for display
    report_end_dt = (
        datetime.now(tz=report_dt.tzinfo) if report_dt.tzinfo is not None else datetime.now()
    )

    # Phase 2 / Phase 3 — load pending_session_verify records and the
    # 24h Health Snapshot so the email body and subject can surface
    # pending-mode anomalies.  Restricted to this run via $GITHUB_RUN_ID
    # so a long-lived d1_drift.jsonl doesn't bleed unrelated runs into
    # this report.
    pending_jsonl = _resolve_default_verify_jsonl(options.verify_jsonl)
    health_snapshot_path = _resolve_default_health_snapshot(
        options.health_snapshot,
    )
    run_id_filter = os.environ.get('GITHUB_RUN_ID')
    run_attempt_filter = os.environ.get('GITHUB_RUN_ATTEMPT')
    # Only load the shared verify JSONL when this run is scoped by
    # GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT; otherwise we would bleed
    # stale historical pending records into this report.
    if run_id_filter or run_attempt_filter:
        pending_verify_records = _load_pending_verify_records(
            pending_jsonl,
            run_id=run_id_filter,
            run_attempt=run_attempt_filter,
        )
    else:
        pending_verify_records = []
    pending_alerts, pending_has_critical = _evaluate_pending_alerts(
        pending_verify_records,
    )
    pending_prefix = _build_pending_subject_prefix(
        pending_verify_records, pending_alerts, pending_has_critical, mode,
    )

    # Send email based on status
    if not has_critical_errors:
        body = format_email_report(
            final_spider_stats, final_uploader_stats, final_pikpak_stats, ban_summary,
            show_spider=spider_log_exists,
            show_uploader=uploader_log_exists,
            show_pikpak=pikpak_log_exists,
            mode=mode,
            adhoc_info=adhoc_info,
            proxy_ban_html_summary=proxy_ban_summary,
            dedup_stats=dedup_stats,
            report_dt=report_dt,
            report_end_dt=report_end_dt,
            pending_verify_records=pending_verify_records,
            pending_alerts=pending_alerts,
            health_snapshot_path=health_snapshot_path,
        )
        subject = f'{pending_prefix}✓ SUCCESS - JavDB {mode_display} Report {today_str}{adhoc_subject_suffix}'
    else:
        error_details = "\n".join([f"  • {error}" for error in pipeline_errors])
        stats_report = format_email_report(
            final_spider_stats, final_uploader_stats, final_pikpak_stats, ban_summary,
            show_spider=spider_log_exists and not spider_critical,
            show_uploader=uploader_log_exists and not uploader_critical,
            show_pikpak=pikpak_log_exists and not pikpak_critical,
            mode=mode,
            adhoc_info=adhoc_info,
            proxy_ban_html_summary=proxy_ban_summary,
            dedup_stats=dedup_stats,
            report_dt=report_dt,
            report_end_dt=report_end_dt,
            pending_verify_records=pending_verify_records,
            pending_alerts=pending_alerts,
            health_snapshot_path=health_snapshot_path,
        )

        # Add mode info to failure report header
        mode_info_line = f"Mode: {mode_display}"
        if adhoc_info:
            mode_info_line += f"\nTarget: {adhoc_info}"

        body = f"""
═══════════════════════════════
⚠️  PIPELINE FAILED ({mode_display})  ⚠️
Started:  {report_dt.strftime('%Y-%m-%d %H:%M:%S')}
Finished: {report_end_dt.strftime('%Y-%m-%d %H:%M:%S')}
═══════════════════════════════

{mode_info_line}

🚨 CRITICAL ERRORS

{error_details}

Check attached logs for details.

{stats_report}
"""
        subject = f'{pending_prefix}✗ FAILED - JavDB {mode_display} Report {today_str}{adhoc_subject_suffix}'

    # P0-6: prepend a top-of-body banner when STORAGE_BACKEND=dual and
    # ``d1_drift.jsonl`` accumulated entries today. The body up to here
    # is sourced from SQLite-local (forced via ``db_get_*_local``); the
    # banner makes it visible to operators that D1 and SQLite may have
    # diverged, and points at the reconcile tool.
    #
    # Gated to dual mode only: in d1-only there is no SQLite-side write
    # path, so SQLite-vs-D1 drift cannot occur by construction. The
    # ``d1_drift.jsonl`` file is still appended by operational tooling
    # (``commit_session._emit_pending_verify``, sweep / cleanup metrics),
    # but those records are audit trails, not drift events — surfacing
    # them as a "DRIFT ADVISORY" in d1-only mode is misleading.
    try:
        from javdb.infra.config import cfg as _cfg
        backend = (
            os.environ.get('STORAGE_BACKEND')
            or _cfg('STORAGE_BACKEND', 'sqlite')
            or 'sqlite'
        ).strip().lower()
    except Exception:  # noqa: BLE001
        backend = 'sqlite'
    if backend == 'dual':
        reports_dir_for_advisory = os.environ.get('REPORTS_DIR', _EMAIL_REPORTS_DIR)
        advisory = _build_dual_drift_advisory(reports_dir_for_advisory)
        if advisory:
            # ADR-009 D6: run drift_diagnose subprocess and append results
            diagnosis_section, diag_suspects = _build_drift_diagnosis_section()
            if diagnosis_section:
                advisory = advisory + diagnosis_section
            body = advisory + body
            logger.warning(
                "Prepended D1 drift advisory to email body — see "
                "%s/D1/d1_drift.jsonl",
                reports_dir_for_advisory,
            )
            # ADR-009 D6: tag subject with drift verdict
            drift_prefix = _drift_diagnosis_subject_prefix(diag_suspects)
            subject = f'{drift_prefix}{subject}'

    ops_advisory = _build_ops_diagnosis_advisory(
        os.environ.get("OPS_DIAGNOSIS_JSON")
    )
    if ops_advisory:
        body = ops_advisory + body

    # Send email
    email_sent = send_email(subject, body, attachments, options.dry_run)

    # Clean up temporary txt files
    for txt_path in txt_attachments:
        if txt_path and os.path.exists(txt_path):
            try:
                os.remove(txt_path)
                logger.debug(f"Cleaned up temporary file: {txt_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up {txt_path}: {e}")

    # Clean up temporary proxy ban zip file
    if proxy_ban_zip_path and os.path.exists(proxy_ban_zip_path):
        try:
            os.remove(proxy_ban_zip_path)
            logger.debug(f"Cleaned up temporary file: {proxy_ban_zip_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up {proxy_ban_zip_path}: {e}")

    # Commit pipeline log (only if credentials are available)
    if not options.dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing pipeline log...")
        flush_log_handlers()

        files_to_commit = ['logs/']
        commit_message = f"Auto-commit: Pipeline notification {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        git_commit_and_push(
            files_to_add=files_to_commit,
            commit_message=commit_message,
            from_pipeline=options.from_pipeline,
            git_username=GIT_USERNAME,
            git_password=GIT_PASSWORD,
            git_repo_url=GIT_REPO_URL,
            git_branch=GIT_BRANCH
        )
    elif not options.dry_run:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")

    logger.info("=" * 60)
    logger.info("EMAIL NOTIFICATION COMPLETED")
    logger.info("=" * 60)

    # P0-5: exit non-zero when the SMTP send actually failed so the CI
    # job (and any operator watching the dashboard) sees the failure.
    # Previously the script always returned 0, so a stalled relay,
    # rejected credentials, or oversized body were silently masked and
    # the pipeline appeared "notified" when no email was ever delivered.
    #
    # ``email_sent`` is set by ``send_email()`` — True on a successful
    # ``server.send_message()``, False on any caught exception. In
    # ``--dry-run`` we deliberately skip SMTP, so the success-path
    # boolean is forced True there and this branch never fires.
    if not options.dry_run and not email_sent:
        logger.error(
            "Email send FAILED for subject=%r; exiting non-zero so the "
            "CI job surfaces the failure instead of marking the pipeline "
            "as notified.",
            subject,
        )

    return EmailNotificationResult(
        email_sent=email_sent,
        dry_run=options.dry_run,
        subject=subject,
    )


def main():
    args = parse_arguments()
    options = EmailNotificationOptions(
        csv_path=args.csv_path,
        mode=args.mode,
        dry_run=args.dry_run,
        from_pipeline=args.from_pipeline,
        session_id=args.session_id,
        verify_jsonl=args.verify_jsonl,
        health_snapshot=args.health_snapshot,
    )
    result = run_email_notification_from_options(options)
    sys.exit(result.exit_code)


if __name__ == '__main__':
    main()
