"""
Path Helper - Utility functions for generating dated subdirectory paths

This module provides functions to generate paths with YYYY/MM subdirectories
for organizing Daily Report and Ad Hoc report files.

Directory Structure:
    reports/
    ├── DailyReport/YYYY/MM/    # Daily report CSV files
    ├── AdHoc/YYYY/MM/          # Ad hoc report CSV files
    ├── parsed_movies_history.csv
    ├── pikpak_bridge_history.csv
    └── proxy_bans.csv

Usage:
    from utils.path_helper import get_dated_report_path, get_history_file_path
    
    # Get path for today's report
    csv_path = get_dated_report_path('reports/DailyReport', 'report.csv')
    # Returns: 'reports/DailyReport/2025/12/report.csv'
    
    # Get path for a specific date
    csv_path = get_dated_report_path('reports/AdHoc', 'report.csv', datetime(2025, 6, 15))
    # Returns: 'reports/AdHoc/2025/06/report.csv'
    
    # Get history file path
    history_path = get_history_file_path('reports', 'parsed_movies_history.csv')
    # Returns: 'reports/parsed_movies_history.csv'
"""

import os
from datetime import datetime
from typing import Optional


def get_history_file_path(reports_dir: str, filename: str) -> str:
    """
    Get the full path for a history file in the reports directory.
    
    History files are stored directly in the reports root directory,
    not in dated subdirectories.
    
    Args:
        reports_dir: Reports root directory (e.g., 'reports')
        filename: History file name (e.g., 'parsed_movies_history.csv')
    
    Returns:
        Full path to the history file (e.g., 'reports/parsed_movies_history.csv')
    """
    return os.path.join(reports_dir, filename)


def ensure_reports_dir(reports_dir: str) -> str:
    """
    Ensure the reports root directory exists.
    
    Args:
        reports_dir: Reports root directory (e.g., 'reports')
    
    Returns:
        Path to the created/existing reports directory
    """
    if not os.path.exists(reports_dir):
        os.makedirs(reports_dir)
    return reports_dir


def get_dated_subdir(base_dir: str, date: Optional[datetime] = None) -> str:
    """
    Generate a dated subdirectory path with YYYY/MM format.
    
    Args:
        base_dir: Base directory (e.g., 'Daily Report' or 'Ad Hoc')
        date: Date to use for subdirectory. Defaults to current date if None.
    
    Returns:
        Path with YYYY/MM subdirectory (e.g., 'Daily Report/2025/12')
    """
    if date is None:
        date = datetime.now()
    
    year = date.strftime('%Y')
    month = date.strftime('%m')
    
    return os.path.join(base_dir, year, month)


def get_dated_report_path(base_dir: str, filename: str, date: Optional[datetime] = None) -> str:
    """
    Generate full path for a report file in a dated subdirectory.
    
    Args:
        base_dir: Base directory (e.g., 'Daily Report' or 'Ad Hoc')
        filename: Name of the file (e.g., 'Javdb_TodayTitle_20251223.csv')
        date: Date to use for subdirectory. Defaults to current date if None.
    
    Returns:
        Full path with YYYY/MM subdirectory (e.g., 'Daily Report/2025/12/Javdb_TodayTitle_20251223.csv')
    """
    subdir = get_dated_subdir(base_dir, date)
    return os.path.join(subdir, filename)


def ensure_dated_dir(base_dir: str, date: Optional[datetime] = None) -> str:
    """
    Ensure the dated subdirectory exists and return its path.
    
    Args:
        base_dir: Base directory (e.g., 'Daily Report' or 'Ad Hoc')
        date: Date to use for subdirectory. Defaults to current date if None.
    
    Returns:
        Path to the created/existing dated subdirectory
    """
    subdir = get_dated_subdir(base_dir, date)
    
    if not os.path.exists(subdir):
        os.makedirs(subdir)
    
    return subdir


def find_latest_report_in_dated_dirs(base_dir: str, pattern: str) -> Optional[str]:
    """
    Find the most recent file matching a pattern in dated subdirectories.
    Searches in reverse chronological order (newest first).
    
    Args:
        base_dir: Base directory to search (e.g., 'Daily Report')
        pattern: Filename pattern (e.g., 'Javdb_TodayTitle_*.csv')
    
    Returns:
        Path to the most recent matching file, or None if not found
    """
    import glob
    
    if not os.path.exists(base_dir):
        return None
    
    # First, try to find in the current month's directory
    current_subdir = get_dated_subdir(base_dir)
    current_pattern = os.path.join(current_subdir, pattern)
    matches = glob.glob(current_pattern)
    
    if matches:
        # Return the most recent file
        return max(matches, key=os.path.getmtime)
    
    # If not found in current month, search all dated subdirectories
    # Pattern: base_dir/YYYY/MM/pattern
    all_pattern = os.path.join(base_dir, '*', '*', pattern)
    all_matches = glob.glob(all_pattern)
    
    if all_matches:
        return max(all_matches, key=os.path.getmtime)
    
    # Fallback: also check base directory (for backwards compatibility)
    legacy_pattern = os.path.join(base_dir, pattern)
    legacy_matches = glob.glob(legacy_pattern)
    
    if legacy_matches:
        return max(legacy_matches, key=os.path.getmtime)
    
    return None

