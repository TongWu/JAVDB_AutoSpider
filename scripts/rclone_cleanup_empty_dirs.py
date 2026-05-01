#!/usr/bin/env python3
"""Remove empty directories under an rclone remote root.

传入的 ``root`` 被视为保留根目录；脚本只清理 root 下面的空子目录，不删除
root 本身。目录会按从深到浅的顺序处理，因此如果某个父目录只包含空子目录，
这些子目录删除后父目录也会被清理。

用法示例::

    python scripts/rclone_cleanup_empty_dirs.py gdrive:/不可以色色/JAV-Sync
    python scripts/rclone_cleanup_empty_dirs.py gdrive:/不可以色色/JAV-Sync --dry-run
    python scripts/rclone_cleanup_empty_dirs.py gdrive:/不可以色色/JAV-Sync --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

LOG = logging.getLogger("rclone_empty_dirs")


@dataclass(frozen=True)
class DirState:
    remote_path: str
    rel_path: str
    child_rels: List[str]
    file_count: int
    scan_failed: bool = False


# ---------------------------------------------------------------------------
# rclone 封装
# ---------------------------------------------------------------------------
def run_rclone(
    args: List[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    cmd = ["rclone", *args]
    LOG.debug("exec: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def join_remote(base: str, name: str) -> str:
    return base + name if base.endswith("/") else f"{base}/{name}"


def list_entries(remote_path: str) -> Tuple[List[str], int]:
    """列出 remote_path 下的一级子目录名和一级文件数量。"""
    proc = run_rclone(
        ["lsjson", "--no-modtime", "--no-mimetype", remote_path],
        check=True,
        capture=True,
    )
    items = json.loads(proc.stdout or "[]")

    dirs: List[str] = []
    file_count = 0
    for item in items:
        name = str(item.get("Name", ""))
        if not name:
            continue
        if item.get("IsDir"):
            dirs.append(name)
        else:
            file_count += 1
    return dirs, file_count


def rmdir_remote(remote_path: str) -> None:
    run_rclone(["rmdir", remote_path], check=True, capture=True)


# ---------------------------------------------------------------------------
# 扫描与清理计划
# ---------------------------------------------------------------------------
def collect_dir_tree(root: str) -> List[DirState]:
    """递归收集目录树，返回 children-first 顺序。"""
    states: List[DirState] = []

    def walk(remote_path: str, rel_path: str) -> None:
        try:
            child_names, file_count = list_entries(remote_path)
        except subprocess.CalledProcessError as exc:
            LOG.error("lsjson failed for %s: %s", remote_path, (exc.stderr or "").strip())
            states.append(
                DirState(
                    remote_path=remote_path,
                    rel_path=rel_path,
                    child_rels=[],
                    file_count=1,
                    scan_failed=True,
                )
            )
            return
        except (json.JSONDecodeError, OSError) as exc:
            LOG.error("listing failed for %s: %s", remote_path, exc)
            states.append(
                DirState(
                    remote_path=remote_path,
                    rel_path=rel_path,
                    child_rels=[],
                    file_count=1,
                    scan_failed=True,
                )
            )
            return

        child_rels: List[str] = []
        for child in child_names:
            child_rel = child if not rel_path else f"{rel_path}/{child}"
            child_rels.append(child_rel)
            walk(join_remote(remote_path, child), child_rel)

        states.append(
            DirState(
                remote_path=remote_path,
                rel_path=rel_path,
                child_rels=child_rels,
                file_count=file_count,
            )
        )

    walk(root, "")
    return states


def find_empty_dirs(states: List[DirState]) -> List[DirState]:
    """从扫描结果中找出可删除空目录，不包含 root 本身。"""
    removable: Set[str] = set()
    empty_dirs: List[DirState] = []

    for state in states:
        if not state.rel_path or state.scan_failed:
            continue

        if state.file_count == 0 and all(child in removable for child in state.child_rels):
            empty_dirs.append(state)
            removable.add(state.rel_path)

    return empty_dirs


def execute_cleanup(empty_dirs: List[DirState], dry_run: bool) -> Tuple[int, int, int]:
    """返回 ``(removed, skipped, failed)``。"""
    planned_empty = {state.rel_path for state in empty_dirs}
    removed_rels: Set[str] = set()
    removed = skipped = failed = 0

    for state in empty_dirs:
        child_empty_rels = [child for child in state.child_rels if child in planned_empty]
        if any(child not in removed_rels for child in child_empty_rels):
            skipped += 1
            LOG.warning("    [SKIP] %s :: child directory was not removed", state.rel_path)
            continue

        if dry_run:
            removed += 1
            removed_rels.add(state.rel_path)
            LOG.info("    [DRY-RUN rmdir] %s", state.rel_path)
            continue

        try:
            rmdir_remote(state.remote_path)
        except subprocess.CalledProcessError as exc:
            failed += 1
            LOG.error(
                "    [FAIL rmdir] %s :: %s",
                state.rel_path,
                (exc.stderr or exc.stdout or str(exc)).strip(),
            )
            continue
        except OSError as exc:
            failed += 1
            LOG.error("    [FAIL rmdir] %s :: %s", state.rel_path, exc)
            continue

        removed += 1
        removed_rels.add(state.rel_path)
        LOG.info("    [OK rmdir] %s", state.rel_path)

    return removed, skipped, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove empty directories under an rclone remote root.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        help="rclone 远端根路径，例如 gdrive:/不可以色色/JAV-Sync",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要删除的空目录，不实际执行 rclone rmdir",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出 DEBUG 级别日志",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        run_rclone(["version"], check=True, capture=True, timeout=15)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        LOG.error("rclone unavailable: %s", exc)
        return 2

    LOG.info("root=%s dry_run=%s", args.root, args.dry_run)
    states = collect_dir_tree(args.root)
    empty_dirs = find_empty_dirs(states)

    LOG.info("scanned_dirs=%d empty_dirs=%d", max(len(states) - 1, 0), len(empty_dirs))
    if not empty_dirs:
        LOG.info("nothing to clean")
        return 0

    removed, skipped, failed = execute_cleanup(empty_dirs, args.dry_run)
    LOG.info(
        "=== finished removed=%d skipped=%d failed=%d ===",
        removed,
        skipped,
        failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
