#!/usr/bin/env python3
"""
RClone Deduplication Script for JAVDB Collections

This script scans a remote Google Drive folder structure and removes duplicate
movie folders based on specific deduplication rules.

Folder Structure:
    {ROOT_FOLDER}/{YYYY}/{Actor Name}/{MOVIE_CODE} [{SENSOR-CATEGORY}-{SUBTITLE-CATEGORY}]
    
    Where:
    - YYYY: Year number (e.g., 2001, 2026) or 未知
    - SENSOR-CATEGORY: 有码, 无码, 无码流出, 无码破解
    - SUBTITLE-CATEGORY: 中字, 无字

Deduplication Rules:
    1. For 无码 category (includes 无码, 无码流出, 无码破解):
       Priority: 无码流出 > 无码 > 无码破解
       Keep the highest priority, delete lower ones
    2. For both 有码 and 无码: If 中字 version exists, delete 无字 version
       Exception: If 无字 folder size is 30% larger than 中字, skip deletion

Usage:
    python3 scripts/rclone_dedup.py <remote_name> <root_folder> [--dry-run] [--workers N]

Examples:
    python3 scripts/rclone_dedup.py gdrive Movies --dry-run
    python3 scripts/rclone_dedup.py "My Drive" "JAVDB Collection" --workers 8

Output:
    - CSV report in reports/Dedup/YYYY/MM/
    - Summary statistics in console

Exit codes:
    0: Success
    1: Health check failed or execution error
"""

import os
import sys
import re
import csv
import json
import gc
import argparse
import subprocess
import logging
import tempfile
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, NamedTuple, Iterator, Generator
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from functools import lru_cache

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger
from utils.path_helper import ensure_dated_dir, get_dated_report_path

# Setup logging
setup_logging()
logger = get_logger(__name__)


# ============================================================================
# Data Classes and Type Definitions
# ============================================================================

class SensorCategory:
    """Sensor category constants with priority order"""
    YOUMA = "有码"
    WUMA = "无码"
    WUMA_LIUCHU = "无码流出"
    WUMA_POJIE = "无码破解"
    
    # Priority map for 无码 categories (higher value = higher priority)
    WUMA_PRIORITY = {
        WUMA_LIUCHU: 3,
        WUMA: 2,
        WUMA_POJIE: 1,
    }
    
    @classmethod
    def is_wuma_category(cls, category: str) -> bool:
        """Check if category belongs to 无码 family"""
        return category in cls.WUMA_PRIORITY
    
    @classmethod
    def get_priority(cls, category: str) -> int:
        """Get priority for 无码 category (0 for non-无码)"""
        return cls.WUMA_PRIORITY.get(category, 0)


class SubtitleCategory:
    """Subtitle category constants"""
    ZHONGZI = "中字"
    WUZI = "无字"


# Size threshold: Skip deletion if 无字 is 30% larger than 中字
SIZE_THRESHOLD_RATIO = 1.30


# Batch size for memory-efficient processing
BATCH_SIZE = 1000

# Dry-run limits
DRY_RUN_MAX_YEARS = 2
DRY_RUN_MAX_ACTORS_PER_YEAR = 50
DRY_RUN_MAX_COMBINATIONS = 100

# Incremental mode: only process folders with video files modified within this many days
INCREMENTAL_DAYS = 30

# Video file extensions for incremental mode
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.wmv', '.mov', '.flv', '.webm', '.m4v', '.ts', '.iso'}


@dataclass
class FolderInfo:
    """Information about a movie folder"""
    full_path: str  # Full rclone path: remote:root/year/actor/folder_name
    year: str
    actor: str
    movie_code: str
    sensor_category: str
    subtitle_category: str
    folder_name: str  # Just the folder name without path
    size: int = 0  # Folder size in bytes
    file_count: int = 0  # Number of files
    video_mod_time: Optional[datetime] = None  # Modification time of video file


@dataclass
class DeletionRecord:
    """Record of a folder to be deleted"""
    movie_code: str
    sensor_category: str
    subtitle_category: str
    deletion_reason: str
    size: int
    file_count: int
    full_path: str
    kept_folder_path: str = ""  # Path of the folder that was kept (same movie code)


@dataclass
class DedupResult:
    """Result of deduplication analysis for a movie code"""
    movie_code: str
    year: str
    actor: str
    folders_to_keep: List[FolderInfo] = field(default_factory=list)
    folders_to_delete: List[Tuple[FolderInfo, str]] = field(default_factory=list)  # (folder, reason)


# ============================================================================
# Cache Manager for Memory Optimization
# ============================================================================

