"""
JavDB Pipeline - Orchestration Service

This script orchestrates the entire pipeline by running individual scripts:
1. Spider - Scrape JavDB for new releases
2. qBittorrent Uploader - Upload torrents to qBittorrent
3. PikPak Bridge - Transfer old torrents to PikPak
4. Email Notification - Send email report

Each script handles its own git commits. When run through this pipeline,
scripts use GIT_USERNAME/GIT_PASSWORD from config.py for commits.
"""

import argparse
import logging
import os
import tempfile
import sys
from datetime import datetime
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
os.chdir(_REPO_ROOT)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import unified configuration
from javdb.infra.config import cfg
from javdb.pipeline.models import PipelineRunResult, StepPolicy, StepResult
from javdb.pipeline.result_io import (
    read_spider_result,
    utc_now_iso,
    write_pipeline_result_atomic,
)
from javdb.pipeline.step_runner import InProcessSpiderStepRunner, SubprocessStepRunner
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override
from javdb.spider.app.options import spider_options_from_args
from javdb.spider.app.run_service import run_spider

PIPELINE_LOG_FILE = cfg('PIPELINE_LOG_FILE', 'logs/pipeline.log')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
DAILY_REPORT_DIR = cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = cfg('AD_HOC_DIR', 'reports/AdHoc')
_REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
DEDUP_CSV = cfg('DEDUP_CSV', 'dedup.csv')

# Import path helper for dated subdirectories
from javdb.infra.paths import get_dated_report_path

# --- LOGGING SETUP ---
from javdb.infra.logging import setup_logging, get_logger
setup_logging(PIPELINE_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)


def check_rust_core_status():
    """Check and log the availability status of Rust core components."""
    status = {}
    
    # Check parsers
    try:
        from apps.api.parsers import RUST_PARSERS_AVAILABLE
        status['parsers'] = RUST_PARSERS_AVAILABLE
    except Exception:
        status['parsers'] = False
    
    # Check proxy pool
    try:
        from javdb.proxy.pool import RUST_PROXY_AVAILABLE
        status['proxy_pool'] = RUST_PROXY_AVAILABLE
    except Exception:
        status['proxy_pool'] = False
    
    # Check request handler
    try:
        from javdb.infra.request import RUST_REQUEST_HANDLER_AVAILABLE
        status['request_handler'] = RUST_REQUEST_HANDLER_AVAILABLE
    except Exception:
        status['request_handler'] = False
    
    # Check history manager
    try:
        from javdb.storage.history_manager import RUST_HISTORY_AVAILABLE
        status['history_manager'] = RUST_HISTORY_AVAILABLE
    except Exception:
        status['history_manager'] = False
    
    # Log summary
    logger.debug("=" * 60)
    logger.debug("RUST CORE STATUS CHECK")
    logger.debug("=" * 60)
    for component, available in status.items():
        icon = "✅" if available else "⚠️ "
        impl = "Rust" if available else "Python"
        logger.debug(f"{icon} {component.replace('_', ' ').title()}: {impl}")
    
    all_rust = all(status.values())
    if all_rust:
        logger.debug("=" * 60)
        logger.debug("🚀 All components using Rust - maximum performance!")
        logger.debug("=" * 60)
    else:
        rust_count = sum(status.values())
        total_count = len(status)
        logger.debug("=" * 60)
        logger.debug(f"📊 Rust components: {rust_count}/{total_count} available")
        logger.debug("=" * 60)
    
    return status


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
    parser.add_argument('--ignore-release-date', action='store_true', help='Ignore today/yesterday tags')
    add_proxy_arguments(
        parser,
        use_help='Force-enable proxy for all pipeline steps',
        no_help='Force-disable proxy for all pipeline steps',
    )
    parser.add_argument(
        '--always-bypass-time',
        type=int,
        nargs='?',
        const=0,
        default=None,
        help='Minutes to keep using CF bypass after fallback success (0 or no value = whole session)',
    )
    # PikPak Bridge arguments
    parser.add_argument('--pikpak-individual', action='store_true', help='Use individual mode for PikPak Bridge')
    # Dedup
    parser.add_argument('--enable-dedup', action='store_true', help='Enable rclone dedup detection and execution')
    # Re-download (洗版): enabled by default when running through pipeline; opt out with --no-redownload
    parser.add_argument(
        '--no-redownload',
        action='store_true',
        help='Disable torrent re-download (洗版); pipeline enables it by default',
    )
    parser.add_argument(
        '--redownload-threshold',
        type=float,
        default=None,
        help='Size increase threshold for re-download (spider default if omitted)',
    )
    parser.add_argument(
        '--result-json',
        type=str,
        default=None,
        help='Write a versioned PipelineRunResult JSON sidecar to this path.',
    )
    return parser.parse_args()


