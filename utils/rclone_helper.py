"""
Shared rclone helper functions and data structures.

Centralises all rclone-related data models, parsing logic, health-check
routines, and dedup analysis used by ``scripts/rclone_manager.py``.
"""

import os
import sys
import re
import csv
import json
import gc
import base64
import subprocess
import tempfile
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Generator
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

from utils.logging_config import get_logger
from utils.path_helper import ensure_dated_dir

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

    WUMA_PRIORITY = {
        WUMA_LIUCHU: 3,
        WUMA: 2,
        WUMA_POJIE: 1,
    }

    @classmethod
    def is_wuma_category(cls, category: str) -> bool:
        return category in cls.WUMA_PRIORITY

    @classmethod
    def get_priority(cls, category: str) -> int:
        return cls.WUMA_PRIORITY.get(category, 0)


class SubtitleCategory:
    """Subtitle category constants"""
    ZHONGZI = "中字"
    WUZI = "无字"


# ============================================================================
# Constants
# ============================================================================

SIZE_THRESHOLD_RATIO = 1.30

BATCH_SIZE = 1000

DRY_RUN_MAX_YEARS = 2
DRY_RUN_MAX_ACTORS_PER_YEAR = 50
DRY_RUN_MAX_COMBINATIONS = 100

INCREMENTAL_DAYS = 30

VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.wmv', '.mov',
    '.flv', '.webm', '.m4v', '.ts', '.iso',
}


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class FolderInfo:
    """Information about a movie folder"""
    full_path: str
    year: str
    actor: str
    movie_code: str
    sensor_category: str
    subtitle_category: str
    folder_name: str
    size: int = 0
    file_count: int = 0
    video_mod_time: Optional[datetime] = None


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
    delete_command: str = ""
    delete_datetime: str = ""
    kept_folder_path: str = ""


@dataclass
class DedupResult:
    """Result of deduplication analysis for a movie code"""
    movie_code: str
    year: str
    actor: str
    folders_to_keep: List[FolderInfo] = field(default_factory=list)
    folders_to_delete: List[Tuple[FolderInfo, str]] = field(default_factory=list)


# ============================================================================
# Cache Manager for Memory Optimization
# ============================================================================

class FolderCache:
    """Disk-based cache manager for large folder structures."""

    def __init__(self, cache_dir: Optional[str] = None):
        self._cache_dir = cache_dir or tempfile.mkdtemp(prefix='rclone_dedup_')
        self._index_file = os.path.join(self._cache_dir, 'index.json')
        self._folder_count = 0
        self._year_actor_index: Dict[str, str] = {}
        logger.debug(f"Cache initialized at: {self._cache_dir}")

    def add_folders(self, year: str, actor: str, folders: List[FolderInfo]) -> None:
        if not folders:
            return
        key = f"{year}/{actor}"
        cache_file = os.path.join(self._cache_dir, f"{hash(key)}.pkl")
        with open(cache_file, 'wb') as f:
            pickle.dump(folders, f, protocol=pickle.HIGHEST_PROTOCOL)
        self._year_actor_index[key] = cache_file
        self._folder_count += len(folders)

    def get_folders(self, year: str, actor: str) -> List[FolderInfo]:
        key = f"{year}/{actor}"
        cache_file = self._year_actor_index.get(key)
        if not cache_file or not os.path.exists(cache_file):
            return []
        with open(cache_file, 'rb') as f:
            return pickle.load(f)

    def iter_all_folders(self, batch_size: int = BATCH_SIZE) -> Generator[List[FolderInfo], None, None]:
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
        result: Dict[str, Dict[str, List[FolderInfo]]] = defaultdict(lambda: defaultdict(list))
        for key, cache_file in self._year_actor_index.items():
            year, actor = key.split('/', 1)
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    result[year][actor] = pickle.load(f)
        return dict(result)

    @property
    def folder_count(self) -> int:
        return self._folder_count

    def clear(self) -> None:
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
# rclone config helper
# ============================================================================

