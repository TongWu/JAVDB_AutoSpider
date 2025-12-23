#!/usr/bin/env python3
"""
Migration Script: Move existing reports to dated subdirectories (YYYY/MM)

This script migrates existing CSV report files from the root of Daily Report
and Ad Hoc directories into dated subdirectories based on their filenames.

Usage:
    # Dry run (preview changes without moving files)
    python migration/migrate_reports_to_dated_dirs.py --dry-run
    
    # Execute migration
    python migration/migrate_reports_to_dated_dirs.py
    
    # Force migration even if target exists
    python migration/migrate_reports_to_dated_dirs.py --force

Files that will be migrated:
    - Daily Report/*.csv (except history files)
    - Ad Hoc/*.csv

Files that will NOT be migrated (stay at root level):
    - parsed_movies_history.csv
    - pikpak_bridge_history.csv
    - proxy_bans.csv
"""

import os
import re
import shutil
import argparse
from datetime import datetime
from typing import Optional, Tuple, List

# Files that should stay at root level (not migrated)
EXCLUDED_FILES = {
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
    # Pattern: Match YYYYMMDD at the end of filename (before .csv)
    patterns = [
        r'_(\d{4})(\d{2})\d{2}\.csv$',  # Standard: _YYYYMMDD.csv
        r'(\d{4})(\d{2})\d{2}\.csv$',    # Without underscore: YYYYMMDD.csv
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            year = match.group(1)
            month = match.group(2)
            # Validate year and month
            if 2020 <= int(year) <= 2099 and 1 <= int(month) <= 12:
                return (year, month)
    
    return None


def extract_date_from_file_mtime(filepath: str) -> Tuple[str, str]:
    """
    Extract year and month from file modification time.
    Used as fallback when date cannot be extracted from filename.
    
    Args:
        filepath: Full path to the file
    
    Returns:
        Tuple of (year, month)
    """
    mtime = os.path.getmtime(filepath)
    dt = datetime.fromtimestamp(mtime)
    return (dt.strftime('%Y'), dt.strftime('%m'))


def get_target_path(source_file: str, base_dir: str) -> Optional[str]:
    """
    Determine the target path for a file in dated subdirectory.
    
    Args:
        source_file: Full path to the source file
        base_dir: Base directory (Daily Report or Ad Hoc)
    
    Returns:
        Target path or None if file should not be migrated
    """
    filename = os.path.basename(source_file)
    
    # Skip excluded files
    if filename in EXCLUDED_FILES:
        return None
    
    # Skip non-CSV files
    if not filename.endswith('.csv'):
        return None
    
    # Try to extract date from filename first
    date_info = extract_date_from_filename(filename)
    
    if date_info is None:
        # Fallback to file modification time
        date_info = extract_date_from_file_mtime(source_file)
        print(f"  Note: Using file mtime for {filename} (no date in filename)")
    
    year, month = date_info
    
    # Construct target path
    target_dir = os.path.join(base_dir, year, month)
    target_path = os.path.join(target_dir, filename)
    
    return target_path


def find_files_to_migrate(base_dir: str) -> List[Tuple[str, str]]:
    """
    Find all files that need to be migrated in a directory.
    
    Args:
        base_dir: Base directory to scan
    
    Returns:
        List of tuples (source_path, target_path)
    """
    migrations = []
    
    if not os.path.exists(base_dir):
        return migrations
    
    # Only scan root level files (not subdirectories)
    for filename in os.listdir(base_dir):
        source_path = os.path.join(base_dir, filename)
        
        # Skip directories
        if os.path.isdir(source_path):
            continue
        
        # Get target path
        target_path = get_target_path(source_path, base_dir)
        
        if target_path:
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
    # Check if target already exists
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
    
    # Move the file
    try:
        shutil.move(source, target)
        print(f"  Moved: {os.path.basename(source)}")
        print(f"      -> {target}")
        return True
    except Exception as e:
        print(f"  ERROR: Failed to move {source}: {e}")
        return False


def run_migration(dry_run: bool = False, force: bool = False):
    """
    Run the migration for all report directories.
    
    Args:
        dry_run: If True, only print what would happen
        force: If True, overwrite existing files
    """
    print("=" * 60)
    print("Report Migration to Dated Subdirectories (YYYY/MM)")
    print("=" * 60)
    
    if dry_run:
        print("\n*** DRY RUN MODE - No files will be moved ***\n")
    
    # Define directories to migrate
    directories = ['Daily Report', 'Ad Hoc']
    
    total_found = 0
    total_migrated = 0
    total_skipped = 0
    total_errors = 0
    
    for base_dir in directories:
        print(f"\n--- Processing: {base_dir} ---")
        
        if not os.path.exists(base_dir):
            print(f"  Directory does not exist, skipping.")
            continue
        
        migrations = find_files_to_migrate(base_dir)
        
        if not migrations:
            print(f"  No files to migrate.")
            continue
        
        print(f"  Found {len(migrations)} file(s) to migrate:")
        total_found += len(migrations)
        
        for source, target in migrations:
            result = migrate_file(source, target, dry_run, force)
            if result:
                total_migrated += 1
            elif os.path.exists(target):
                total_skipped += 1
            else:
                total_errors += 1
    
    # Print summary
    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"  Total files found:    {total_found}")
    print(f"  Successfully moved:   {total_migrated}")
    print(f"  Skipped (exists):     {total_skipped}")
    print(f"  Errors:               {total_errors}")
    
    if dry_run:
        print("\n*** This was a dry run. Run without --dry-run to execute. ***")
    
    print("=" * 60)
    
    return total_errors == 0


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Migrate CSV reports to dated subdirectories (YYYY/MM)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Preview what would be migrated (recommended first step)
    python migration/migrate_reports_to_dated_dirs.py --dry-run
    
    # Execute the migration
    python migration/migrate_reports_to_dated_dirs.py
    
    # Force migration (overwrite existing files)
    python migration/migrate_reports_to_dated_dirs.py --force

Notes:
    - History files (parsed_movies_history.csv, etc.) are NOT migrated
    - Files are moved, not copied
    - Date is extracted from filename (YYYYMMDD pattern)
    - If no date found, file modification time is used
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

