#!/usr/bin/env python3
"""
Rclone Dedup Executor

Reads dedup.csv, skips already-deleted entries, executes rclone purge
for each remaining entry, and updates the is_deleted column.

Usage:
    python3 scripts/rclone_dedup_executor.py [--dry-run] [--dedup-csv path]
"""

import os
import sys
import argparse
import subprocess
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.config_helper import cfg
from utils.logging_config import setup_logging, get_logger
from scripts.rclone_inventory import setup_rclone_config_from_base64
from scripts.spider.dedup_checker import (
    DEDUP_FIELDNAMES,
    load_dedup_csv,
    save_dedup_csv,
)

RCLONE_CONFIG_BASE64 = cfg('RCLONE_CONFIG_BASE64', None)
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
DEDUP_CSV = cfg('DEDUP_CSV', 'dedup.csv')
DEDUP_LOG_FILE = cfg('DEDUP_LOG_FILE', 'logs/rclone_dedup.log')

setup_logging()
logger = get_logger(__name__)


def rclone_purge(folder_path: str, dry_run: bool = False) -> bool:
    """Execute ``rclone purge <folder_path>``.

    Returns True on success.
    """
    if dry_run:
        logger.info(f"[DRY-RUN] Would purge: {folder_path}")
        return True

    cmd = ['rclone', 'purge', folder_path]
    logger.info(f"Executing: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info(f"  ✓ Purged: {folder_path}")
            return True
        else:
            logger.error(f"  ✗ Failed to purge {folder_path}: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"  ✗ Timeout purging {folder_path}")
        return False
    except Exception as e:
        logger.error(f"  ✗ Error purging {folder_path}: {e}")
        return False


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Execute rclone dedup deletions')
    parser.add_argument('--dry-run', action='store_true', help='Simulate without deleting')
    parser.add_argument('--dedup-csv', type=str, default=None, help='Override dedup CSV path')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    setup_logging(log_level=args.log_level)

    # Setup rclone config from Base64
    if RCLONE_CONFIG_BASE64:
        if not setup_rclone_config_from_base64(RCLONE_CONFIG_BASE64):
            return 1
    else:
        logger.info("No RCLONE_CONFIG_BASE64 in config – assuming rclone is pre-configured")

    # Resolve dedup CSV path
    if args.dedup_csv:
        dedup_csv = args.dedup_csv
    else:
        dedup_csv = os.path.join(REPORTS_DIR, DEDUP_CSV)

    # Setup dedup log file
    os.makedirs(os.path.dirname(DEDUP_LOG_FILE) or '.', exist_ok=True)
    setup_logging(DEDUP_LOG_FILE, args.log_level)

    logger.info("=" * 60)
    logger.info("RCLONE DEDUP EXECUTOR")
    logger.info(f"Dedup CSV: {dedup_csv}")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info("=" * 60)

    rows = load_dedup_csv(dedup_csv)
    if not rows:
        logger.info("No dedup records found – nothing to do")
        return 0

    pending = [r for r in rows if r.get('is_deleted', 'False') != 'True']
    logger.info(f"Total records: {len(rows)}, pending deletion: {len(pending)}")

    if not pending:
        logger.info("All records already deleted – nothing to do")
        return 0

    success_count = 0
    fail_count = 0

    # Group pending rows by folder path so each unique path is purged only once.
    path_to_rows: dict[str, list[dict]] = {}
    for row in rows:
        if row.get('is_deleted', 'False') == 'True':
            continue
        folder_path = row.get('existing_gdrive_path', '')
        if not folder_path:
            logger.warning(f"Skipping record with empty path: {row.get('video_code', '?')}")
            fail_count += 1
            continue
        path_to_rows.setdefault(folder_path, []).append(row)

    for folder_path, dup_rows in path_to_rows.items():
        if len(dup_rows) > 1:
            logger.info(f"Path has {len(dup_rows)} pending rows, will purge once: {folder_path}")

        ok = rclone_purge(folder_path, dry_run=args.dry_run)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if ok:
            for r in dup_rows:
                r['is_deleted'] = 'True'
                r['delete_datetime'] = now_str
            success_count += 1
        else:
            fail_count += 1

    # Persist updated state
    if not args.dry_run:
        save_dedup_csv(dedup_csv, rows)
        logger.info(f"Updated dedup CSV: {dedup_csv}")

    unique_paths = success_count + fail_count
    logger.info("=" * 60)
    logger.info("DEDUP EXECUTOR COMPLETE")
    logger.info(f"Pending rows: {len(pending)}, unique paths: {unique_paths}")
    logger.info(f"Purged: {success_count}, failed: {fail_count}")
    logger.info("=" * 60)

    return 0 if fail_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