def setup_rclone_config_from_base64(config_base64: str) -> bool:
    """Decode a Base64 rclone config and write it to the standard location."""
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


# ============================================================================
# Health Check Functions
# ============================================================================

def check_rclone_installed() -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ['rclone', 'version'],
            capture_output=True, text=True, timeout=30,
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
    try:
        result = subprocess.run(
            ['rclone', 'listremotes'],
            capture_output=True, text=True, timeout=30,
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
    remote_path = f"{remote_name}:{root_folder}"
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path, '--max-depth', '1'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return False, f"Folder '{root_folder}' not found on remote '{remote_name}'"
            return False, f"Cannot read folder: {result.stderr}"
        test_file = f"{remote_path}/.rclone_dedup_test_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        touch_result = subprocess.run(
            ['rclone', 'touch', test_file],
            capture_output=True, text=True, timeout=30,
        )
        if touch_result.returncode == 0:
            subprocess.run(
                ['rclone', 'deletefile', test_file],
                capture_output=True, timeout=30,
            )
            return True, f"Remote folder '{remote_path}' is readable and writable"
        else:
            return False, f"Remote folder is readable but not writable: {touch_result.stderr}"
    except subprocess.TimeoutExpired:
        return False, f"Timeout accessing remote folder '{remote_path}'"
    except Exception as e:
        return False, f"Error checking folder access: {str(e)}"


def run_health_checks(remote_name: str, root_folder: str) -> bool:
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

def _py_parse_folder_name(folder_name: str) -> Optional[Tuple[str, str, str]]:
    """Pure-Python fallback for folder name parsing."""
    pattern = r'^(.+?)\s*\[(.+?)-(.+?)\]$'
    match = re.match(pattern, folder_name.strip())
    if not match:
        return None

    movie_code = match.group(1).strip()
    sensor_category = match.group(2).strip()
    subtitle_category = match.group(3).strip()

    valid_sensors = [SensorCategory.YOUMA, SensorCategory.WUMA,
                     SensorCategory.WUMA_LIUCHU, SensorCategory.WUMA_POJIE]
    if sensor_category not in valid_sensors:
        logger.warning(f"Unknown sensor category '{sensor_category}' in folder: {folder_name}")
        return None

    valid_subtitles = [SubtitleCategory.ZHONGZI, SubtitleCategory.WUZI]
    if subtitle_category not in valid_subtitles:
        logger.warning(f"Unknown subtitle category '{subtitle_category}' in folder: {folder_name}")
        return None

    return movie_code, sensor_category, subtitle_category


try:
    from javdb_rust_core import parse_folder_name as _rs_parse_folder_name

    def parse_folder_name(folder_name: str) -> Optional[Tuple[str, str, str]]:
        """Parse a movie folder name (Rust-accelerated)."""
        return _rs_parse_folder_name(folder_name)
except ImportError:
    parse_folder_name = _py_parse_folder_name


def get_year_folders(remote_name: str, root_folder: str) -> List[str]:
    """Get list of year folders under root folder."""
    remote_path = f"{remote_name}:{root_folder}"
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list year folders: {result.stderr}")
        years = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 5:
                    folder_name = ' '.join(parts[4:])
                    if re.match(r'^\d{4}$', folder_name) or folder_name == '未知':
                        years.append(folder_name)
        logger.info(f"Found {len(years)} year folders: {years}")
        return years
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing year folders from {remote_path}")
    except Exception as e:
        raise RuntimeError(f"Error listing year folders: {str(e)}")


def get_actor_folders(remote_name: str, root_folder: str, year: str) -> List[str]:
    """Get list of actor folders under a year folder."""
    remote_path = f"{remote_name}:{root_folder}/{year}"
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path],
            capture_output=True, text=True, timeout=120,
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
    """Get list of movie folders under an actor folder."""
    remote_path = f"{remote_name}:{root_folder}/{year}/{actor}"
    try:
        result = subprocess.run(
            ['rclone', 'lsd', remote_path],
            capture_output=True, text=True, timeout=120,
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
                            year=year, actor=actor,
                            movie_code=movie_code,
                            sensor_category=sensor,
                            subtitle_category=subtitle,
                            folder_name=folder_name,
                        ))
        return folders
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing movie folders from {remote_path}")


