#!/usr/bin/env python3
"""
Migration Script: Migrate reports to new directory structure

This script migrates existing CSV report files from the old directory structure
to the new consolidated reports directory structure.

Old Structure:
    Daily Report/
    ├── *.csv (report files)
    ├── YYYY/MM/*.csv (already migrated reports)
    ├── parsed_movies_history.csv
    ├── pikpak_bridge_history.csv
    └── proxy_bans.csv
    
    Ad Hoc/
    ├── *.csv (report files)
    └── YYYY/MM/*.csv (already migrated reports)

New Structure:
    reports/
    ├── DailyReport/YYYY/MM/*.csv  (daily report files)
    ├── AdHoc/YYYY/MM/*.csv        (ad hoc report files)
    ├── parsed_movies_history.csv  (history file at root)
    ├── pikpak_bridge_history.csv  (history file at root)
    └── proxy_bans.csv             (history file at root)

Usage:
    # Dry run (preview changes without moving files)
    python migration/migrate_reports_to_dated_dirs.py --dry-run
    
    # Execute migration
    python migration/migrate_reports_to_dated_dirs.py
    
    # Force migration even if target exists
    python migration/migrate_reports_to_dated_dirs.py --force
"""

import os
import re
import shutil
import argparse
from datetime import datetime
from typing import Optional, Tuple, List

# Old directory names
OLD_DAILY_REPORT_DIR = 'Daily Report'
OLD_AD_HOC_DIR = 'Ad Hoc'

# New directory structure
NEW_REPORTS_DIR = 'reports'
NEW_DAILY_REPORT_DIR = 'reports/DailyReport'
NEW_AD_HOC_DIR = 'reports/AdHoc'

# History files that should be moved to reports root
HISTORY_FILES = {
    'parsed_movies_history.csv',
    'pikpak_bridge_history.csv',
    'proxy_bans.csv',
}


