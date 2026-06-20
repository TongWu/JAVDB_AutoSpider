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
)

try:
    from javdb.rust_core import analyze_folder_dedup as _rs_analyze_folder_dedup

    _RUST_DEDUP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _rs_analyze_folder_dedup = None
    _RUST_DEDUP_AVAILABLE = False

logger = get_logger(__name__)


def _require_rust_dedup() -> None:
    """Fail closed when the Rust dedup decision is unavailable (ADR-048 D4).

    The folder-dedup cascade is a Rust-Required module: there is no Python
    fallback, because a divergent keep/delete decision drives irreversible
    ``rclone purge`` (ADR-048 D6 — irreversibility/blast-radius trigger). It
    must fail loud, never silently degrade.
    """
    if not _RUST_DEDUP_AVAILABLE:
        raise RuntimeError(
            "rclone folder-dedup cascade is Rust-Required (ADR-048 D4): "
            "javdb.rust_core is unavailable. Build it with "
            "`maturin develop --release -m javdb/rust_core/Cargo.toml` "
            "or install the prebuilt wheel."
        )


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


def _build_delete_reason(rule_info: dict) -> str:
    """Rebuild the EXACT Python reason string for a Rust-decided deletion.

    The Rust cascade owns the keep/delete *decision* (ADR-048 D4); the
    human-readable reason strings live here so reports stay byte-identical to
    the pre-port Python cascade templates. ``rule_info`` carries the rule that
    fired plus the referenced sensors.
    """
    rule = rule_info["rule"]
    if rule == "Rule1":
        keep = rule_info["keep_sensor"]
        loser = rule_info["loser_sensor"]
        return (
            f"Rule1: Uncensored priority ({keep} > {loser}), "
            f"keep {keep}, delete {loser}"
        )
    if rule == "Rule2_youma":
        category_name = rule_info["category_name"]
        return f"Rule2: Subtitle version exists in {category_name} category, delete no-subtitle version"
    if rule == "Rule2_wuma":
        kept_zhongzi_sensor = rule_info["kept_zhongzi_sensor"]
        return (
            f"Rule2: Subtitle version exists ({kept_zhongzi_sensor}-中字), "
            f"delete no-subtitle version"
        )
    raise ValueError(f"Unknown dedup rule: {rule!r}")


def analyze_duplicates_for_code(movie_code: str, folders: List[FolderInfo]) -> DedupResult:
    """Analyze folders for a single movie code and determine which to delete.

    ADR-048 Phase 3a: the keep/delete *decision* is made in Rust
    (``analyze_folder_dedup``). Folders cross the boundary as dicts keyed by
    their input index; Rust returns indices to keep plus per-deletion rule
    context, and this function reattaches the original ``FolderInfo`` objects
    and rebuilds the exact reason strings in Python.
    """
    _require_rust_dedup()

    result = DedupResult(
        movie_code=movie_code,
        year=folders[0].year if folders else "",
        actor=folders[0].actor if folders else "",
        folders_to_keep=[],
        folders_to_delete=[],
    )

    if len(folders) <= 1:
        result.folders_to_keep = list(folders)
        return result

    decision = _rs_analyze_folder_dedup([
        {
            "sensor_category": f.sensor_category,
            "subtitle_category": f.subtitle_category,
            "size": f.size,
        }
        for f in folders
    ])

    # Defense-in-depth: the Rust core already guarantees a total, disjoint
    # partition, so this never fires — but it converts a would-be opaque
    # IndexError (out-of-range / duplicate index) into an explicit signal that
    # Rust returned a non-partition decision for this code.
    keep_idx = list(decision["keep"])
    del_idx = [d["index"] for d in decision["delete"]]
    if sorted(keep_idx + del_idx) != list(range(len(folders))):
        # ValueError (not RuntimeError) so analyze_all_duplicates classifies it
        # with the Rust-side D5 violations under DEDUP_INVARIANT_VIOLATION.
        raise ValueError(
            f"Rust dedup returned a non-partition decision for {movie_code}: "
            f"keep={keep_idx} delete={del_idx} n={len(folders)}"
        )

    result.folders_to_keep = [folders[i] for i in keep_idx]
    result.folders_to_delete = [
        (folders[d["index"]], _build_delete_reason(d))
        for d in decision["delete"]
    ]
    return result


def analyze_all_duplicates(
    folder_structure: Dict[str, Dict[str, List[FolderInfo]]],
    max_workers: int = 4,
) -> List[DedupResult]:
    """Analyze all folders for duplicates using parallel processing."""
    # Fail loud at the chokepoint before spawning workers — a per-code raise
    # inside the pool below would be swallowed by the except handler.
    _require_rust_dedup()

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
            except ValueError as e:
                # A Rust D5 invariant violation: skip this code (no deletions)
                # but mark it greppably — it warrants operator investigation.
                logger.error(f"DEDUP_INVARIANT_VIOLATION for code {code}, deletions skipped: {e}")
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