def get_movie_folders_with_stats(
    remote_name: str, root_folder: str, year: str, actor: str,
) -> List[FolderInfo]:
    """Get movie folders with size/count using a single ``rclone lsjson -R``."""
    remote_path = f"{remote_name}:{root_folder}/{year}/{actor}"
    try:
        result = subprocess.run(
            ['rclone', 'lsjson', remote_path, '-R', '--fast-list'],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return []
            raise RuntimeError(f"Failed to list {remote_path}: {result.stderr}")
        entries = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing {remote_path}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from rclone for {remote_path}: {exc}") from exc

    top_dirs: set = set()
    dir_sizes: Dict[str, int] = defaultdict(int)
    dir_counts: Dict[str, int] = defaultdict(int)

    for entry in entries:
        path = entry.get('Path', '')
        is_dir = entry.get('IsDir', False)
        if is_dir:
            if '/' not in path:
                top_dirs.add(path)
            continue
        if '/' in path:
            dir_name = path.split('/', 1)[0]
            top_dirs.add(dir_name)
            dir_sizes[dir_name] += entry.get('Size', 0)
            dir_counts[dir_name] += 1

    folders: List[FolderInfo] = []
    for dir_name in top_dirs:
        parsed = parse_folder_name(dir_name)
        if not parsed:
            continue
        movie_code, sensor, subtitle = parsed
        folders.append(FolderInfo(
            full_path=f"{remote_path}/{dir_name}",
            year=year, actor=actor,
            movie_code=movie_code,
            sensor_category=sensor,
            subtitle_category=subtitle,
            folder_name=dir_name,
            size=dir_sizes.get(dir_name, 0),
            file_count=dir_counts.get(dir_name, 0),
        ))
    return folders


def get_all_movie_folders_for_year(
    remote_name: str, root_folder: str, year: str,
) -> List[FolderInfo]:
    """Get ALL movie folders under a year with one ``rclone lsjson -R`` call."""
    remote_path = f"{remote_name}:{root_folder}/{year}"
    try:
        result = subprocess.run(
            ['rclone', 'lsjson', remote_path, '-R', '--fast-list'],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return []
            raise RuntimeError(f"Failed to list {remote_path}: {result.stderr}")
        entries = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing {remote_path}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from rclone for {remote_path}: {exc}") from exc

    movie_dirs: set = set()
    dir_sizes: Dict[Tuple[str, str], int] = defaultdict(int)
    dir_counts: Dict[Tuple[str, str], int] = defaultdict(int)

    for entry in entries:
        path = entry.get('Path', '')
        is_dir = entry.get('IsDir', False)
        parts = path.split('/')
        if is_dir and len(parts) == 2:
            movie_dirs.add((parts[0], parts[1]))
            continue
        if not is_dir and len(parts) >= 3:
            key = (parts[0], parts[1])
            movie_dirs.add(key)
            dir_sizes[key] += entry.get('Size', 0)
            dir_counts[key] += 1

    folders: List[FolderInfo] = []
    for actor, folder_name in movie_dirs:
        parsed = parse_folder_name(folder_name)
        if not parsed:
            continue
        movie_code, sensor, subtitle = parsed
        key = (actor, folder_name)
        folders.append(FolderInfo(
            full_path=f"{remote_path}/{actor}/{folder_name}",
            year=year, actor=actor,
            movie_code=movie_code,
            sensor_category=sensor,
            subtitle_category=subtitle,
            folder_name=folder_name,
            size=dir_sizes.get(key, 0),
            file_count=dir_counts.get(key, 0),
        ))
    return folders


def get_folder_stats(remote_path: str) -> Tuple[int, int]:
    """Get folder size and file count using rclone."""
    try:
        result = subprocess.run(
            ['rclone', 'size', remote_path, '--json'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get('bytes', 0), data.get('count', 0)
    except Exception:
        pass
    return 0, 0


def get_video_file_mod_time(remote_path: str) -> Optional[datetime]:
    """Get the latest modification time of video files in a folder."""
    try:
        result = subprocess.run(
            ['rclone', 'lsjson', remote_path, '--files-only'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        files = json.loads(result.stdout)
        latest_mod_time: Optional[datetime] = None
        for file_info in files:
            file_name = file_info.get('Name', '').lower()
            _, ext = os.path.splitext(file_name)
            if ext in VIDEO_EXTENSIONS:
                mod_time_str = file_info.get('ModTime', '')
                if mod_time_str:
                    try:
                        mod_time = datetime.fromisoformat(mod_time_str.replace('Z', '+00:00'))
                        mod_time = mod_time.replace(tzinfo=None)
                        if latest_mod_time is None or mod_time > latest_mod_time:
                            latest_mod_time = mod_time
                    except ValueError:
                        continue
        return latest_mod_time
    except Exception as e:
        logger.debug(f"Could not get video mod time for {remote_path}: {str(e)}")
        return None


# ============================================================================
# Batch / parallel helpers
# ============================================================================

def get_video_mod_times_batch(folders: List[FolderInfo], max_workers: int = 4) -> None:
    """Get video file modification times for multiple folders in parallel."""
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


def get_folder_stats_batch(folders: List[FolderInfo], max_workers: int = 4) -> None:
    """Get folder stats (size, file count) for multiple folders in parallel."""
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


def filter_folders_by_recent_changes(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]],
    days: int = INCREMENTAL_DAYS,
    max_workers: int = 4,
) -> Dict[str, Dict[str, List[FolderInfo]]]:
    """Filter folder structure to only include movie codes with recently modified video files."""
    logger.info(f"Filtering for movie codes with video files modified in last {days} days...")

    all_folders: List[FolderInfo] = []
    for year_data in folder_structure.values():
        for folders in year_data.values():
            all_folders.extend(folders)
    if not all_folders:
        return {}

    get_video_mod_times_batch(all_folders, max_workers=max_workers)
    cutoff_date = datetime.now() - timedelta(days=days)

    code_to_folders: Dict[str, List[FolderInfo]] = defaultdict(list)
    for folder in all_folders:
        code_to_folders[folder.movie_code].append(folder)

    codes_with_recent_changes: set = set()
    for code, folders in code_to_folders.items():
        for folder in folders:
            if folder.video_mod_time and folder.video_mod_time > cutoff_date:
                codes_with_recent_changes.add(code)
                logger.debug(
                    f"Movie code {code} has recent change: {folder.folder_name} "
                    f"(mod time: {folder.video_mod_time})"
                )
                break

    logger.info(
        f"Found {len(codes_with_recent_changes)} movie codes with recent changes "
        f"(out of {len(code_to_folders)} total)"
    )

    result: Dict[str, Dict[str, List[FolderInfo]]] = defaultdict(lambda: defaultdict(list))
    for year, actors in folder_structure.items():
        for actor, folders in actors.items():
            filtered_folders = [f for f in folders if f.movie_code in codes_with_recent_changes]
            if filtered_folders:
                result[year][actor] = filtered_folders
    return dict(result)


# ============================================================================
# Folder structure scanning
# ============================================================================

def scan_folder_structure(
    remote_name: str,
    root_folder: str,
    max_workers: int = 4,
    use_cache: bool = True,
    dry_run: bool = False,
    year_filter: Optional[List[str]] = None,
) -> Tuple[Dict[str, Dict[str, List[FolderInfo]]], Optional[FolderCache]]:
    """Scan the entire folder structure and return organized data."""
    logger.info(f"Scanning folder structure from {remote_name}:{root_folder}...")

    cache = FolderCache() if use_cache else None

    years = get_year_folders(remote_name, root_folder)
    if not years:
        logger.warning("No year folders found")
        return {}, cache

    if year_filter:
        original_count = len(years)
        years = [y for y in years if y in year_filter]
        logger.info(f"Year filter applied: {len(years)}/{original_count} years selected: {years}")
        if not years:
            logger.warning(f"No matching years found for filter: {year_filter}")
            return {}, cache

    if dry_run:
        def year_sort_key(y):
            if y == '未知':
                return 0
            try:
                return int(y)
            except ValueError:
                return 0
        sorted_years = sorted(years, key=year_sort_key, reverse=True)
        years = sorted_years[:DRY_RUN_MAX_YEARS]
        logger.info(f"[DRY-RUN] Limited to {len(years)} latest years: {years}")

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
                if dry_run and len(actors) > DRY_RUN_MAX_ACTORS_PER_YEAR:
                    actors = actors[:DRY_RUN_MAX_ACTORS_PER_YEAR]
                    logger.debug(f"[DRY-RUN] Year {year}: limited to {len(actors)} actors")
                year_actor_map[year] = actors
                logger.debug(f"Year {year}: {len(actors)} actors")
            except Exception as e:
                logger.error(f"Error scanning year {year}: {str(e)}")
                year_actor_map[year] = []

    result: Dict[str, Dict[str, List[FolderInfo]]] = defaultdict(lambda: defaultdict(list))
    total_combinations = sum(len(actors) for actors in year_actor_map.values())

    if dry_run and total_combinations > DRY_RUN_MAX_COMBINATIONS:
        logger.info(f"[DRY-RUN] Limiting from {total_combinations} to {DRY_RUN_MAX_COMBINATIONS} combinations")
        total_combinations = DRY_RUN_MAX_COMBINATIONS

    logger.info(f"Scanning movie folders for {total_combinations} year/actor combinations...")

    batch_tasks = []
    for year, actors in year_actor_map.items():
        for actor in actors:
            batch_tasks.append((year, actor))
            if dry_run and len(batch_tasks) >= DRY_RUN_MAX_COMBINATIONS:
                break
        if dry_run and len(batch_tasks) >= DRY_RUN_MAX_COMBINATIONS:
            break

    completed = 0
    total_folders = 0
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
                        logger.info(
                            f"Progress: {completed}/{total_combinations} combinations scanned, "
                            f"{total_folders} folders found"
                        )
                except Exception as e:
                    logger.error(f"Error scanning {year}/{actor}: {str(e)}")
        if i % (batch_chunk_size * 5) == 0 and i > 0:
            gc.collect()

    logger.info(f"Scan complete: {len(result)} years, {total_folders} movie folders")
    if cache:
        logger.info(f"Cache contains {cache.folder_count} folders")
    return dict(result), cache


# ============================================================================
# Deduplication Logic Functions
# ============================================================================

def group_folders_by_movie_code(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]],
) -> Dict[str, List[FolderInfo]]:
    """Group all folders by movie code for deduplication analysis."""
    code_map: Dict[str, List[FolderInfo]] = defaultdict(list)
    for year, actors in folder_structure.items():
        for actor, folders in actors.items():
            for folder in folders:
                code_map[folder.movie_code].append(folder)
    return dict(code_map)


def analyze_duplicates_for_code(movie_code: str, folders: List[FolderInfo]) -> DedupResult:
    """Analyze folders for a single movie code and determine which to delete."""
    if len(folders) <= 1:
        return DedupResult(
            movie_code=movie_code,
            year=folders[0].year if folders else "",
            actor=folders[0].actor if folders else "",
            folders_to_keep=folders,
            folders_to_delete=[],
        )

    result = DedupResult(
        movie_code=movie_code,
        year=folders[0].year,
        actor=folders[0].actor,
        folders_to_keep=[],
        folders_to_delete=[],
    )

    youma_folders = [f for f in folders if f.sensor_category == SensorCategory.YOUMA]
    wuma_folders = [f for f in folders if SensorCategory.is_wuma_category(f.sensor_category)]

    result = _process_subtitle_dedup(result, youma_folders, "有码")
    result = _process_wuma_dedup(result, wuma_folders)
    return result


def _process_wuma_dedup(result: DedupResult, folders: List[FolderInfo]) -> DedupResult:
    """Process deduplication for 无码 category folders."""
    if not folders:
        return result

    zhongzi_folders = [f for f in folders if f.subtitle_category == SubtitleCategory.ZHONGZI]
    wuzi_folders = [f for f in folders if f.subtitle_category == SubtitleCategory.WUZI]

    kept_zhongzi = _apply_sensor_priority(zhongzi_folders, result)
    kept_wuzi = _apply_sensor_priority(wuzi_folders, result)

    if kept_zhongzi:
        result.folders_to_keep.extend(kept_zhongzi)
        zhongzi_size = max(f.size for f in kept_zhongzi) if kept_zhongzi else 0
        for folder in kept_wuzi:
            wuzi_size = folder.size
            if zhongzi_size > 0 and wuzi_size > zhongzi_size * SIZE_THRESHOLD_RATIO:
                reason = (
                    f"Exception: No-subtitle version ({format_size(wuzi_size)}) "
                    f"is 30%+ larger than subtitle version ({format_size(zhongzi_size)}), kept"
                )
                logger.debug(f"Size exception for {folder.movie_code}: {reason}")
                result.folders_to_keep.append(folder)
            else:
                reason = (
                    f"Rule2: Subtitle version exists ({kept_zhongzi[0].sensor_category}-中字), "
                    f"delete no-subtitle version"
                )
                result.folders_to_delete.append((folder, reason))
    else:
        result.folders_to_keep.extend(kept_wuzi)
    return result


def _apply_sensor_priority(folders: List[FolderInfo], result: DedupResult) -> List[FolderInfo]:
    """Apply sensor category priority within a group."""
    if not folders:
        return []
    if len(folders) == 1:
        return folders

    sorted_folders = sorted(
        folders,
        key=lambda f: SensorCategory.get_priority(f.sensor_category),
        reverse=True,
    )
    keep_folder = sorted_folders[0]
    for folder in sorted_folders[1:]:
        reason = (
            f"Rule1: Uncensored priority ({keep_folder.sensor_category} > {folder.sensor_category}), "
            f"keep {keep_folder.sensor_category}, delete {folder.sensor_category}"
        )
        result.folders_to_delete.append((folder, reason))
    return [keep_folder]


def _process_subtitle_dedup(
    result: DedupResult,
    folders: List[FolderInfo],
    category_name: str,
) -> DedupResult:
    """Process deduplication based on subtitle category."""
    if not folders:
        return result

    zhongzi = [f for f in folders if f.subtitle_category == SubtitleCategory.ZHONGZI]
    wuzi = [f for f in folders if f.subtitle_category == SubtitleCategory.WUZI]

    if zhongzi and wuzi:
        result.folders_to_keep.extend(zhongzi)
        for wuzi_folder in wuzi:
            zhongzi_size = max(f.size for f in zhongzi) if zhongzi else 0
            wuzi_size = wuzi_folder.size
            if zhongzi_size > 0 and wuzi_size > zhongzi_size * SIZE_THRESHOLD_RATIO:
                reason = (
                    f"Exception: No-subtitle version ({format_size(wuzi_size)}) "
                    f"is 30%+ larger than subtitle version ({format_size(zhongzi_size)}), kept"
                )
                logger.debug(f"Size exception for {wuzi_folder.movie_code}: {reason}")
                result.folders_to_keep.append(wuzi_folder)
            else:
                reason = f"Rule2: Subtitle version exists in {category_name} category, delete no-subtitle version"
                result.folders_to_delete.append((wuzi_folder, reason))
    else:
        result.folders_to_keep.extend(zhongzi)
        result.folders_to_keep.extend(wuzi)
    return result


def analyze_all_duplicates(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]],
    max_workers: int = 4,
) -> List[DedupResult]:
    """Analyze all folders for duplicates using parallel processing."""
    logger.info("Analyzing duplicates...")
    code_map = group_folders_by_movie_code(folder_structure)
    logger.info(f"Found {len(code_map)} unique movie codes")

    results: List[DedupResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_code = {
            executor.submit(analyze_duplicates_for_code, code, folders): code
            for code, folders in code_map.items()
        }
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                r = future.result()
                if r.folders_to_delete:
                    results.append(r)
            except Exception as e:
                logger.error(f"Error analyzing code {code}: {str(e)}")

    total_deletions = sum(len(r.folders_to_delete) for r in results)
    logger.info(f"Found {total_deletions} folders to delete across {len(results)} movie codes")
    return results


# ============================================================================
# Deletion and Reporting Functions
# ============================================================================

def rclone_purge(folder_path: str, dry_run: bool = False) -> bool:
    """Execute ``rclone purge <folder_path>``.  Returns True on success."""
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


def delete_folder(remote_path: str, dry_run: bool = True) -> Tuple[bool, str]:
    """Delete a folder from remote storage using ``rclone purge``."""
    try:
        if dry_run:
            return True, f"[DRY-RUN] Would delete: {remote_path}"
        result = subprocess.run(
            ['rclone', 'purge', remote_path],
            capture_output=True, text=True, timeout=300,
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
    max_workers: int = 2,
) -> Tuple[int, int, int, int]:
    """Execute folder deletions based on dedup results."""
    all_deletions: List[Tuple[FolderInfo, str]] = []
    for r in dedup_results:
        all_deletions.extend(r.folders_to_delete)

    if not all_deletions:
        logger.info("No folders to delete")
        return 0, 0, 0, 0

    folders_to_delete = [f for f, _ in all_deletions]
    get_folder_stats_batch(folders_to_delete, max_workers=max_workers)

    total_size = sum(f.size for f in folders_to_delete)
    total_files = sum(f.file_count for f in folders_to_delete)

    logger.info(f"{'[DRY-RUN] ' if dry_run else ''}Deleting {len(all_deletions)} folders...")
    logger.info(f"Total size: {format_size(total_size)}, Total files: {total_files}")

    deleted_count = 0
    failed_count = 0
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


# ============================================================================
# Formatting / Reporting helpers
# ============================================================================

def format_size(size_bytes: int) -> str:
    """Format size in bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def generate_csv_report(
    dedup_results: List[DedupResult],
    output_dir: str = "reports/Dedup",
) -> str:
    """Generate CSV report of deleted folders."""
    dated_dir = ensure_dated_dir(output_dir)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"Dedup_Report_{timestamp}.csv"
    csv_path = os.path.join(dated_dir, filename)

    records: List[DeletionRecord] = []
    delete_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for r in dedup_results:
        kept_paths = [f.full_path for f in r.folders_to_keep]
        kept_folder_path = "; ".join(kept_paths) if kept_paths else ""
        for folder, reason in r.folders_to_delete:
            delete_command = f'rclone purge "{folder.full_path}"'
            records.append(DeletionRecord(
                movie_code=folder.movie_code,
                sensor_category=folder.sensor_category,
                subtitle_category=folder.subtitle_category,
                deletion_reason=reason,
                size=folder.size,
                file_count=folder.file_count,
                full_path=folder.full_path,
                delete_command=delete_command,
                delete_datetime=delete_timestamp,
                kept_folder_path=kept_folder_path,
            ))

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Movie Code', 'Sensor Category', 'Subtitle Category',
            'Deletion Reason', 'Folder Size', 'File Count',
            'Deleted Folder Path', 'Delete Command',
            'Delete Datetime', 'Kept Folder Path',
        ])
        for rec in records:
            writer.writerow([
                rec.movie_code, rec.sensor_category, rec.subtitle_category,
                rec.deletion_reason, format_size(rec.size), rec.file_count,
                rec.full_path, rec.delete_command,
                rec.delete_datetime, rec.kept_folder_path,
            ])

    logger.info(f"CSV report saved to: {csv_path}")
    return csv_path


def print_summary(
    csv_path: str,
    deleted_count: int,
    failed_count: int,
    total_size: int,
    total_files: int,
    dry_run: bool,
) -> None:
    """Print execution summary."""
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