class FolderCache:
    """
    Disk-based cache manager for large folder structures.
    
    For datasets with 50000+ movies, keeping everything in memory is not feasible.
    This cache manager uses temporary files to store folder data and only loads
    batches when needed.
    """
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the cache manager.
        
        Args:
            cache_dir: Directory for cache files. Uses temp dir if None.
        """
        self._cache_dir = cache_dir or tempfile.mkdtemp(prefix='rclone_dedup_')
        self._index_file = os.path.join(self._cache_dir, 'index.json')
        self._folder_count = 0
        self._year_actor_index: Dict[str, str] = {}  # key -> cache file path
        logger.debug(f"Cache initialized at: {self._cache_dir}")
    
    def add_folders(self, year: str, actor: str, folders: List[FolderInfo]) -> None:
        """
        Add folders to the cache.
        
        Args:
            year: Year folder name
            actor: Actor folder name
            folders: List of folder info objects
        """
        if not folders:
            return
        
        key = f"{year}/{actor}"
        cache_file = os.path.join(self._cache_dir, f"{hash(key)}.pkl")
        
        # Serialize to disk
        with open(cache_file, 'wb') as f:
            pickle.dump(folders, f, protocol=pickle.HIGHEST_PROTOCOL)
        
        self._year_actor_index[key] = cache_file
        self._folder_count += len(folders)
    
    def get_folders(self, year: str, actor: str) -> List[FolderInfo]:
        """
        Get folders from cache.
        
        Args:
            year: Year folder name
            actor: Actor folder name
        
        Returns:
            List[FolderInfo]: Cached folders or empty list
        """
        key = f"{year}/{actor}"
        cache_file = self._year_actor_index.get(key)
        
        if not cache_file or not os.path.exists(cache_file):
            return []
        
        with open(cache_file, 'rb') as f:
            return pickle.load(f)
    
    def iter_all_folders(self, batch_size: int = BATCH_SIZE) -> Generator[List[FolderInfo], None, None]:
        """
        Iterate over all cached folders in batches.
        
        Args:
            batch_size: Number of folders per batch
        
        Yields:
            List[FolderInfo]: Batch of folders
        """
        batch: List[FolderInfo] = []
        
        for key, cache_file in self._year_actor_index.items():
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    folders = pickle.load(f)
                    batch.extend(folders)
                    
                    while len(batch) >= batch_size:
                        yield batch[:batch_size]
                        batch = batch[batch_size:]
        
        if batch:
            yield batch
    
    def get_all_as_dict(self) -> Dict[str, Dict[str, List[FolderInfo]]]:
        """
        Get all folders as nested dictionary (for backward compatibility).
        Warning: May use significant memory for large datasets.
        
        Returns:
            Dict[str, Dict[str, List[FolderInfo]]]: year -> actor -> folders
        """
        result: Dict[str, Dict[str, List[FolderInfo]]] = defaultdict(lambda: defaultdict(list))
        
        for key, cache_file in self._year_actor_index.items():
            year, actor = key.split('/', 1)
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    result[year][actor] = pickle.load(f)
        
        return dict(result)
    
    @property
    def folder_count(self) -> int:
        """Total number of folders in cache."""
        return self._folder_count
    
    def clear(self) -> None:
        """Clear all cached data and remove cache files."""
        import shutil
        try:
            shutil.rmtree(self._cache_dir)
            logger.debug(f"Cache cleared: {self._cache_dir}")
        except Exception as e:
            logger.warning(f"Could not clear cache: {e}")
        
        self._year_actor_index.clear()
        self._folder_count = 0
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.clear()


# ============================================================================
# Health Check Functions
# ============================================================================

def check_rclone_installed() -> Tuple[bool, str]:
    """
    Check if rclone is installed and callable.
    
    Returns:
        Tuple[bool, str]: (success, message)
    
    Raises:
        None - all exceptions are caught and returned as failure messages
    """
    try:
        result = subprocess.run(
            ['rclone', 'version'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            version_line = result.stdout.strip().split('\n')[0]
            return True, f"rclone installed: {version_line}"
        else:
            return False, f"rclone command failed: {result.stderr}"
    except FileNotFoundError:
        return False, "rclone is not installed or not in PATH"
    except subprocess.TimeoutExpired:
        return False, "rclone version check timed out"
    except Exception as e:
        return False, f"Error checking rclone: {str(e)}"


def check_remote_exists(remote_name: str) -> Tuple[bool, str]:
    """
    Check if the remote drive exists in rclone config.
    
    Args:
        remote_name: Name of the rclone remote
    
    Returns:
        Tuple[bool, str]: (success, message)
    """
    try:
        result = subprocess.run(
            ['rclone', 'listremotes'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to list remotes: {result.stderr}"
        
        remotes = [r.strip().rstrip(':') for r in result.stdout.strip().split('\n') if r.strip()]
        
        if remote_name in remotes:
            return True, f"Remote '{remote_name}' found in rclone config"
        else:
            available = ', '.join(remotes) if remotes else 'none'
            return False, f"Remote '{remote_name}' not found. Available: {available}"
    except subprocess.TimeoutExpired:
        return False, "Timeout checking remote config"
    except Exception as e:
        return False, f"Error checking remote: {str(e)}"


def check_remote_folder_access(remote_name: str, root_folder: str) -> Tuple[bool, str]:
    """
    Check if the remote folder is readable and writable.
    
    Args:
        remote_name: Name of the rclone remote
        root_folder: Root folder path on the remote
    
    Returns:
        Tuple[bool, str]: (success, message)
    """
    remote_path = f"{remote_name}:{root_folder}"
    
    try:
        # Check read access by listing the folder
        result = subprocess.run(
            ['rclone', 'lsd', remote_path, '--max-depth', '1'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return False, f"Folder '{root_folder}' not found on remote '{remote_name}'"
            return False, f"Cannot read folder: {result.stderr}"
        
        # Check write access by attempting to create and delete a test file
        # Note: This is a non-destructive test using rclone touch
        test_file = f"{remote_path}/.rclone_dedup_test_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        touch_result = subprocess.run(
            ['rclone', 'touch', test_file],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if touch_result.returncode == 0:
            # Clean up test file
            subprocess.run(
                ['rclone', 'deletefile', test_file],
                capture_output=True,
                timeout=30
            )
            return True, f"Remote folder '{remote_path}' is readable and writable"
        else:
            return False, f"Remote folder is readable but not writable: {touch_result.stderr}"
            
    except subprocess.TimeoutExpired:
        return False, f"Timeout accessing remote folder '{remote_path}'"
    except Exception as e:
        return False, f"Error checking folder access: {str(e)}"


def run_health_checks(remote_name: str, root_folder: str) -> bool:
    """
    Run all health checks before proceeding.
    
    Args:
        remote_name: Name of the rclone remote
        root_folder: Root folder path on the remote
    
    Returns:
        bool: True if all checks passed, False otherwise
    """
    logger.info("=" * 60)
    logger.info("HEALTH CHECK - Pre-flight Verification")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    checks = [
        ("rclone Installation", lambda: check_rclone_installed()),
        ("Remote Config", lambda: check_remote_exists(remote_name)),
        ("Folder Access", lambda: check_remote_folder_access(remote_name, root_folder)),
    ]
    
    all_passed = True
    results = []
    
    for i, (name, check_func) in enumerate(checks, 1):
        logger.info(f"[{i}/{len(checks)}] Checking {name}...")
        success, message = check_func()
        results.append((name, success, message))
        
        if success:
            logger.info(f"  ✓ {message}")
        else:
            logger.error(f"  ✗ {message}")
            all_passed = False
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("HEALTH CHECK SUMMARY")
    logger.info("=" * 60)
    
    for name, success, message in results:
        status = "✓ PASS" if success else "✗ FAIL"
        logger.info(f"  {status}: {name}")
    
    logger.info("")
    if all_passed:
        logger.info("✓ All health checks PASSED")
    else:
        logger.error("✗ Health checks FAILED - cannot proceed")
    logger.info("=" * 60)
    
    return all_passed


# ============================================================================
# Folder Structure Parsing Functions
# ============================================================================

def parse_folder_name(folder_name: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse a movie folder name to extract movie code, sensor category, and subtitle category.
    
    Expected format: "{MOVIE_CODE} [{SENSOR-CATEGORY}-{SUBTITLE-CATEGORY}]"
    Examples:
        - "ABC-123 [有码-中字]"
        - "XYZ-456 [无码流出-无字]"
    
    Args:
        folder_name: The folder name to parse
    
    Returns:
        Optional[Tuple[str, str, str]]: (movie_code, sensor_category, subtitle_category)
                                        or None if parsing fails
    """
    # Pattern: MOVIE_CODE [SENSOR-SUBTITLE]
    pattern = r'^(.+?)\s*\[(.+?)-(.+?)\]$'
    match = re.match(pattern, folder_name.strip())
    
    if not match:
        return None
    
    movie_code = match.group(1).strip()
    sensor_category = match.group(2).strip()
    subtitle_category = match.group(3).strip()
    
    # Validate sensor category
    valid_sensors = [SensorCategory.YOUMA, SensorCategory.WUMA, 
                     SensorCategory.WUMA_LIUCHU, SensorCategory.WUMA_POJIE]
    if sensor_category not in valid_sensors:
        logger.warning(f"Unknown sensor category '{sensor_category}' in folder: {folder_name}")
        return None
    
    # Validate subtitle category
    valid_subtitles = [SubtitleCategory.ZHONGZI, SubtitleCategory.WUZI]
    if subtitle_category not in valid_subtitles:
        logger.warning(f"Unknown subtitle category '{subtitle_category}' in folder: {folder_name}")
        return None
    
    return movie_code, sensor_category, subtitle_category


