"""Rclone folder deduplication, deletion, and reporting helpers."""

import csv
import json
import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from javdb.infra.logging import get_logger, log_summary_block
from javdb.infra.paths import ensure_dated_dir
from javdb.integrations.rclone.types import (
    DedupResult,
    DeletionRecord,
    FolderInfo,
    SensorCategory,
    SIZE_THRESHOLD_RATIO,
    SubtitleCategory,
)

logger = get_logger(__name__)


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


def rclone_move(folder_path: str, destination_path: str, dry_run: bool = False) -> bool:
    """Execute ``rclone move <folder_path> <destination_path>``."""
    if dry_run:
        logger.info(f"[DRY-RUN] Would move: {folder_path} -> {destination_path}")
        return True
    cmd = ['rclone', 'move', folder_path, destination_path]
    logger.info(f"Executing: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            logger.info(f"  ✓ Moved: {folder_path} -> {destination_path}")
            return True
        logger.error(
            "  ✗ Failed to move %s -> %s: %s",
            folder_path,
            destination_path,
            result.stderr.strip(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"  ✗ Timeout moving {folder_path} -> {destination_path}")
        return False
    except Exception as e:
        logger.error(f"  ✗ Error moving {folder_path} -> {destination_path}: {e}")
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


def _get_folder_stats(remote_path: str) -> Optional[Tuple[int, int]]:
    """Get folder size and file count for deletion reporting."""
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


def _populate_deletion_stats(folders: List[FolderInfo], max_workers: int = 4) -> None:
    """Populate size and file_count for folders pending deletion."""
    if not folders:
        return
    logger.info(f"Getting folder stats for {len(folders)} folders...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_folder = {
            executor.submit(_get_folder_stats, folder.full_path): folder
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
    _populate_deletion_stats(folders_to_delete, max_workers=max_workers)

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
    mode = "[DRY-RUN MODE]" if dry_run else "[LIVE MODE]"
    summary = [
        ("Mode", mode),
        ("CSV Report", csv_path),
        (f"Folders {'to delete' if dry_run else 'deleted'}", deleted_count),
        ("Total size", format_size(total_size)),
        ("Total files", total_files),
    ]
    if failed_count > 0:
        summary.insert(3, ("Failed deletions", failed_count))
    log_summary_block(logger, "EXECUTION SUMMARY", summary)
    if dry_run:
        logger.info("This was a DRY RUN. No files were actually deleted.")
        logger.info("Run without --dry-run flag to perform actual deletion.")
    else:
        if failed_count > 0:
            logger.warning(f"Completed with {failed_count} failures. Check logs for details.")
        else:
            logger.info("✓ All deletions completed successfully")