def extract_date_from_filename(filename: str) -> Optional[Tuple[str, str]]:
    """
    Extract year and month from CSV filename.
    
    Supported filename patterns:
        - Javdb_TodayTitle_YYYYMMDD.csv
        - Javdb_AdHoc_*_YYYYMMDD.csv
        - Javdb_DailyReport_YYYYMMDD.csv
        - Any filename containing _YYYYMMDD pattern
    
    Args:
        filename: Name of the CSV file
    
    Returns:
        Tuple of (year, month) or None if date cannot be extracted
    """
    patterns = [
        r'_(\d{4})(\d{2})\d{2}\.csv$',  # Standard: _YYYYMMDD.csv
        r'(\d{4})(\d{2})\d{2}\.csv$',    # Without underscore: YYYYMMDD.csv
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            year = match.group(1)
            month = match.group(2)
            if 2020 <= int(year) <= 2099 and 1 <= int(month) <= 12:
                return (year, month)
    
    return None


def extract_date_from_file_mtime(filepath: str) -> Tuple[str, str]:
    """
    Extract year and month from file modification time.
    Used as fallback when date cannot be extracted from filename.
    """
    mtime = os.path.getmtime(filepath)
    dt = datetime.fromtimestamp(mtime)
    return (dt.strftime('%Y'), dt.strftime('%m'))


def find_files_to_migrate(old_dir: str, new_base_dir: str) -> List[Tuple[str, str]]:
    """
    Find all report files that need to be migrated.
    
    This function handles:
    1. Root-level CSV files → new_base_dir/YYYY/MM/
    2. Already dated files (YYYY/MM/*.csv) → new_base_dir/YYYY/MM/
    
    Args:
        old_dir: Old directory path (e.g., 'Daily Report')
        new_base_dir: New base directory (e.g., 'reports/DailyReport')
    
    Returns:
        List of tuples (source_path, target_path)
    """
    migrations = []
    
    if not os.path.exists(old_dir):
        return migrations
    
    for root, dirs, files in os.walk(old_dir):
        for filename in files:
            if not filename.endswith('.csv'):
                continue
            
            # Skip history files (they're handled separately)
            if filename in HISTORY_FILES:
                continue
            
            source_path = os.path.join(root, filename)
            
            # Determine if file is already in dated subdirectory
            rel_path = os.path.relpath(source_path, old_dir)
            path_parts = rel_path.split(os.sep)
            
            if len(path_parts) == 3:
                # Already in YYYY/MM/ subdirectory
                year, month = path_parts[0], path_parts[1]
            else:
                # Root level file - extract date from filename
                date_info = extract_date_from_filename(filename)
                if date_info is None:
                    date_info = extract_date_from_file_mtime(source_path)
                    print(f"  Note: Using file mtime for {filename} (no date in filename)")
                year, month = date_info
            
            # Construct target path in new structure
            target_path = os.path.join(new_base_dir, year, month, filename)
            migrations.append((source_path, target_path))
    
    return migrations


def find_history_files_to_migrate() -> List[Tuple[str, str]]:
    """
    Find history files that need to be moved to the new reports root.
    
    Returns:
        List of tuples (source_path, target_path)
    """
    migrations = []
    
    # Check old Daily Report directory for history files
    if os.path.exists(OLD_DAILY_REPORT_DIR):
        for filename in HISTORY_FILES:
            source_path = os.path.join(OLD_DAILY_REPORT_DIR, filename)
            if os.path.exists(source_path):
                target_path = os.path.join(NEW_REPORTS_DIR, filename)
                migrations.append((source_path, target_path))
    
    return migrations


def migrate_file(source: str, target: str, dry_run: bool = False, force: bool = False) -> bool:
    """
    Migrate a single file to its target location.
    
    Args:
        source: Source file path
        target: Target file path
        dry_run: If True, only print what would happen
        force: If True, overwrite existing files
    
    Returns:
        True if migration was successful (or would be in dry_run)
    """
    if os.path.exists(target) and not force:
        print(f"  SKIP: Target already exists: {target}")
        return False
    
    if dry_run:
        print(f"  [DRY RUN] Would move: {source}")
        print(f"            To: {target}")
        return True
    
    # Create target directory if needed
    target_dir = os.path.dirname(target)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        print(f"  Created directory: {target_dir}")
    
    try:
        shutil.move(source, target)
        print(f"  Moved: {os.path.basename(source)}")
        print(f"      -> {target}")
        return True
    except Exception as e:
        print(f"  ERROR: Failed to move {source}: {e}")
        return False


def cleanup_empty_dirs(directory: str, dry_run: bool = False):
    """Remove empty directories after migration."""
    if not os.path.exists(directory):
        return
    
    # Walk bottom-up to remove empty directories
    for root, dirs, files in os.walk(directory, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                if not os.listdir(dir_path):
                    if dry_run:
                        print(f"  [DRY RUN] Would remove empty directory: {dir_path}")
                    else:
                        os.rmdir(dir_path)
                        print(f"  Removed empty directory: {dir_path}")
            except Exception as e:
                print(f"  Warning: Could not remove directory {dir_path}: {e}")
    
    # Try to remove the root directory if empty
    try:
        if os.path.exists(directory) and not os.listdir(directory):
            if dry_run:
                print(f"  [DRY RUN] Would remove empty directory: {directory}")
            else:
                os.rmdir(directory)
                print(f"  Removed empty directory: {directory}")
    except Exception:
        pass


def run_migration(dry_run: bool = False, force: bool = False):
    """
    Run the migration for all directories.
    
    Args:
        dry_run: If True, only print what would happen
        force: If True, overwrite existing files
    """
    print("=" * 70)
    print("Migration: Old Directory Structure → New Reports Structure")
    print("=" * 70)
    print()
    print("Old Structure:")
    print("  Daily Report/  → reports/DailyReport/YYYY/MM/")
    print("  Ad Hoc/        → reports/AdHoc/YYYY/MM/")
    print("  History files  → reports/")
    print()
    
    if dry_run:
        print("*** DRY RUN MODE - No files will be moved ***\n")
    
    # Create new reports directory
    if not dry_run:
        os.makedirs(NEW_REPORTS_DIR, exist_ok=True)
        os.makedirs(NEW_DAILY_REPORT_DIR, exist_ok=True)
        os.makedirs(NEW_AD_HOC_DIR, exist_ok=True)
    
    total_found = 0
    total_migrated = 0
    total_skipped = 0
    total_errors = 0
    
    # Migrate Daily Report files
    print(f"--- Processing: {OLD_DAILY_REPORT_DIR} → {NEW_DAILY_REPORT_DIR} ---")
    migrations = find_files_to_migrate(OLD_DAILY_REPORT_DIR, NEW_DAILY_REPORT_DIR)
    if migrations:
        print(f"  Found {len(migrations)} report file(s) to migrate:")
        total_found += len(migrations)
        for source, target in migrations:
            result = migrate_file(source, target, dry_run, force)
            if result:
                total_migrated += 1
            elif os.path.exists(target):
                total_skipped += 1
            else:
                total_errors += 1
    else:
        print("  No report files to migrate.")
    
    # Migrate Ad Hoc files
    print(f"\n--- Processing: {OLD_AD_HOC_DIR} → {NEW_AD_HOC_DIR} ---")
    migrations = find_files_to_migrate(OLD_AD_HOC_DIR, NEW_AD_HOC_DIR)
    if migrations:
        print(f"  Found {len(migrations)} report file(s) to migrate:")
        total_found += len(migrations)
        for source, target in migrations:
            result = migrate_file(source, target, dry_run, force)
            if result:
                total_migrated += 1
            elif os.path.exists(target):
                total_skipped += 1
            else:
                total_errors += 1
    else:
        print("  No report files to migrate.")
    
    # Migrate history files
    print(f"\n--- Processing: History Files → {NEW_REPORTS_DIR} ---")
    migrations = find_history_files_to_migrate()
    if migrations:
        print(f"  Found {len(migrations)} history file(s) to migrate:")
        total_found += len(migrations)
        for source, target in migrations:
            result = migrate_file(source, target, dry_run, force)
            if result:
                total_migrated += 1
            elif os.path.exists(target):
                total_skipped += 1
            else:
                total_errors += 1
    else:
        print("  No history files to migrate.")
    
    # Cleanup empty directories
    print("\n--- Cleanup: Removing empty directories ---")
    cleanup_empty_dirs(OLD_DAILY_REPORT_DIR, dry_run)
    cleanup_empty_dirs(OLD_AD_HOC_DIR, dry_run)
    
    # Print summary
    print("\n" + "=" * 70)
    print("MIGRATION SUMMARY")
    print("=" * 70)
    print(f"  Total files found:    {total_found}")
    print(f"  Successfully moved:   {total_migrated}")
    print(f"  Skipped (exists):     {total_skipped}")
    print(f"  Errors:               {total_errors}")
    
    if dry_run:
        print("\n*** This was a dry run. Run without --dry-run to execute. ***")
    
    print("=" * 70)
    
    return total_errors == 0


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Migrate reports to new directory structure',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Preview what would be migrated (recommended first step)
    python migration/migrate_reports_to_dated_dirs.py --dry-run
    
    # Execute the migration
    python migration/migrate_reports_to_dated_dirs.py
    
    # Force migration (overwrite existing files)
    python migration/migrate_reports_to_dated_dirs.py --force

Migration Details:
    1. Daily Report/*.csv → reports/DailyReport/YYYY/MM/*.csv
    2. Daily Report/YYYY/MM/*.csv → reports/DailyReport/YYYY/MM/*.csv
    3. Ad Hoc/*.csv → reports/AdHoc/YYYY/MM/*.csv
    4. Ad Hoc/YYYY/MM/*.csv → reports/AdHoc/YYYY/MM/*.csv
    5. Daily Report/parsed_movies_history.csv → reports/parsed_movies_history.csv
    6. Daily Report/pikpak_bridge_history.csv → reports/pikpak_bridge_history.csv
    7. Daily Report/proxy_bans.csv → reports/proxy_bans.csv
        """
    )
    
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without moving files')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite existing files at target location')
    
    return parser.parse_args()


def main():
    # Change to project root directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    os.chdir(project_root)
    
    args = parse_arguments()
    
    success = run_migration(dry_run=args.dry_run, force=args.force)
    
    return 0 if success else 1


if __name__ == '__main__':
    exit(main())