def get_year_folders(remote_name: str, root_folder: str) -> List[str]:
    """
    Get list of year folders under root folder.
    
    Args:
        remote_name: Name of the rclone remote
        root_folder: Root folder path on the remote
    
    Returns:
        List[str]: List of year folder names (e.g., ['2024', '2025', '未知'])
    
    Raises:
        RuntimeError: If rclone command fails
    """
    remote_path = f"{remote_name}:{root_folder}"
    
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list year folders: {result.stderr}")
        
        years = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                # rclone lsd output format: "-1 2024-01-01 00:00:00 -1 folder_name"
                parts = line.strip().split()
                if len(parts) >= 5:
                    folder_name = ' '.join(parts[4:])
                    # Accept year numbers or 未知
                    if re.match(r'^\d{4}$', folder_name) or folder_name == '未知':
                        years.append(folder_name)
        
        logger.info(f"Found {len(years)} year folders: {years}")
        return years
        
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing year folders from {remote_path}")
    except Exception as e:
        raise RuntimeError(f"Error listing year folders: {str(e)}")


def get_actor_folders(remote_name: str, root_folder: str, year: str) -> List[str]:
    """
    Get list of actor folders under a year folder.
    
    Args:
        remote_name: Name of the rclone remote
        root_folder: Root folder path on the remote
        year: Year folder name
    
    Returns:
        List[str]: List of actor folder names
    
    Raises:
        RuntimeError: If rclone command fails
    """
    remote_path = f"{remote_name}:{root_folder}/{year}"
    
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return []
            raise RuntimeError(f"Failed to list actor folders: {result.stderr}")
        
        actors = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 5:
                    folder_name = ' '.join(parts[4:])
                    actors.append(folder_name)
        
        return actors
        
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing actor folders from {remote_path}")


def get_movie_folders(remote_name: str, root_folder: str, year: str, actor: str) -> List[FolderInfo]:
    """
    Get list of movie folders under an actor folder.
    
    Args:
        remote_name: Name of the rclone remote
        root_folder: Root folder path on the remote
        year: Year folder name
        actor: Actor folder name
    
    Returns:
        List[FolderInfo]: List of parsed folder information
    
    Raises:
        RuntimeError: If rclone command fails
    """
    remote_path = f"{remote_name}:{root_folder}/{year}/{actor}"
    
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return []
            raise RuntimeError(f"Failed to list movie folders: {result.stderr}")
        
        folders = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 5:
                    folder_name = ' '.join(parts[4:])
                    parsed = parse_folder_name(folder_name)
                    
                    if parsed:
                        movie_code, sensor, subtitle = parsed
                        full_path = f"{remote_path}/{folder_name}"
                        
                        folders.append(FolderInfo(
                            full_path=full_path,
                            year=year,
                            actor=actor,
                            movie_code=movie_code,
                            sensor_category=sensor,
                            subtitle_category=subtitle,
                            folder_name=folder_name
                        ))
        
        return folders
        
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing movie folders from {remote_path}")


