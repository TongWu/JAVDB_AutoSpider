"""Rclone health checks, folder parsing, cache, and scan helpers."""

import base64
import gc
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Generator, List, Optional, Tuple

from javdb.infra.logging import get_logger
from javdb.infra.paths import atomic_write
from javdb.integrations.rclone.types import (
    BATCH_SIZE,
    DRY_RUN_MAX_ACTORS_PER_YEAR,
    DRY_RUN_MAX_COMBINATIONS,
    DRY_RUN_MAX_YEARS,
    FolderInfo,
    INCREMENTAL_DAYS,
    VIDEO_EXTENSIONS,
    _VALID_SENSORS,
    _VALID_SUBTITLES,
)

logger = get_logger(__name__)


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

    @staticmethod
    def _cache_path_for_key(cache_dir: str, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(cache_dir, f"{digest}.json")

    @staticmethod
    def _serialize_folders(folders: List[FolderInfo]) -> list[dict]:
        payload: list[dict] = []
        for folder in folders:
            item = asdict(folder)
            if folder.video_mod_time is not None:
                item["video_mod_time"] = folder.video_mod_time.isoformat()
            payload.append(item)
        return payload

    @staticmethod
    def _deserialize_folders(items: list[dict]) -> List[FolderInfo]:
        folders: List[FolderInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = dict(item)
            raw_video_mod_time = raw.get("video_mod_time")
            if raw_video_mod_time:
                try:
                    raw["video_mod_time"] = datetime.fromisoformat(raw_video_mod_time)
                except ValueError:
                    raw["video_mod_time"] = None
            else:
                raw["video_mod_time"] = None
            try:
                folders.append(FolderInfo(**raw))
            except TypeError:
                logger.warning("Skipping invalid cached folder payload for key=%s", raw.get("movie_code", "unknown"))
        return folders

    def add_folders(self, year: str, actor: str, folders: List[FolderInfo]) -> None:
        if not folders:
            return
        key = f"{year}/{actor}"
        cache_file = self._cache_path_for_key(self._cache_dir, key)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(self._serialize_folders(folders), f, ensure_ascii=False)
        self._year_actor_index[key] = cache_file
        self._folder_count += len(folders)

    def get_folders(self, year: str, actor: str) -> List[FolderInfo]:
        key = f"{year}/{actor}"
        cache_file = self._year_actor_index.get(key)
        if not cache_file or not os.path.exists(cache_file):
            return []
        with open(cache_file, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return self._deserialize_folders(payload if isinstance(payload, list) else [])

    def iter_all_folders(self, batch_size: int = BATCH_SIZE) -> Generator[List[FolderInfo], None, None]:
        batch: List[FolderInfo] = []
        for key, cache_file in self._year_actor_index.items():
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                folders = self._deserialize_folders(payload if isinstance(payload, list) else [])
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
                with open(cache_file, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                result[year][actor] = self._deserialize_folders(payload if isinstance(payload, list) else [])
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
        # B.8 (2026-05-12): atomic_write + 0o600 in one call. The legacy
        # ``open('wb')`` left a window where rclone could observe a
        # partially-written config (truncated remote section, missing
        # credentials) after a kill/restart and silently fall back to
        # an unprotected route. The temp-file-and-replace dance keeps
        # the destination either complete-old or complete-new.
        atomic_write(config_path, config_bytes, mode=0o600)
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

def _py_parse_folder_name(folder_name: str) -> Optional[Tuple[str, str, str]]:
    """Pure-Python fallback for **legacy** folder names ``code [sensor-subtitle]``.

    The current on-remote layout no longer uses this combined form (see
    :func:`parse_leaf_name`), but the helper is kept for backward
    compatibility with older inventory data and external callers/tests.
    """
    pattern = r'^(.+?)\s*\[(.+?)-(.+?)\]$'
    match = re.match(pattern, folder_name.strip())
    if not match:
        return None

    movie_code = match.group(1).strip()
    sensor_category = match.group(2).strip()
    subtitle_category = match.group(3).strip()

    if sensor_category not in _VALID_SENSORS:
        logger.warning(f"Unknown sensor category '{sensor_category}' in folder: {folder_name}")
        return None
    if subtitle_category not in _VALID_SUBTITLES:
        logger.warning(f"Unknown subtitle category '{subtitle_category}' in folder: {folder_name}")
        return None

    return movie_code, sensor_category, subtitle_category


def parse_leaf_name(leaf_name: str) -> Optional[Tuple[str, str]]:
    """Parse a leaf directory name ``<sensor>-<subtitle>`` into ``(sensor, subtitle)``.

    The new on-remote layout (after ``rclone_group_jav.py``) places per-token
    leaf directories one level below the ``<movie_code>/`` directory, so the
    leaf name no longer contains the movie code or surrounding brackets.
    """
    if not leaf_name:
        return None
    name = leaf_name.strip().strip('[]()')
    if '-' not in name:
        return None
    sensor, _, subtitle = name.rpartition('-')
    sensor = sensor.strip()
    subtitle = subtitle.strip()
    if sensor not in _VALID_SENSORS or subtitle not in _VALID_SUBTITLES:
        return None
    return sensor, subtitle


def _py_parse_lsd_output(output: str) -> List[str]:
    """Pure-Python fallback for ``rclone lsd`` line parsing.

    Each line looks like ``-1 2024-01-01 00:00:00 -1 <folder name>``; the
    folder name is everything from the 5th whitespace-delimited token onward
    (so names containing spaces are preserved).
    """
    folders: List[str] = []
    for line in output.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        parts = trimmed.split()
        if len(parts) >= 5:
            folders.append(' '.join(parts[4:]))
    return folders


try:
    from javdb.rust_core import (
        parse_folder_name as _rs_parse_folder_name,
        parse_lsd_output as _rs_parse_lsd_output,
        parse_lsjson_for_year as _rs_parse_lsjson_for_year,
    )
    _RUST_RCLONE_PARSE = True

    def parse_folder_name(folder_name: str) -> Optional[Tuple[str, str, str]]:
        """Parse a legacy movie folder name (Rust-accelerated)."""
        return _rs_parse_folder_name(folder_name)

    def parse_lsd_output(output: str) -> List[str]:
        """Parse ``rclone lsd`` output into folder names (Rust-accelerated)."""
        return _rs_parse_lsd_output(output)
except ImportError:
    _RUST_RCLONE_PARSE = False
    parse_folder_name = _py_parse_folder_name
    parse_lsd_output = _py_parse_lsd_output
    # ADR-041 D3: loud WARNING when the Rust accelerator is unavailable and we
    # fall back to the Best-Effort pure-Python scan path (ADR-048 D7).
    logger.warning(
        "javdb.rust_core unavailable — rclone scan parsing falls back to "
        "pure-Python (slower). Build the Rust extension for full performance."
    )


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
        years = [
            folder_name
            for folder_name in parse_lsd_output(result.stdout)
            if re.match(r'^\d{4}$', folder_name) or folder_name == '未知'
        ]
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
        return parse_lsd_output(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout listing actor folders from {remote_path}")


def get_movie_folders(remote_name: str, root_folder: str, year: str, actor: str) -> List[FolderInfo]:
    """Get movie folders under an actor folder.

    Layout (post ``rclone_group_jav.py``)::

        <root>/<year>/<actor>/<movie_code>/<sensor-subtitle>

    Each returned :class:`FolderInfo` corresponds to a leaf
    ``<sensor-subtitle>`` directory; ``movie_code`` is taken from the
    parent (depth-1) directory name.  Size/file counts are not populated
    here — use :func:`get_movie_folders_with_stats` for that.
    """
    remote_path = f"{remote_name}:{root_folder}/{year}/{actor}"
    try:
        result = subprocess.run(
            ['rclone', 'lsjson', remote_path, '-R',
             '--dirs-only', '--no-modtime', '--no-mimetype',
             '--fast-list'],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            if "directory not found" in result.stderr.lower():
                return []
            raise RuntimeError(f"Failed to list movie folders: {result.stderr}")
        try:
            entries = json.loads(result.stdout or '[]')
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from rclone for {remote_path}: {exc}") from exc

        folders: List[FolderInfo] = []
        for entry in entries:
            path = entry.get('Path', '')
            if not entry.get('IsDir'):
                continue
            parts = path.split('/')
            if len(parts) != 2:
                continue
            movie_code, leaf = parts[0], parts[1]
            parsed = parse_leaf_name(leaf)
            if not parsed:
                continue
            sensor, subtitle = parsed
            folders.append(FolderInfo(
                full_path=f"{remote_path}/{movie_code}/{leaf}",
                year=year, actor=actor,
                movie_code=movie_code,
                sensor_category=sensor,
                subtitle_category=subtitle,
                folder_name=leaf,
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

    # Layout: <actor>/<movie_code>/<sensor-subtitle>/<files...>
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
    for movie_code, leaf in movie_dirs:
        parsed = parse_leaf_name(leaf)
        if not parsed:
            continue
        sensor, subtitle = parsed
        key = (movie_code, leaf)
        folders.append(FolderInfo(
            full_path=f"{remote_path}/{movie_code}/{leaf}",
            year=year, actor=actor,
            movie_code=movie_code,
            sensor_category=sensor,
            subtitle_category=subtitle,
            folder_name=leaf,
            size=dir_sizes.get(key, 0),
            file_count=dir_counts.get(key, 0),
        ))
    return folders


def _py_parse_lsjson_for_year(json_str: str) -> List[dict]:
    """Pure-Python fallback mirroring Rust ``parse_lsjson_for_year``.

    Layout: ``<actor>/<movie_code>/<sensor-subtitle>/<files...>``. Directories
    register at depth 3; files at depth >=4 contribute size/file_count keyed by
    the same ``(actor, movie_code, leaf)`` 3-tuple. Returns one dict per kept
    leaf with keys ``actor``, ``movie_code``, ``folder_name``, ``sensor``,
    ``subtitle``, ``size``, ``file_count`` (same contract as the Rust helper).
    ``full_path``/``year`` are added by the caller when assembling FolderInfo.
    """
    entries = json.loads(json_str)
    if not isinstance(entries, list):
        # Mirror the Rust ``parse_lsjson_for_year`` contract, which rejects any
        # JSON that is not an array-of-objects (e.g. ``{}``, ``null``, ``42``)
        # with a sequence/struct error. Raise ValueError here so the caller's
        # single ``except`` maps it to RuntimeError identically on both paths.
        raise ValueError(f"expected a JSON array of objects, got {type(entries).__name__}")

    movie_dirs: set = set()
    dir_sizes: Dict[Tuple[str, str, str], int] = defaultdict(int)
    dir_counts: Dict[Tuple[str, str, str], int] = defaultdict(int)

    for entry in entries:
        path = entry.get('Path', '')
        is_dir = entry.get('IsDir', False)
        parts = path.split('/')
        if is_dir and len(parts) == 3:
            movie_dirs.add((parts[0], parts[1], parts[2]))
            continue
        if not is_dir and len(parts) >= 4:
            key = (parts[0], parts[1], parts[2])
            movie_dirs.add(key)
            dir_sizes[key] += entry.get('Size', 0)
            dir_counts[key] += 1

    results: List[dict] = []
    for actor, movie_code, leaf in movie_dirs:
        parsed = parse_leaf_name(leaf)
        if not parsed:
            continue
        sensor, subtitle = parsed
        key = (actor, movie_code, leaf)
        results.append({
            'actor': actor,
            'movie_code': movie_code,
            'folder_name': leaf,
            'sensor': sensor,
            'subtitle': subtitle,
            'size': dir_sizes.get(key, 0),
            'file_count': dir_counts.get(key, 0),
        })
    return results


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
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timeout listing {remote_path}") from exc

    # ADR-048 Phase 2: the Rust ``parse_lsjson_for_year`` now handles the
    # 3-level ``<actor>/<movie_code>/<sensor-subtitle>`` layout. Route through
    # it when available; the pure-Python ``_py_parse_lsjson_for_year`` is the
    # Best-Effort fallback (ADR-048 D7) used when the extension is missing.
    try:
        if _RUST_RCLONE_PARSE:
            parsed_rows = _rs_parse_lsjson_for_year(result.stdout)
        else:
            parsed_rows = _py_parse_lsjson_for_year(result.stdout)
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"Invalid JSON from rclone for {remote_path}: {exc}") from exc

    # Layout: <actor>/<movie_code>/<sensor-subtitle>/<files...>
    folders: List[FolderInfo] = []
    for row in parsed_rows:
        actor = row['actor']
        movie_code = row['movie_code']
        leaf = row['folder_name']
        folders.append(FolderInfo(
            full_path=f"{remote_path}/{actor}/{movie_code}/{leaf}",
            year=year, actor=actor,
            movie_code=movie_code,
            sensor_category=row['sensor'],
            subtitle_category=row['subtitle'],
            folder_name=leaf,
            size=row['size'],
            file_count=row['file_count'],
        ))
    return folders


def get_folder_stats(remote_path: str) -> Optional[Tuple[int, int]]:
    """Get folder size and file count using rclone.

    Returns ``None`` on error (rclone failure, timeout, JSON parse error)
    and ``(0, 0)`` for a reachable but empty folder.
    """
    try:
        result = subprocess.run(
            ['rclone', 'size', remote_path, '--json'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get('bytes', 0), data.get('count', 0)
    except Exception:
        return None


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
                        mod_time = mod_time.astimezone(timezone.utc).replace(tzinfo=None)
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
                result = future.result()
                if result is not None:
                    folder.size, folder.file_count = result
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
