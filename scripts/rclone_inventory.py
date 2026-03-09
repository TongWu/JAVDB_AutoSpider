#!/usr/bin/env python3
"""
RClone Inventory Script for JAVDB Collections

Scans a remote Google Drive folder structure and records all existing movies
into rclone_inventory.csv for use by the Spider dedup system.

The inventory CSV uses video_code as the primary key and records the
sensor/subtitle category, full rclone path, and folder size.

Usage:
    python3 scripts/rclone_inventory.py [--root-path "gdrive:/path"] [--years "2025,2026"] [--workers 4]

Output:
    - rclone_inventory.csv in REPORTS_DIR
"""

import os
import sys
import re
import csv
import base64
import argparse
import gc
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.config_helper import cfg
from utils.logging_config import setup_logging, get_logger

RCLONE_DRIVE_NAME = cfg('RCLONE_DRIVE_NAME', None)
RCLONE_ROOT_FOLDER = cfg('RCLONE_ROOT_FOLDER', None)
RCLONE_CONFIG_BASE64 = cfg('RCLONE_CONFIG_BASE64', None)
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
RCLONE_INVENTORY_CSV = cfg('RCLONE_INVENTORY_CSV', 'rclone_inventory.csv')
from scripts.rclone_dedup import (
    SensorCategory,
    SubtitleCategory,
    FolderInfo,
    FolderCache,
    parse_folder_name,
    get_year_folders,
    get_actor_folders,
    get_movie_folders,
    get_folder_stats,
    get_folder_stats_batch,
    check_rclone_installed,
    check_remote_exists,
    check_remote_folder_access,
    VIDEO_EXTENSIONS,
)

setup_logging()
logger = get_logger(__name__)

INVENTORY_FIELDNAMES = [
    'video_code',
    'sensor_category',
    'subtitle_category',
    'folder_path',
    'folder_size',
    'file_count',
    'scan_datetime',
]

VIDEO_CODE_PATTERN = re.compile(
    r'(?:^|[\s\[/\\])([A-Z]{2,10}-\d{2,8})(?:[\s\]./\\]|$)',
    re.IGNORECASE,
)


def setup_rclone_config_from_base64(config_base64: str) -> bool:
    """Decode a Base64 rclone config and write it to the standard location.

    Returns True on success.
    """
    if not config_base64:
        logger.error("RCLONE_CONFIG_BASE64 is empty")
        return False

    try:
        config_bytes = base64.b64decode(config_base64)
        config_dir = os.path.expanduser('~/.config/rclone')
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, 'rclone.conf')
        with open(config_path, 'wb') as f:
            f.write(config_bytes)
        os.chmod(config_path, 0o600)
        logger.info(f"rclone config written to {config_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to decode/write rclone config: {e}")
        return False


def parse_root_path(root_path: str) -> Tuple[str, str]:
    """Split 'remote:/path' into (remote_name, folder_path)."""
    if ':' not in root_path:
        raise ValueError(f"Invalid root path (missing ':'): {root_path}")
    remote_name, folder_path = root_path.split(':', 1)
    return remote_name.strip(), folder_path.strip().strip('/')


def extract_video_code_from_filename(filename: str) -> Optional[str]:
    """Try to extract a video code from a filename using regex."""
    match = VIDEO_CODE_PATTERN.search(filename)
    if match:
        return match.group(1).upper()
    return None