def get_folder_stats(remote_path: str) -> Tuple[int, int]:
    """
    Get folder size and file count using rclone.
    
    Args:
        remote_path: Full rclone path to the folder
    
    Returns:
        Tuple[int, int]: (size_in_bytes, file_count)
    """
    try:
        result = subprocess.run(
            ['rclone', 'size', remote_path, '--json'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get('bytes', 0), data.get('count', 0)
    except Exception:
        pass
    
    return 0, 0


def get_video_file_mod_time(remote_path: str) -> Optional[datetime]:
    """
    Get the latest modification time of video files in a folder.
    
    Only considers video files (mp4, mkv, avi, etc.), not metadata files.
    
    Args:
        remote_path: Full rclone path to the folder
    
    Returns:
        Optional[datetime]: Latest video file modification time, or None if not found
    """
    try:
        # Use rclone lsjson to get file list with modification times
        result = subprocess.run(
            ['rclone', 'lsjson', remote_path, '--files-only'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return None
        
        files = json.loads(result.stdout)
        
        # Find video files and get latest mod time
        latest_mod_time: Optional[datetime] = None
        
        for file_info in files:
            file_name = file_info.get('Name', '').lower()
            _, ext = os.path.splitext(file_name)
            
            if ext in VIDEO_EXTENSIONS:
                mod_time_str = file_info.get('ModTime', '')
                if mod_time_str:
                    try:
                        # Parse ISO format: 2026-01-25T10:30:00.000000000Z
                        mod_time = datetime.fromisoformat(mod_time_str.replace('Z', '+00:00'))
                        # Convert to naive datetime for comparison
                        mod_time = mod_time.replace(tzinfo=None)
                        
                        if latest_mod_time is None or mod_time > latest_mod_time:
                            latest_mod_time = mod_time
                    except ValueError:
                        continue
        
        return latest_mod_time
        
    except Exception as e:
        logger.debug(f"Could not get video mod time for {remote_path}: {str(e)}")
        return None


def get_video_mod_times_batch(
    folders: List[FolderInfo],
    max_workers: int = 4
) -> None:
    """
    Get video file modification times for multiple folders in parallel.
    Updates the FolderInfo objects in place.
    
    Args:
        folders: List of FolderInfo objects to update
        max_workers: Number of parallel workers
    """
    if not folders:
        return
    
    logger.info(f"Getting video file modification times for {len(folders)} folders...")
    
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_folder = {
            executor.submit(get_video_file_mod_time, folder.full_path): folder
            for folder in folders
        }
        
        for future in as_completed(future_to_folder):
            folder = future_to_folder[future]
            completed += 1
            
            try:
                mod_time = future.result()
                folder.video_mod_time = mod_time
            except Exception as e:
                logger.debug(f"Could not get video mod time for {folder.full_path}: {str(e)}")
            
            if completed % 100 == 0:
                logger.info(f"Progress: {completed}/{len(folders)} folders processed")


def filter_folders_by_recent_changes(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]],
    days: int = INCREMENTAL_DAYS,
    max_workers: int = 4
) -> Dict[str, Dict[str, List[FolderInfo]]]:
    """
    Filter folder structure to only include movie codes with recently modified video files.
    
    For incremental dedup mode: only process movie codes where at least one folder
    has a video file modified within the specified number of days.
    
    Args:
        folder_structure: Original folder structure
        days: Number of days to look back for modifications
        max_workers: Number of parallel workers
    
    Returns:
        Dict: Filtered folder structure containing only movie codes with recent changes
    """
    logger.info(f"Filtering for movie codes with video files modified in last {days} days...")
    
    # First, collect all folders and get their video mod times
    all_folders: List[FolderInfo] = []
    for year_data in folder_structure.values():
        for folders in year_data.values():
            all_folders.extend(folders)
    
    if not all_folders:
        return {}
    
    # Get video modification times
    get_video_mod_times_batch(all_folders, max_workers=max_workers)
    
    # Calculate cutoff date
    cutoff_date = datetime.now() - timedelta(days=days)
    
    # Group folders by movie code and check if any has recent changes
    code_to_folders: Dict[str, List[FolderInfo]] = defaultdict(list)
    for folder in all_folders:
        code_to_folders[folder.movie_code].append(folder)
    
    # Find movie codes with at least one recent change
    codes_with_recent_changes: set = set()
    for code, folders in code_to_folders.items():
        for folder in folders:
            if folder.video_mod_time and folder.video_mod_time > cutoff_date:
                codes_with_recent_changes.add(code)
                logger.debug(f"Movie code {code} has recent change: {folder.folder_name} "
                            f"(mod time: {folder.video_mod_time})")
                break
    
    logger.info(f"Found {len(codes_with_recent_changes)} movie codes with recent changes "
                f"(out of {len(code_to_folders)} total)")
    
    # Rebuild folder structure with only the affected movie codes
    result: Dict[str, Dict[str, List[FolderInfo]]] = defaultdict(lambda: defaultdict(list))
    
    for year, actors in folder_structure.items():
        for actor, folders in actors.items():
            filtered_folders = [f for f in folders if f.movie_code in codes_with_recent_changes]
            if filtered_folders:
                result[year][actor] = filtered_folders
    
    return dict(result)


def scan_folder_structure(
    remote_name: str, 
    root_folder: str, 
    max_workers: int = 4,
    use_cache: bool = True,
    dry_run: bool = False,
    year_filter: Optional[List[str]] = None
) -> Tuple[Dict[str, Dict[str, List[FolderInfo]]], Optional[FolderCache]]:
    """
    Scan the entire folder structure and return organized data.
    
    Uses disk-based caching for memory optimization when dealing with large
    datasets (50000+ movies).
    
    In dry-run mode, scanning is limited to:
    - Latest 2 years
    - 50 actors per year
    - Maximum 100 year/actor combinations total
    
    Args:
        remote_name: Name of the rclone remote
        root_folder: Root folder path on the remote
        max_workers: Number of parallel workers for scanning
        use_cache: Whether to use disk-based caching for large datasets
        dry_run: If True, limit scanning scope for testing
        year_filter: Optional list of years to process (e.g., ['2025', '2026'])
    
    Returns:
        Tuple[Dict, Optional[FolderCache]]: 
            - Structure: {year: {actor: [FolderInfo, ...]}}
            - FolderCache instance (caller should clean up) or None
    
    Raises:
        RuntimeError: If scanning fails
    """
    logger.info(f"Scanning folder structure from {remote_name}:{root_folder}...")
    
    # Initialize cache for memory optimization
    cache = FolderCache() if use_cache else None
    
    # Step 1: Get all year folders
    years = get_year_folders(remote_name, root_folder)
    if not years:
        logger.warning("No year folders found")
        return {}, cache
    
    # Apply year filter if specified
    if year_filter:
        original_count = len(years)
        years = [y for y in years if y in year_filter]
        logger.info(f"Year filter applied: {len(years)}/{original_count} years selected: {years}")
        if not years:
            logger.warning(f"No matching years found for filter: {year_filter}")
            return {}, cache
    
    # In dry-run mode, limit to latest N years
    if dry_run:
        # Sort years descending (numeric years first, then 未知)
        def year_sort_key(y):
            if y == '未知':
                return 0  # Put 未知 at the end
            try:
                return int(y)
            except ValueError:
                return 0
        
        sorted_years = sorted(years, key=year_sort_key, reverse=True)
        years = sorted_years[:DRY_RUN_MAX_YEARS]
        logger.info(f"[DRY-RUN] Limited to {len(years)} latest years: {years}")
    
    # Step 2: Get actor folders for each year (parallel)
    year_actor_map: Dict[str, List[str]] = {}
    
    logger.info(f"Scanning actor folders with {max_workers} workers...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_year = {
            executor.submit(get_actor_folders, remote_name, root_folder, year): year
            for year in years
        }
        
        for future in as_completed(future_to_year):
            year = future_to_year[future]
            try:
                actors = future.result()
                
                # In dry-run mode, limit actors per year
                if dry_run and len(actors) > DRY_RUN_MAX_ACTORS_PER_YEAR:
                    actors = actors[:DRY_RUN_MAX_ACTORS_PER_YEAR]
                    logger.debug(f"[DRY-RUN] Year {year}: limited to {len(actors)} actors")
                
                year_actor_map[year] = actors
                logger.debug(f"Year {year}: {len(actors)} actors")
            except Exception as e:
                logger.error(f"Error scanning year {year}: {str(e)}")
                year_actor_map[year] = []
    
    # Step 3: Get movie folders for each year/actor combination (parallel)
    # Use batched processing for memory efficiency
    result: Dict[str, Dict[str, List[FolderInfo]]] = defaultdict(lambda: defaultdict(list))
    total_combinations = sum(len(actors) for actors in year_actor_map.values())
    
    # In dry-run mode, enforce total combination limit
    if dry_run and total_combinations > DRY_RUN_MAX_COMBINATIONS:
        logger.info(f"[DRY-RUN] Limiting from {total_combinations} to {DRY_RUN_MAX_COMBINATIONS} combinations")
        total_combinations = DRY_RUN_MAX_COMBINATIONS
    
    logger.info(f"Scanning movie folders for {total_combinations} year/actor combinations...")
    
    # Process in batches to control memory usage
    batch_tasks = []
    for year, actors in year_actor_map.items():
        for actor in actors:
            batch_tasks.append((year, actor))
            # In dry-run mode, stop when we reach the limit
            if dry_run and len(batch_tasks) >= DRY_RUN_MAX_COMBINATIONS:
                break
        if dry_run and len(batch_tasks) >= DRY_RUN_MAX_COMBINATIONS:
            break
    
    completed = 0
    total_folders = 0
    
    # Process in smaller batches if dataset is large
    batch_chunk_size = min(100, max_workers * 10)
    
    for i in range(0, len(batch_tasks), batch_chunk_size):
        chunk = batch_tasks[i:i + batch_chunk_size]
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(get_movie_folders, remote_name, root_folder, year, actor): (year, actor)
                for year, actor in chunk
            }
            
            for future in as_completed(future_to_path):
                year, actor = future_to_path[future]
                completed += 1
                
                try:
                    folders = future.result()
                    if folders:
                        if cache:
                            cache.add_folders(year, actor, folders)
                        result[year][actor] = folders
                        total_folders += len(folders)
                    
                    if completed % 50 == 0:
                        logger.info(f"Progress: {completed}/{total_combinations} combinations scanned, "
                                    f"{total_folders} folders found")
                        
                except Exception as e:
                    logger.error(f"Error scanning {year}/{actor}: {str(e)}")
        
        # Periodic garbage collection for memory management
        if i % (batch_chunk_size * 5) == 0 and i > 0:
            gc.collect()
    
    # Summary
    logger.info(f"Scan complete: {len(result)} years, {total_folders} movie folders")
    
    if cache:
        logger.info(f"Cache contains {cache.folder_count} folders")
    
    return dict(result), cache


# ============================================================================
# Deduplication Logic Functions
# ============================================================================

def group_folders_by_movie_code(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]]
) -> Dict[str, List[FolderInfo]]:
    """
    Group all folders by movie code for deduplication analysis.
    
    Args:
        folder_structure: Nested dictionary of year/actor/folders
    
    Returns:
        Dict[str, List[FolderInfo]]: movie_code -> list of folders with that code
    """
    code_map: Dict[str, List[FolderInfo]] = defaultdict(list)
    
    for year, actors in folder_structure.items():
        for actor, folders in actors.items():
            for folder in folders:
                code_map[folder.movie_code].append(folder)
    
    return dict(code_map)