def _step_failed(step_result):
    return step_result.status in ("failed", "timed_out")


def _raise_for_required_step(step_result):
    if step_result.required and _step_failed(step_result):
        reason = step_result.failure_reason or step_result.status
        raise RuntimeError(f"Required step {step_result.name} failed: {reason}")


def _failed_step_from_exception(policy, command, error, *, result_path=None):
    now = utc_now_iso()
    return StepResult(
        name=policy.name,
        status="failed",
        required=policy.required,
        run_on_failure=policy.run_on_failure,
        command=list(command),
        started_at=now,
        finished_at=now,
        exit_code=None,
        failure_reason=str(error),
        result_path=result_path,
    )


def _write_pipeline_result_best_effort(
    *,
    args,
    steps,
    spider_result,
    started_at,
    status,
    exit_code,
    failure_reason,
):
    result_json = getattr(args, 'result_json', None)
    if not result_json:
        return

    try:
        write_pipeline_result_atomic(
            result_json,
            PipelineRunResult(
                status=status,
                mode="adhoc" if args.url else "daily",
                url=args.url,
                started_at=started_at,
                finished_at=utc_now_iso(),
                exit_code=exit_code,
                failure_reason=failure_reason,
                spider_result=asdict(spider_result) if spider_result is not None else None,
                steps=list(steps),
            ),
        )
    except Exception as result_error:
        logger.warning("Failed to write pipeline result JSON: %s", result_error)


