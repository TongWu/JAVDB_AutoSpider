#!/usr/bin/env python3
"""将指定远端目录下的文件按体积分档：大文件提到目录根层，小文件删除。

默认规则（可用 ``--min-mib`` 调整）：

- **大于等于** 阈值（默认 200 MiB）：若位于子目录中，则 ``moveto`` 到该根目录下；
  已在根目录下的文件跳过。
- **小于** 阈值：``deletefile`` 删除（包含根目录下的小文件）。

同名冲突（多个子目录内同名文件都要提到根目录）时，自动在文件名前加上
相对路径前缀（把 ``/`` 换成 ``_``）以保证目标路径唯一。

用法示例::

    python scripts/rclone_flatten_by_size.py \\
        'gdrive:剧集/不可以色色/动作片/日本分类' --dry-run
    python scripts/rclone_flatten_by_size.py \\
        'gdrive:剧集/不可以色色/动作片/日本分类' -w 16
    python scripts/rclone_flatten_by_size.py REMOTE:PATH --min-mib 500 --rmdirs

性能说明：

- 使用 ``rclone lsjson --recursive`` 一次性递归列出所有文件；
- 列举时可加 ``--fast-list``（适合 Google Drive 等支持的远端）；
- 删除与移动使用线程池并发执行（``-w``）；dry-run 下仅打印不落盘。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

LOG = logging.getLogger("rclone_flatten")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


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


def join_remote(base: str, *parts: str) -> str:
    """拼接 remote 路径片段（POSIX 风格）。"""
    path = base.rstrip("/")
    for p in parts:
        if not p:
            continue
        for seg in p.replace("\\", "/").split("/"):
            if seg:
                path = f"{path}/{seg}"
    return path


def normalize_root(root: str) -> str:
    r = root.strip()
    if not r:
        raise ValueError("empty root path")
    return r.rstrip("/")


def list_files_recursive(
    remote_root: str,
    *,
    fast_list: bool,
) -> List[dict]:
    """返回 lsjson 条目列表（仅文件）。"""
    args: List[str] = [
        "lsjson",
        "--files-only",
        "--recursive",
        "--no-mimetype",
    ]
    if fast_list:
        args.append("--fast-list")
    args.append(remote_root)
    try:
        proc = run_rclone(args, check=True, capture=True, timeout=None)
    except subprocess.CalledProcessError as exc:
        LOG.error("lsjson failed for %s: %s", remote_root, (exc.stderr or "").strip())
        return []
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        LOG.error("malformed rclone JSON for %s", remote_root)
        return []
    return [it for it in items if not it.get("IsDir")]


def mib_to_bytes(mib: float) -> int:
    return int(mib * 1024 * 1024)


@dataclass(frozen=True)
class FileRow:
    rel_path: str  # 相对列举根的路径，POSIX
    size: int


def parse_rows(items: List[dict]) -> List[FileRow]:
    rows: List[FileRow] = []
    for it in items:
        rel = str(it.get("Path") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        size = int(it.get("Size") or 0)
        rows.append(FileRow(rel_path=rel, size=size))
    return rows


def depth_one_name(rel_path: str) -> bool:
    """是否已在列举根下一层（无子目录）。"""
    return "/" not in rel_path


def choose_dst_names(large_under_subdir: List[FileRow]) -> Dict[str, str]:
    """rel_path -> 根目录下的目标文件名（仅 basename，冲突已消解）。"""
    # 先按 basename 分组，冲突时加入目录前缀
    basenames: Dict[str, List[str]] = {}
    for row in large_under_subdir:
        base = posix_basename(row.rel_path)
        basenames.setdefault(base, []).append(row.rel_path)

    result: Dict[str, str] = {}
    used: Set[str] = set()

    for row in large_under_subdir:
        base = posix_basename(row.rel_path)
        peers = basenames.get(base, [])
        if len(peers) <= 1:
            candidate = base
        else:
            parent = posix_dirname(row.rel_path)
            slug = parent.replace("/", "_") if parent else "dup"
            candidate = f"{slug}__{base}"
        candidate = uniquify_name(candidate, used)
        used.add(candidate)
        result[row.rel_path] = candidate

    return result


def posix_basename(path: str) -> str:
    path = path.replace("\\", "/").rstrip("/")
    if "/" not in path:
        return path
    return path.rsplit("/", 1)[-1]


def posix_dirname(path: str) -> str:
    path = path.replace("\\", "/").rstrip("/")
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def uniquify_name(name: str, used: Set[str]) -> str:
    if name not in used:
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        cand = f"{stem}_{n}{ext}"
        if cand not in used:
            return cand
        n += 1


def execute_delete(remote_file: str, dry_run: bool) -> Tuple[str, bool, str]:
    if dry_run:
        return remote_file, True, "DRY-RUN deletefile"
    try:
        run_rclone(["deletefile", remote_file], check=True, capture=True)
        return remote_file, True, "OK"
    except subprocess.CalledProcessError as exc:
        return remote_file, False, (exc.stderr or exc.stdout or str(exc)).strip()


def execute_moveto(src: str, dst: str, dry_run: bool) -> Tuple[str, bool, str]:
    if dry_run:
        return src, True, f"DRY-RUN moveto -> {dst}"
    try:
        run_rclone(["moveto", src, dst], check=True, capture=True)
        return src, True, "OK"
    except subprocess.CalledProcessError as exc:
        return src, False, (exc.stderr or exc.stdout or str(exc)).strip()


def rmdirs_root(remote_root: str, workers: int, dry_run: bool) -> None:
    args: List[str] = []
    if dry_run:
        args.append("--dry-run")
    args.extend(["--checkers", str(workers), "rmdirs", "--leave-root", remote_root])
    run_rclone(args, check=True, capture=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "将大于等于阈值的大文件提到远端目录根下，删除小于阈值的小文件。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        help=(
            "rclone 远端根路径（即操作目录），例如 "
            "gdrive:剧集/不可以色色/动作片/日本分类"
        ),
    )
    parser.add_argument(
        "-w", "--workers",
        type=positive_int,
        default=16,
        help="并发 worker 数量（默认 16）",
    )
    parser.add_argument(
        "--min-mib",
        type=float,
        default=200.0,
        help="体积分档阈值（MiB，二进制）；默认 200",
    )
    parser.add_argument(
        "--fast-list",
        action="store_true",
        help="列举时传入 rclone --fast-list（远端支持时更快）",
    )
    parser.add_argument(
        "--rmdirs",
        action="store_true",
        help="结束后对根目录执行 rclone rmdirs --leave-root（清理空文件夹）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的 deletefile / moveto，不实际调用 rclone",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="输出 DEBUG 级别日志",
    )
    return parser.parse_args()


def _init_logging(verbose: bool) -> None:
    """Use the canonical setup_logging when importable, fall back otherwise.

    Standalone CLI scripts under ``scripts/`` may be invoked outside the
    repo's import path; if the canonical logging module is unreachable
    we fall back to ``logging.basicConfig`` to preserve standalone use.
    """
    import logging as _logging
    import os as _os
    import pathlib as _pathlib
    import sys as _sys
    repo_root = _pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    try:
        from packages.python.javdb_platform.logging_config import setup_logging
        setup_logging(log_level='DEBUG' if verbose else 'INFO')
    except ImportError:
        _logging.basicConfig(
            level=_logging.DEBUG if verbose else _logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )


def main() -> int:
    args = parse_args()
    _init_logging(args.verbose)

    try:
        run_rclone(["version"], check=True, capture=True, timeout=15)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        LOG.error("rclone unavailable: %s", exc)
        return 2

    root = normalize_root(args.root)
    threshold = mib_to_bytes(args.min_mib)

    LOG.info(
        "root=%s threshold=%s MiB (%d bytes) workers=%d dry_run=%s fast_list=%s",
        root,
        args.min_mib,
        threshold,
        args.workers,
        args.dry_run,
        args.fast_list,
    )

    items = list_files_recursive(root, fast_list=args.fast_list)
    rows = parse_rows(items)
    LOG.info("listed files: %d", len(rows))

    small: List[FileRow] = []
    large_root: List[FileRow] = []
    large_nested: List[FileRow] = []

    for row in rows:
        if row.size >= threshold:
            if depth_one_name(row.rel_path):
                large_root.append(row)
            else:
                large_nested.append(row)
        else:
            small.append(row)

    LOG.info(
        "large at root (skip): %d | large to promote: %d | small (delete): %d",
        len(large_root),
        len(large_nested),
        len(small),
    )

    dst_by_rel = choose_dst_names(large_nested)

    delete_targets = [join_remote(root, r.rel_path) for r in small]
    move_jobs: List[Tuple[str, str, str]] = []
    for row in large_nested:
        dst_name = dst_by_rel[row.rel_path]
        src_full = join_remote(root, row.rel_path)
        dst_full = join_remote(root, dst_name)
        move_jobs.append((src_full, dst_full, row.rel_path))

    failed = 0

    if delete_targets:
        LOG.info("deleting %d small files...", len(delete_targets))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(execute_delete, path, args.dry_run): path
                for path in delete_targets
            }
            for fut in as_completed(futures):
                path, ok, msg = fut.result()
                if ok:
                    LOG.info("  delete %s :: %s", path, msg)
                else:
                    failed += 1
                    LOG.error("  [FAIL] delete %s :: %s", path, msg)

    if move_jobs:
        LOG.info("moving %d large files to root...", len(move_jobs))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(execute_moveto, src, dst, args.dry_run): (src, dst, rel)
                for src, dst, rel in move_jobs
            }
            for fut in as_completed(futures):
                src, dst, rel = futures[fut]
                _, ok, msg = fut.result()
                if ok:
                    LOG.info("  moveto %s -> %s :: %s", rel, posix_basename(dst), msg)
                else:
                    failed += 1
                    LOG.error("  [FAIL] moveto %s -> %s :: %s", src, dst, msg)

    if args.rmdirs:
        LOG.info("rmdirs (leave root) on %s ...", root)
        try:
            rmdirs_root(root, args.workers, args.dry_run)
        except subprocess.CalledProcessError as exc:
            failed += 1
            LOG.error("rmdirs failed: %s", (exc.stderr or "").strip())

    LOG.info(
        "=== done failed_ops=%d (large_skip=%d) ===",
        failed,
        len(large_root),
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