def analyze_duplicates_for_code(movie_code: str, folders: List[FolderInfo]) -> DedupResult:
    """
    Analyze folders for a single movie code and determine which to delete.
    
    Deduplication rules:
    1. Split into 有码 and 无码 groups
    2. For 无码 group: Keep highest priority (无码流出 > 无码 > 无码破解)
    3. For each remaining group: If 中字 exists, delete 无字
    
    Args:
        movie_code: The movie code being analyzed
        folders: List of folders with this movie code
    
    Returns:
        DedupResult: Analysis result with folders to keep and delete
    """
    if len(folders) <= 1:
        return DedupResult(
            movie_code=movie_code,
            year=folders[0].year if folders else "",
            actor=folders[0].actor if folders else "",
            folders_to_keep=folders,
            folders_to_delete=[]
        )
    
    result = DedupResult(
        movie_code=movie_code,
        year=folders[0].year,
        actor=folders[0].actor,
        folders_to_keep=[],
        folders_to_delete=[]
    )
    
    # Split into 有码 and 无码 groups
    youma_folders = [f for f in folders if f.sensor_category == SensorCategory.YOUMA]
    wuma_folders = [f for f in folders if SensorCategory.is_wuma_category(f.sensor_category)]
    
    # Process 有码 group
    result = _process_subtitle_dedup(result, youma_folders, "有码")
    
    # Process 无码 group (with priority handling)
    result = _process_wuma_dedup(result, wuma_folders)
    
    return result