def main():
    uploader_cmd = ['python3', '-u', '-m', 'apps.cli.qb.uploader']
    pikpak_cmd = ['python3', '-u', '-m', 'apps.cli.pikpak.bridge']
    rclone_cmd = ['python3', '-u', '-m', 'apps.cli.rclone.manager']
    email_cmd = ['python3', '-u', '-m', 'apps.cli.notify.email']

    args = parse_arguments()
    runner = SubprocessStepRunner()
    steps = []
    spider_result = None
    pipeline_started_at = utc_now_iso()
    proxy_override = resolve_proxy_override(args.use_proxy, args.no_proxy)
    if args.always_bypass_time is not None and args.always_bypass_time < 0:
        logger.error("--always-bypass-time must be >= 0")
        _write_pipeline_result_best_effort(
            args=args,
            steps=steps,
            spider_result=spider_result,
            started_at=pipeline_started_at,
            status="failed",
            exit_code=2,
            failure_reason="--always-bypass-time must be >= 0",
        )
        sys.exit(2)

    is_adhoc_mode = args.url is not None
    
    # For adhoc mode, let spider generate the filename dynamically
    # For daily mode or when output_file is specified, use a pre-determined filename
    if args.output_file:
        csv_filename = args.output_file
        spider_output_file = csv_filename
    elif not is_adhoc_mode:
        today_str = datetime.now().strftime('%Y%m%d')
        csv_filename = f'Javdb_TodayTitle_{today_str}.csv'
        spider_output_file = csv_filename
    else:
        # Adhoc mode without explicit output file: spider will generate the name
        csv_filename = None
        spider_output_file = None

    csv_path = None  # Will be set after spider runs if not pre-determined
    if csv_filename:
        if is_adhoc_mode:
            csv_path = get_dated_report_path(AD_HOC_DIR, csv_filename)
            logger.info(f"Ad hoc mode detected. Expected CSV: {csv_path}")
        else:
            csv_path = get_dated_report_path(DAILY_REPORT_DIR, csv_filename)
            logger.info(f"Daily mode. Expected CSV: {csv_path}")
    else:
        logger.info("Ad hoc mode: CSV filename will be determined by spider")

    enable_dedup = args.enable_dedup
    # Build base arguments for uploader (csv filename will be added after spider runs)
    uploader_args = ['--from-pipeline']  # Always pass --from-pipeline
    if is_adhoc_mode:
        uploader_args.extend(['--mode', 'adhoc'])
    else:
        uploader_args.extend(['--mode', 'daily'])
    if proxy_override is True:
        uploader_args.append('--use-proxy')
    elif proxy_override is False:
        uploader_args.append('--no-proxy')

    # Build arguments for pikpak bridge
    pikpak_args = ['--from-pipeline']  # Always pass --from-pipeline
    pikpak_args.extend(['--days', '3'])
    if args.dry_run:
        pikpak_args.append('--dry-run')
    if args.pikpak_individual:
        pikpak_args.append('--individual')
    if proxy_override is True:
        pikpak_args.append('--use-proxy')
    elif proxy_override is False:
        pikpak_args.append('--no-proxy')

    pipeline_success = False
    failure_reason = None
    
    try:
        # Check Rust core status at startup
        check_rust_core_status()
        
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
        with tempfile.TemporaryDirectory(prefix="pipeline-result-") as spider_result_dir:
            spider_result_path = Path(spider_result_dir) / "spider-result.json"
            spider_options = replace(
                spider_options_from_args(args),
                output_file=spider_output_file,
                use_proxy=proxy_override is True,
                no_proxy=proxy_override is False,
                enable_redownload=not args.no_redownload,
                result_json=str(spider_result_path),
                use_history=False,
                from_pipeline=True,
            )
            spider_step, spider_result = InProcessSpiderStepRunner(
                run_spider=run_spider,
            ).run(
                StepPolicy(name="spider", required=True, timeout_sec=3600),
                options=spider_options,
                command_label=[
                    "in-process",
                    "javdb.spider.app.run_service.run_spider",
                ],
            )
            steps.append(spider_step)
            if _step_failed(spider_step) and spider_result is None and spider_result_path.exists():
                try:
                    spider_result = read_spider_result(spider_result_path)
                except Exception as spider_result_error:
                    logger.warning("Could not read partial spider result JSON: %s", spider_result_error)
            _raise_for_required_step(spider_step)
        logger.info("✓ JavDB Spider completed successfully")
        
        csv_path = spider_result.csv_path or csv_path
        if csv_path:
            logger.info(f"Captured CSV path from spider result: {csv_path}")
        else:
            logger.warning("Spider result did not include a CSV path, uploader will use auto-discovery")

        session_id = spider_result.session_id
        if session_id:
            logger.info(f"Captured session ID from spider result: {session_id}")
            uploader_args.extend(['--session-id', str(session_id)])
            pikpak_args.extend(['--session-id', str(session_id)])

        # Add CSV path to uploader args
        if csv_path:
            uploader_args.extend(['--input-file', csv_path])

        # 2. Run Uploader
        logger.info("Step 2: Running qBittorrent Uploader...")
        uploader_step = runner.run(
            StepPolicy(name="qb_uploader", required=True, timeout_sec=3600),
            uploader_cmd + uploader_args,
        )
        steps.append(uploader_step)
        _raise_for_required_step(uploader_step)
        logger.info("✓ qBittorrent Uploader completed successfully")

        # 3. Run PikPak Bridge
        logger.info("Step 3: Running PikPak Bridge to clean up old torrents...")
        pikpak_step = runner.run(
            StepPolicy(name="pikpak_bridge", required=True, timeout_sec=3600),
            pikpak_cmd + pikpak_args,
        )
        steps.append(pikpak_step)
        _raise_for_required_step(pikpak_step)
        logger.info("✓ PikPak Bridge completed successfully")

        # 3.5 Run Rclone Dedup Executor (if enabled)
        if enable_dedup:
            logger.info("Step 3.5: Running Rclone Dedup Executor...")
            dedup_policy = StepPolicy(name="rclone_dedup", required=False, timeout_sec=3600)
            dedup_command = rclone_cmd + ['--execute']
            try:
                dedup_step = runner.run(dedup_policy, dedup_command)
            except Exception as dedup_error:
                dedup_step = _failed_step_from_exception(
                    dedup_policy,
                    dedup_command,
                    dedup_error,
                )
            steps.append(dedup_step)
            if _step_failed(dedup_step):
                logger.warning(
                    "Rclone Dedup Executor failed (non-fatal): %s",
                    dedup_step.failure_reason or dedup_step.status,
                )
            else:
                logger.info("✓ Rclone Dedup Executor completed successfully")

        # 4. Run Email Notification
        # Build email args after spider runs (csv_path is now known)
        email_args = ['--from-pipeline']
        if csv_path:
            email_args.extend(['--csv-path', csv_path])
        if args.dry_run:
            email_args.append('--dry-run')
        
        logger.info("Step 4: Sending email notification...")
        email_step = runner.run(
            StepPolicy(name="email_notification", required=True, timeout_sec=3600),
            email_cmd + email_args,
        )
        steps.append(email_step)
        _raise_for_required_step(email_step)
        logger.info("✓ Email notification sent successfully")

        pipeline_success = True
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)

    except Exception as e:
        failure_reason = str(e)
        logger.error("=" * 60)
        logger.error("PIPELINE EXECUTION ERROR")
        logger.error("=" * 60)
        logger.error(f'Error: {e}')
        pipeline_success = False
        
        # Still try to send email notification on failure
        email_args = ['--from-pipeline']
        if csv_path:
            email_args.extend(['--csv-path', csv_path])
        if args.dry_run:
            email_args.append('--dry-run')
        failure_email_command = email_cmd + email_args
        failure_email_policy = SimpleNamespace(
            name="email_notification_failure",
            required=False,
            run_on_failure=True,
            timeout_sec=3600,
        )
        try:
            logger.info("Attempting to send failure notification email...")
            # Keep the fallback above available if policy construction fails.
            failure_email_policy = StepPolicy(
                name="email_notification_failure",
                required=False,
                run_on_failure=True,
                timeout_sec=3600,
            )
            failure_email_step = runner.run(
                failure_email_policy,
                failure_email_command,
            )
            steps.append(failure_email_step)
            if _step_failed(failure_email_step):
                logger.error(
                    "Failed to send failure notification: %s",
                    failure_email_step.failure_reason or failure_email_step.status,
                )
        except Exception as email_error:
            failure_email_step = _failed_step_from_exception(
                failure_email_policy,
                failure_email_command,
                email_error,
            )
            steps.append(failure_email_step)
            logger.error(f"Failed to send failure notification: {email_error}")
    
    # Exit with appropriate code
    if pipeline_success:
        _write_pipeline_result_best_effort(
            args=args,
            steps=steps,
            spider_result=spider_result,
            started_at=pipeline_started_at,
            status="success",
            exit_code=0,
            failure_reason=None,
        )
        sys.exit(0)
    else:
        _write_pipeline_result_best_effort(
            args=args,
            steps=steps,
            spider_result=spider_result,
            started_at=pipeline_started_at,
            status="failed",
            exit_code=1,
            failure_reason=failure_reason,
        )
        sys.exit(1)


class PipelineRunService:
    """Application-service wrapper for the pipeline runtime."""

    def run(self):
        return main()


__all__ = ["PipelineRunService", "main"]


if __name__ == '__main__':
    raise SystemExit(PipelineRunService().run())
