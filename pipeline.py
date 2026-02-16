"""
JavDB Pipeline - Orchestration Script

This script orchestrates the entire pipeline by running individual scripts:
1. Spider - Scrape JavDB for new releases
2. qBittorrent Uploader - Upload torrents to qBittorrent
3. PikPak Bridge - Transfer old torrents to PikPak
4. Email Notification - Send email report

Each script handles its own git commits. When run through this pipeline,
scripts use GIT_USERNAME/GIT_PASSWORD from config.py for commits.
"""

import subprocess
import logging
import os
import sys
import argparse
from datetime import datetime

# Change to script directory
os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# Import unified configuration
try:
    from config import (
        PIPELINE_LOG_FILE, LOG_LEVEL, DAILY_REPORT_DIR, AD_HOC_DIR
    )
except ImportError:
    PIPELINE_LOG_FILE = 'logs/pipeline.log'
    LOG_LEVEL = 'INFO'
    DAILY_REPORT_DIR = 'reports/DailyReport'
    AD_HOC_DIR = 'reports/AdHoc'

# Import path helper for dated subdirectories
from utils.path_helper import get_dated_report_path

# --- LOGGING SETUP ---
from utils.logging_config import setup_logging, get_logger
setup_logging(PIPELINE_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)


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
    parser.add_argument('--use-proxy', action='store_true', help='Enable proxy for all HTTP requests')
    # PikPak Bridge arguments
    parser.add_argument('--pikpak-individual', action='store_true', help='Use individual mode for PikPak Bridge')
    return parser.parse_args()


def run_script(script_path, args=None):
    """
    Run a Python script with arguments and stream output.
    
    Args:
        script_path: Path to the script to run
        args: List of arguments to pass to the script
    
    Returns:
        str: Captured output from the script
    
    Raises:
        RuntimeError: If the script fails with non-zero exit code
    """
    cmd = ['python3', script_path]
    if args:
        cmd += args
    logger.info(f'Running: {" ".join(cmd)}')

    # Get the file handler from the root logger to write subprocess output directly
    file_handler = None
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            file_handler = handler
            break

    # Run with real-time output
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    # Capture output and write to both console and log file
    output_lines = []
    if process.stdout:
        for line in iter(process.stdout.readline, ''):
            if line:
                line_stripped = line.rstrip()
                print(line_stripped)  # Print to console
                output_lines.append(line)
                # Write directly to log file (preserving original format from subprocess)
                if file_handler:
                    file_handler.stream.write(line)
                    file_handler.stream.flush()

        process.stdout.close()

    return_code = process.wait()

    if return_code != 0:
        logger.error(f'Script {script_path} failed with return code {return_code}')
        raise RuntimeError(f'Script {script_path} failed with return code {return_code}')
    
    return ''.join(output_lines)


def extract_csv_path_from_output(output):
    """
    Extract CSV full path from spider output.
    
    Looks for a line in format: SPIDER_OUTPUT_CSV=/path/to/file.csv
    
    Args:
        output: The captured stdout from spider script
    
    Returns:
        str or None: The extracted CSV full path, or None if not found
    """
    for line in output.splitlines():
        if line.startswith('SPIDER_OUTPUT_CSV='):
            return line.split('=', 1)[1].strip()
    return None


def main():
    args = parse_arguments()
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

    # Build arguments for spider
    spider_args = ['--from-pipeline']  # Always pass --from-pipeline
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
    if spider_output_file:
        spider_args.extend(['--output-file', spider_output_file])
    if args.dry_run:
        spider_args.append('--dry-run')
    if args.ignore_release_date:
        spider_args.append('--ignore-release-date')
    if args.use_proxy:
        spider_args.append('--use-proxy')
    # Build base arguments for uploader (csv filename will be added after spider runs)
    uploader_args = ['--from-pipeline']  # Always pass --from-pipeline
    if is_adhoc_mode:
        uploader_args.extend(['--mode', 'adhoc'])
    else:
        uploader_args.extend(['--mode', 'daily'])
    if args.use_proxy:
        uploader_args.append('--use-proxy')

    # Build arguments for pikpak bridge
    pikpak_args = ['--from-pipeline']  # Always pass --from-pipeline
    pikpak_args.extend(['--days', '3'])
    if args.dry_run:
        pikpak_args.append('--dry-run')
    if args.pikpak_individual:
        pikpak_args.append('--individual')
    if args.use_proxy:
        pikpak_args.append('--use-proxy')

    pipeline_success = False
    
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
        spider_output = run_script('scripts/spider.py', spider_args)
        logger.info("✓ JavDB Spider completed successfully")
        
        # Extract CSV path from spider output if not pre-determined
        if csv_path is None:
            csv_path = extract_csv_path_from_output(spider_output)
            if csv_path:
                logger.info(f"Captured CSV path from spider: {csv_path}")
            else:
                logger.warning("Could not extract CSV path from spider output, uploader will use auto-discovery")
        
        # Add CSV path to uploader args
        if csv_path:
            uploader_args.extend(['--input-file', csv_path])

        # 2. Run Uploader
        logger.info("Step 2: Running qBittorrent Uploader...")
        run_script('scripts/qb_uploader.py', uploader_args)
        logger.info("✓ qBittorrent Uploader completed successfully")

        # 3. Run PikPak Bridge
        logger.info("Step 3: Running PikPak Bridge to clean up old torrents...")
        run_script('scripts/pikpak_bridge.py', pikpak_args)
        logger.info("✓ PikPak Bridge completed successfully")

        # 4. Run Email Notification
        # Build email args after spider runs (csv_path is now known)
        email_args = ['--from-pipeline']
        if csv_path:
            email_args.extend(['--csv-path', csv_path])
        if args.dry_run:
            email_args.append('--dry-run')
        
        logger.info("Step 4: Sending email notification...")
        run_script('scripts/email_notification.py', email_args)
        logger.info("✓ Email notification sent successfully")

        pipeline_success = True
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)

    except Exception as e:
        logger.error("=" * 60)
        logger.error("PIPELINE EXECUTION ERROR")
        logger.error("=" * 60)
        logger.error(f'Error: {e}')
        pipeline_success = False
        
        # Still try to send email notification on failure
        try:
            logger.info("Attempting to send failure notification email...")
            # Build email args for failure notification
            email_args = ['--from-pipeline']
            if csv_path:
                email_args.extend(['--csv-path', csv_path])
            if args.dry_run:
                email_args.append('--dry-run')
            run_script('scripts/email_notification.py', email_args)
        except Exception as email_error:
            logger.error(f"Failed to send failure notification: {email_error}")
    
    # Exit with appropriate code
    if pipeline_success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