def _process_wuma_dedup(result: DedupResult, folders: List[FolderInfo]) -> DedupResult:
    """
    Process deduplication for 无码 category folders.
    
    Priority: 无码流出 > 无码 > 无码破解
    After priority dedup, apply subtitle dedup.
    Exception: If 无字 folder size is 30% larger than 中字, skip deletion.
    
    Args:
        result: Current dedup result to update
        folders: List of 无码 category folders
    
    Returns:
        DedupResult: Updated result
    """
    if not folders:
        return result
    
    # Group by subtitle category first
    zhongzi_folders = [f for f in folders if f.subtitle_category == SubtitleCategory.ZHONGZI]
    wuzi_folders = [f for f in folders if f.subtitle_category == SubtitleCategory.WUZI]
    
    # Process each subtitle group separately for sensor priority
    kept_zhongzi = _apply_sensor_priority(zhongzi_folders, result)
    kept_wuzi = _apply_sensor_priority(wuzi_folders, result)
    
    # Now apply subtitle rule: if 中字 exists in kept, delete 无字 (with size exception)
    if kept_zhongzi:
        result.folders_to_keep.extend(kept_zhongzi)
        
        # Get max 中字 size for comparison
        zhongzi_size = max(f.size for f in kept_zhongzi) if kept_zhongzi else 0
        
        for folder in kept_wuzi:
            wuzi_size = folder.size
            
            # Check size exception: if 无字 is 30% larger, keep it
            if zhongzi_size > 0 and wuzi_size > zhongzi_size * SIZE_THRESHOLD_RATIO:
                reason = (f"Exception: No-subtitle version ({format_size(wuzi_size)}) "
                          f"is 30%+ larger than subtitle version ({format_size(zhongzi_size)}), kept")
                logger.debug(f"Size exception for {folder.movie_code}: {reason}")
                result.folders_to_keep.append(folder)
            else:
                reason = f"Rule2: Subtitle version exists ({kept_zhongzi[0].sensor_category}-中字), delete no-subtitle version"
                result.folders_to_delete.append((folder, reason))
    else:
        result.folders_to_keep.extend(kept_wuzi)
    
    return result


def _apply_sensor_priority(folders: List[FolderInfo], result: DedupResult) -> List[FolderInfo]:
    """
    Apply sensor category priority within a group.
    
    Args:
        folders: List of folders to process
        result: DedupResult to update with deletions
    
    Returns:
        List[FolderInfo]: Folders to keep after priority dedup
    """
    if not folders:
        return []
    
    if len(folders) == 1:
        return folders
    
    # Find the highest priority folder
    sorted_folders = sorted(
        folders, 
        key=lambda f: SensorCategory.get_priority(f.sensor_category),
        reverse=True
    )
    
    keep_folder = sorted_folders[0]
    
    for folder in sorted_folders[1:]:
        reason = (f"Rule1: Uncensored priority ({keep_folder.sensor_category} > {folder.sensor_category}), "
                  f"keep {keep_folder.sensor_category}, delete {folder.sensor_category}")
        result.folders_to_delete.append((folder, reason))
    
    return [keep_folder]


def _process_subtitle_dedup(
    result: DedupResult, 
    folders: List[FolderInfo], 
    category_name: str
) -> DedupResult:
    """
    Process deduplication based on subtitle category.
    
    Rule: If 中字 version exists, delete 无字 version.
    Exception: If 无字 folder size is 30% larger than 中字, skip deletion.
    
    Args:
        result: Current dedup result to update
        folders: List of folders to process
        category_name: Category name for logging
    
    Returns:
        DedupResult: Updated result
    """
    if not folders:
        return result
    
    zhongzi = [f for f in folders if f.subtitle_category == SubtitleCategory.ZHONGZI]
    wuzi = [f for f in folders if f.subtitle_category == SubtitleCategory.WUZI]
    
    if zhongzi and wuzi:
        # Keep 中字
        result.folders_to_keep.extend(zhongzi)
        
        # For each 无字, check if it should be deleted or kept (size exception)
        for wuzi_folder in wuzi:
            # Compare with the first (or largest) 中字 folder
            zhongzi_size = max(f.size for f in zhongzi) if zhongzi else 0
            wuzi_size = wuzi_folder.size
            
            # Check size exception: if 无字 is 30% larger, keep it
            if zhongzi_size > 0 and wuzi_size > zhongzi_size * SIZE_THRESHOLD_RATIO:
                reason = (f"Exception: No-subtitle version ({format_size(wuzi_size)}) "
                          f"is 30%+ larger than subtitle version ({format_size(zhongzi_size)}), kept")
                logger.debug(f"Size exception for {wuzi_folder.movie_code}: {reason}")
                result.folders_to_keep.append(wuzi_folder)
            else:
                reason = f"Rule2: Subtitle version exists in {category_name} category, delete no-subtitle version"
                result.folders_to_delete.append((wuzi_folder, reason))
    else:
        # Keep all
        result.folders_to_keep.extend(zhongzi)
        result.folders_to_keep.extend(wuzi)
    
    return result


def analyze_all_duplicates(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]],
    max_workers: int = 4
) -> List[DedupResult]:
    """
    Analyze all folders for duplicates using parallel processing.
    
    Args:
        folder_structure: Nested dictionary from scan_folder_structure
        max_workers: Number of parallel workers
    
    Returns:
        List[DedupResult]: Analysis results for each movie code
    """
    logger.info("Analyzing duplicates...")
    
    # Group by movie code
    code_map = group_folders_by_movie_code(folder_structure)
    logger.info(f"Found {len(code_map)} unique movie codes")
    
    # Analyze each code in parallel
    results: List[DedupResult] = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_code = {
            executor.submit(analyze_duplicates_for_code, code, folders): code
            for code, folders in code_map.items()
        }
        
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                result = future.result()
                if result.folders_to_delete:
                    results.append(result)
            except Exception as e:
                logger.error(f"Error analyzing code {code}: {str(e)}")
    
    total_deletions = sum(len(r.folders_to_delete) for r in results)
    logger.info(f"Found {total_deletions} folders to delete across {len(results)} movie codes")
    
    return results