def scan_non_standard_folder_for_codes(
    remote_name: str,
    root_folder: str,
    year: str,
    actor: str,
    folder_name: str,
) -> List[FolderInfo]:
    """For folders that don't match the standard naming pattern, scan file
    names inside to try to extract video codes."""
    import subprocess
    import json as _json

    remote_path = f"{remote_name}:{root_folder}/{year}/{actor}/{folder_name}"
    try:
        result = subprocess.run(
            ['rclone', 'lsjson', remote_path, '--files-only'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return []

        files = _json.loads(result.stdout)
        found: List[FolderInfo] = []
        for file_info in files:
            fname = file_info.get('Name', '')
            _, ext = os.path.splitext(fname.lower())
            if ext not in VIDEO_EXTENSIONS:
                continue
            code = extract_video_code_from_filename(fname)
            if code:
                found.append(FolderInfo(
                    full_path=remote_path,
                    year=year,
                    actor=actor,
                    movie_code=code,
                    sensor_category='',
                    subtitle_category='',
                    folder_name=folder_name,
                ))
                break
        return found
    except Exception as e:
        logger.debug(f"Error scanning non-standard folder {remote_path}: {e}")
        return []


def scan_inventory(
    remote_name: str,
    root_folder: str,
    max_workers: int = 4,
    year_filter: Optional[List[str]] = None,
    flush_callback=None,
    flush_interval: int = 100,
) -> int:
    """Scan the full folder tree and return the total number of folders found.

    When *flush_callback* is provided, accumulated folders are handed off every
    *flush_interval* completed year/actor combinations so the caller can persist
    them incrementally and free memory.
    """
    logger.info(f"Scanning inventory from {remote_name}:{root_folder}...")

    years = get_year_folders(remote_name, root_folder)
    if not years:
        logger.warning("No year folders found")
        return 0

    if year_filter:
        years = [y for y in years if y in year_filter]
        logger.info(f"Year filter applied: {years}")
        if not years:
            return 0

    year_actor_map: Dict[str, List[str]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_year = {
            executor.submit(get_actor_folders, remote_name, root_folder, year): year
            for year in years
        }
        for future in as_completed(future_to_year):
            year = future_to_year[future]
            try:
                year_actor_map[year] = future.result()
            except Exception as e:
                logger.error(f"Error scanning year {year}: {e}")
                year_actor_map[year] = []

    pending_folders: List[FolderInfo] = []
    total_found = 0
    batch_tasks = [(y, a) for y, actors in year_actor_map.items() for a in actors]
    total = len(batch_tasks)
    completed = 0

    logger.info(f"Scanning movie folders for {total} year/actor combinations...")

    def _flush():
        nonlocal total_found
        if pending_folders:
            if flush_callback:
                flush_callback(pending_folders)
            total_found += len(pending_folders)
            pending_folders.clear()
            gc.collect()

    chunk_size = min(100, max_workers * 10)
    for i in range(0, len(batch_tasks), chunk_size):
        chunk = batch_tasks[i:i + chunk_size]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(get_movie_folders, remote_name, root_folder, y, a): (y, a)
                for y, a in chunk
            }
            for future in as_completed(future_to_path):
                year, actor = future_to_path[future]
                completed += 1
                try:
                    folders = future.result()
                    if folders:
                        pending_folders.extend(folders)
                    if completed % flush_interval == 0:
                        pct = completed / total * 100
                        logger.info(
                            f"Progress: {completed}/{total} ({pct:.1f}%) combinations, "
                            f"{total_found + len(pending_folders)} folders"
                        )
                        _flush()
                except Exception as e:
                    logger.error(f"Error scanning {year}/{actor}: {e}")

    _flush()
    logger.info(f"Scan complete: {total_found} movie folders found")
    return total_found


def write_inventory_csv(
    folders: List[FolderInfo],
    output_path: str,
    remote_name: str,
    root_folder: str,
) -> int:
    """Write the inventory CSV.  Returns number of records written."""
    scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    records_written = 0

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
        writer.writeheader()
        for folder in folders:
            folder_path = folder.full_path
            if not folder_path.startswith(f"{remote_name}:"):
                folder_path = f"{remote_name}:{root_folder}/{folder.year}/{folder.actor}/{folder.folder_name}"
            writer.writerow({
                'video_code': folder.movie_code,
                'sensor_category': folder.sensor_category,
                'subtitle_category': folder.subtitle_category,
                'folder_path': folder_path,
                'folder_size': folder.size,
                'file_count': folder.file_count,
                'scan_datetime': scan_time,
            })
            records_written += 1

    logger.info(f"Inventory CSV written: {output_path} ({records_written} records)")
    return records_written


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Scan rclone remote and generate inventory CSV',
    )
    parser.add_argument(
        '--root-path', type=str, default=None,
        help='Full rclone path (remote:/path). Defaults to config RCLONE_DRIVE_NAME:RCLONE_ROOT_FOLDER',
    )
    parser.add_argument(
        '--years', type=str, default=None,
        help='Comma-separated list of years to scan (e.g., "2025,2026")',
    )
    parser.add_argument(
        '--workers', type=int, default=4,
        help='Number of parallel workers (default: 4)',
    )
    parser.add_argument(
        '--output', type=str, default=None,
        help='Override output CSV path',
    )
    parser.add_argument(
        '--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    setup_logging(log_level=args.log_level)

    # Resolve rclone remote and folder
    if args.root_path:
        remote_name, root_folder = parse_root_path(args.root_path)
    else:
        if not RCLONE_DRIVE_NAME or not RCLONE_ROOT_FOLDER:
            logger.error("No --root-path provided and RCLONE_DRIVE_NAME/RCLONE_ROOT_FOLDER not in config")
            return 1
        remote_name = RCLONE_DRIVE_NAME
        root_folder = RCLONE_ROOT_FOLDER.strip('/')

    # Setup rclone config from Base64 if available
    if RCLONE_CONFIG_BASE64:
        if not setup_rclone_config_from_base64(RCLONE_CONFIG_BASE64):
            return 1
    else:
        logger.info("No RCLONE_CONFIG_BASE64 in config – assuming rclone is pre-configured")

    # Resolve output path
    if args.output:
        output_path = args.output
    else:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        output_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)

    year_filter = None
    if args.years:
        year_filter = [y.strip() for y in args.years.split(',') if y.strip()]

    logger.info("=" * 60)
    logger.info("RCLONE INVENTORY SCAN")
    logger.info(f"Remote: {remote_name}:{root_folder}")
    if year_filter:
        logger.info(f"Year filter: {year_filter}")
    logger.info(f"Workers: {args.workers}")
    logger.info(f"Output: {output_path}")
    logger.info("=" * 60)

    # Health checks
    ok, msg = check_rclone_installed()
    if not ok:
        logger.error(msg)
        return 1
    logger.info(f"  {msg}")

    ok, msg = check_remote_exists(remote_name)
    if not ok:
        logger.error(msg)
        return 1
    logger.info(f"  {msg}")

    ok, msg = check_remote_folder_access(remote_name, root_folder)
    if not ok:
        logger.error(msg)
        return 1
    logger.info(f"  {msg}")

    # Scan & write incrementally to reduce memory usage
    scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_written = 0

    with open(output_path, 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=INVENTORY_FIELDNAMES)
        writer.writeheader()

        def flush_batch(batch: List[FolderInfo]):
            nonlocal total_written
            get_folder_stats_batch(batch, max_workers=args.workers)
            for folder in batch:
                folder_path = folder.full_path
                if not folder_path.startswith(f"{remote_name}:"):
                    folder_path = (
                        f"{remote_name}:{root_folder}/{folder.year}/"
                        f"{folder.actor}/{folder.folder_name}"
                    )
                writer.writerow({
                    'video_code': folder.movie_code,
                    'sensor_category': folder.sensor_category,
                    'subtitle_category': folder.subtitle_category,
                    'folder_path': folder_path,
                    'folder_size': folder.size,
                    'file_count': folder.file_count,
                    'scan_datetime': scan_time,
                })
                total_written += 1
            csv_file.flush()

        total_found = scan_inventory(
            remote_name, root_folder,
            max_workers=args.workers,
            year_filter=year_filter,
            flush_callback=flush_batch,
        )

    if total_found == 0:
        logger.warning("No movie folders found")
        return 0

    logger.info("=" * 60)
    logger.info("INVENTORY SCAN COMPLETE")
    logger.info(f"Total movies recorded: {total_written}")
    logger.info(f"Output: {output_path}")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