# ============================================================================
# Deletion and Reporting Functions
# ============================================================================

def get_folder_stats_batch(
    folders: List[FolderInfo], 
    max_workers: int = 4
) -> None:
    """
    Get folder stats (size, file count) for multiple folders in parallel.
    Updates the FolderInfo objects in place.
    
    Args:
        folders: List of FolderInfo objects to update
        max_workers: Number of parallel workers
    """
    if not folders:
        return
    
    logger.info(f"Getting folder stats for {len(folders)} folders...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_folder = {
            executor.submit(get_folder_stats, folder.full_path): folder
            for folder in folders
        }
        
        for future in as_completed(future_to_folder):
            folder = future_to_folder[future]
            try:
                size, count = future.result()
                folder.size = size
                folder.file_count = count
            except Exception as e:
                logger.debug(f"Could not get stats for {folder.full_path}: {str(e)}")


def delete_folder(remote_path: str, dry_run: bool = True) -> Tuple[bool, str]:
    """
    Delete a folder from remote storage.
    
    Args:
        remote_path: Full rclone path to delete
        dry_run: If True, only simulate deletion
    
    Returns:
        Tuple[bool, str]: (success, message)
    """
    try:
        if dry_run:
            return True, f"[DRY-RUN] Would delete: {remote_path}"
        
        result = subprocess.run(
            ['rclone', 'purge', remote_path],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            return True, f"Deleted: {remote_path}"
        else:
            return False, f"Failed to delete: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        return False, f"Timeout deleting: {remote_path}"
    except Exception as e:
        return False, f"Error deleting: {str(e)}"


def execute_deletions(
    dedup_results: List[DedupResult],
    dry_run: bool = True,
    max_workers: int = 2
) -> Tuple[int, int, int, int]:
    """
    Execute folder deletions based on dedup results.
    
    Args:
        dedup_results: List of deduplication analysis results
        dry_run: If True, only simulate deletions
        max_workers: Number of parallel workers for deletion
    
    Returns:
        Tuple[int, int, int, int]: (deleted_count, failed_count, total_size, total_files)
    """
    # Collect all folders to delete
    all_deletions: List[Tuple[FolderInfo, str]] = []
    for result in dedup_results:
        all_deletions.extend(result.folders_to_delete)
    
    if not all_deletions:
        logger.info("No folders to delete")
        return 0, 0, 0, 0
    
    # Get folder stats first
    folders_to_delete = [f for f, _ in all_deletions]
    get_folder_stats_batch(folders_to_delete, max_workers=max_workers)
    
    total_size = sum(f.size for f in folders_to_delete)
    total_files = sum(f.file_count for f in folders_to_delete)
    
    logger.info(f"{'[DRY-RUN] ' if dry_run else ''}Deleting {len(all_deletions)} folders...")
    logger.info(f"Total size: {format_size(total_size)}, Total files: {total_files}")
    
    deleted_count = 0
    failed_count = 0
    
    # Use fewer workers for actual deletion to be safe
    effective_workers = 1 if not dry_run else max_workers
    
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_folder = {
            executor.submit(delete_folder, folder.full_path, dry_run): folder
            for folder, _ in all_deletions
        }
        
        for future in as_completed(future_to_folder):
            folder = future_to_folder[future]
            try:
                success, message = future.result()
                if success:
                    deleted_count += 1
                    logger.debug(message)
                else:
                    failed_count += 1
                    logger.error(message)
            except Exception as e:
                failed_count += 1
                logger.error(f"Error deleting {folder.full_path}: {str(e)}")
    
    return deleted_count, failed_count, total_size, total_files


def format_size(size_bytes: int) -> str:
    """Format size in bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def generate_csv_report(
    dedup_results: List[DedupResult],
    output_dir: str = "reports/Dedup"
) -> str:
    """
    Generate CSV report of deleted folders.
    
    Args:
        dedup_results: List of deduplication analysis results
        output_dir: Base directory for reports
    
    Returns:
        str: Path to the generated CSV file
    """
    # Ensure output directory exists
    dated_dir = ensure_dated_dir(output_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"Dedup_Report_{timestamp}.csv"
    csv_path = os.path.join(dated_dir, filename)
    
    # Collect all deletion records
    records: List[DeletionRecord] = []
    
    for result in dedup_results:
        # Get the kept folder path(s) for this movie code
        kept_paths = [f.full_path for f in result.folders_to_keep]
        kept_folder_path = "; ".join(kept_paths) if kept_paths else ""
        
        for folder, reason in result.folders_to_delete:
            records.append(DeletionRecord(
                movie_code=folder.movie_code,
                sensor_category=folder.sensor_category,
                subtitle_category=folder.subtitle_category,
                deletion_reason=reason,
                size=folder.size,
                file_count=folder.file_count,
                full_path=folder.full_path,
                kept_folder_path=kept_folder_path
            ))
    
    # Write CSV
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # Header
        writer.writerow([
            'Movie Code',
            'Sensor Category',
            'Subtitle Category',
            'Deletion Reason',
            'Folder Size',
            'File Count',
            'Deleted Folder Path',
            'Kept Folder Path'
        ])
        
        # Data rows
        for record in records:
            writer.writerow([
                record.movie_code,
                record.sensor_category,
                record.subtitle_category,
                record.deletion_reason,
                format_size(record.size),
                record.file_count,
                record.full_path,
                record.kept_folder_path
            ])
    
    logger.info(f"CSV report saved to: {csv_path}")
    return csv_path


def print_summary(
    csv_path: str,
    deleted_count: int,
    failed_count: int,
    total_size: int,
    total_files: int,
    dry_run: bool
) -> None:
    """
    Print execution summary.
    
    Args:
        csv_path: Path to the generated CSV report
        deleted_count: Number of successfully deleted folders
        failed_count: Number of failed deletions
        total_size: Total size of deleted data
        total_files: Total number of deleted files
        dry_run: Whether this was a dry run
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("EXECUTION SUMMARY")
    logger.info("=" * 60)
    
    mode = "[DRY-RUN MODE]" if dry_run else "[LIVE MODE]"
    logger.info(f"Mode: {mode}")
    logger.info(f"CSV Report: {csv_path}")
    logger.info(f"Folders {'to delete' if dry_run else 'deleted'}: {deleted_count}")
    
    if failed_count > 0:
        logger.info(f"Failed deletions: {failed_count}")
    
    logger.info(f"Total size: {format_size(total_size)}")
    logger.info(f"Total files: {total_files}")
    
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("This was a DRY RUN. No files were actually deleted.")
        logger.info("Run without --dry-run flag to perform actual deletion.")
    else:
        if failed_count > 0:
            logger.warning(f"Completed with {failed_count} failures. Check logs for details.")
        else:
            logger.info("✓ All deletions completed successfully")


# ============================================================================
# Main Entry Point
# ============================================================================

def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.
    
    Returns:
        argparse.Namespace: Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description='Deduplicate movie folders on remote Google Drive',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s gdrive Movies --dry-run
  %(prog)s "My Drive" "JAVDB Collection" --workers 8
  %(prog)s gdrive Movies --incremental
  %(prog)s gdrive Movies --years "2025,2026"
  %(prog)s gdrive Movies --incremental --years "2026"
        """
    )
    
    parser.add_argument(
        'remote_name',
        help='Name of the rclone remote (e.g., gdrive)'
    )
    
    parser.add_argument(
        'root_folder',
        help='Root folder path on the remote (e.g., Movies)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate deletion without actually removing files'
    )
    
    parser.add_argument(
        '--workers',
        type=int,
        default=4,
        help='Number of parallel workers for scanning (default: 4)'
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )
    
    parser.add_argument(
        '--incremental',
        action='store_true',
        help=f'Incremental mode: only process movie codes with video files modified in last {INCREMENTAL_DAYS} days'
    )
    
    parser.add_argument(
        '--years',
        type=str,
        default=None,
        help='Comma-separated list of years to process (e.g., "2025,2026" or "2024,2025,未知")'
    )
    
    return parser.parse_args()


def main() -> int:
    """
    Main entry point for the deduplication script.
    
    Returns:
        int: Exit code (0 for success, 1 for failure)
    """
    args = parse_arguments()
    
    # Reconfigure logging with specified level
    setup_logging(log_level=args.log_level)
    
    # Parse year filter
    year_filter = None
    if args.years:
        year_filter = [y.strip() for y in args.years.split(',') if y.strip()]
    
    logger.info("=" * 60)
    logger.info("RCLONE DEDUPLICATION SCRIPT")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Remote: {args.remote_name}:{args.root_folder}")
    logger.info(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"Incremental: {'Yes' if args.incremental else 'No'}")
    if year_filter:
        logger.info(f"Year Filter: {year_filter}")
    logger.info(f"Workers: {args.workers}")
    logger.info("=" * 60)
    
    # Step 1: Health checks
    if not run_health_checks(args.remote_name, args.root_folder):
        return 1
    
    cache = None
    try:
        # Step 2: Scan folder structure
        logger.info("")
        if args.dry_run:
            logger.info("PHASE 1: Scanning folder structure (DRY-RUN: limited scope)...")
            logger.info(f"  - Max years: {DRY_RUN_MAX_YEARS} (latest)")
            logger.info(f"  - Max actors per year: {DRY_RUN_MAX_ACTORS_PER_YEAR}")
            logger.info(f"  - Max total combinations: {DRY_RUN_MAX_COMBINATIONS}")
        else:
            logger.info("PHASE 1: Scanning folder structure...")
        
        folder_structure, cache = scan_folder_structure(
            args.remote_name,
            args.root_folder,
            max_workers=args.workers,
            use_cache=True,
            dry_run=args.dry_run,
            year_filter=year_filter
        )
        
        if not folder_structure:
            logger.warning("No valid folder structure found. Nothing to deduplicate.")
            return 0
        
        # Step 2.5: Apply incremental filter if enabled
        if args.incremental:
            logger.info("")
            logger.info(f"PHASE 1.5: Filtering for recent changes (last {INCREMENTAL_DAYS} days)...")
            folder_structure = filter_folders_by_recent_changes(
                folder_structure,
                days=INCREMENTAL_DAYS,
                max_workers=args.workers
            )
            
            if not folder_structure:
                logger.info("No movie codes with recent changes found. Nothing to deduplicate.")
                return 0
        
        # Step 2.6: Get folder stats for all folders (needed for size-based rules)
        logger.info("")
        logger.info("PHASE 1.6: Getting folder sizes for size-based rules...")
        all_folders = []
        for year_data in folder_structure.values():
            for folders in year_data.values():
                all_folders.extend(folders)
        
        logger.info(f"Getting stats for {len(all_folders)} folders...")
        get_folder_stats_batch(all_folders, max_workers=args.workers)
        
        # Periodic garbage collection for large datasets
        if len(all_folders) > 10000:
            gc.collect()
        
        # Step 3: Analyze duplicates
        logger.info("")
        logger.info("PHASE 2: Analyzing duplicates...")
        dedup_results = analyze_all_duplicates(
            folder_structure,
            max_workers=args.workers
        )
        
        if not dedup_results:
            logger.info("No duplicates found. Nothing to delete.")
            return 0
        
        # Step 4: Execute deletions
        logger.info("")
        logger.info("PHASE 3: Executing deletions...")
        deleted_count, failed_count, total_size, total_files = execute_deletions(
            dedup_results,
            dry_run=args.dry_run,
            max_workers=args.workers
        )
        
        # Step 5: Generate report
        logger.info("")
        logger.info("PHASE 4: Generating report...")
        csv_path = generate_csv_report(dedup_results)
        
        # Step 6: Print summary
        print_summary(
            csv_path,
            deleted_count,
            failed_count,
            total_size,
            total_files,
            args.dry_run
        )
        
        return 0 if failed_count == 0 else 1
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        logger.exception("Stack trace:")
        return 1
    finally:
        # Clean up cache
        if cache:
            cache.clear()
        gc.collect()


if __name__ == '__main__':
    sys.exit(main())
