#!/usr/bin/env python3
"""Remove empty directories under year folders on an rclone remote.

传入的 ``root`` 被视为 JAV-Sync 根目录。脚本会先列出 root 下的四位数字年份
目录和 ``未知`` 目录，然后逐个调用 ``rclone rmdirs --leave-root`` 清理这些
目录下面的空子目录；年份/未知目录本身不会被删除。

相比在 Python 里递归逐目录检查，本脚本把递归扫描交给 rclone 内部执行，并用
``--workers`` 映射到 rclone 的 ``--checkers`` 来提高远端并发。对于支持
``--fast-list`` 的远端，也可以打开该选项来减少递归列目录请求数。

用法示例::

    python scripts/rclone_cleanup_empty_dirs.py gdrive:/不可以色色/JAV-Sync
    python scripts/rclone_cleanup_empty_dirs.py gdrive:/不可以色色/JAV-Sync --dry-run
    python scripts/rclone_cleanup_empty_dirs.py gdrive:/不可以色色/JAV-Sync -w 64 --fast-list
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from typing import List, Optional, Tuple

LOG = logging.getLogger("rclone_empty_dirs")
YEAR_DIR_RE = re.compile(r"^\d{4}$")
UNKNOWN_YEAR_DIR = "未知"


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


def list_dirs(remote_path: str) -> List[str]:
    """列出某个 remote 路径下的一级子目录名。"""
    proc = run_rclone(
        ["lsjson", "--dirs-only", "--no-modtime", "--no-mimetype", remote_path],
        check=True,
        capture=True,
    )
    items = json.loads(proc.stdout or "[]")
    return [
        str(item.get("Name", ""))
        for item in items
        if item.get("IsDir") and str(item.get("Name", ""))
    ]


def is_year_dir(name: str) -> bool:
    """四位数字年份和“未知”都作为年份目录处理。"""
    return bool(YEAR_DIR_RE.fullmatch(name)) or name == UNKNOWN_YEAR_DIR


def year_sort_key(name: str) -> Tuple[int, str]:
    """数字年份排前面，“未知”排在所有数字年份之后。"""
    if YEAR_DIR_RE.fullmatch(name):
        return 0, name
    return 1, name


def select_year_dirs(dirs: List[str]) -> Tuple[List[str], List[str]]:
    """返回 ``(year_dirs, skipped_dirs)``。处理四位数字年份目录和“未知”。"""
    year_dirs = sorted((name for name in dirs if is_year_dir(name)), key=year_sort_key)
    skipped_dirs = sorted(name for name in dirs if not is_year_dir(name))
    return year_dirs, skipped_dirs


def rmdirs_year(
    year_path: str,
    *,
    workers: int,
    dry_run: bool,
    fast_list: bool,
) -> subprocess.CompletedProcess:
    """用 rclone 内建 rmdirs 清理年份目录下的空目录，并保留年份目录本身。"""
    args: List[str] = []
    if dry_run:
        args.append("--dry-run")
    if fast_list:
        args.append("--fast-list")
    args.extend(["--checkers", str(workers), "rmdirs", "--leave-root", year_path])
    return run_rclone(args, check=True, capture=True, timeout=None)


def _log_rclone_output(proc: subprocess.CompletedProcess) -> None:
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    if output:
        LOG.info("%s", output)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove empty directories under year folders on an rclone remote.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        help="rclone 远端根路径，例如 gdrive:/不可以色色/JAV-Sync",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=32,
        help="传给 rclone --checkers 的并发数（默认 32）",
    )
    parser.add_argument(
        "--fast-list",
        action="store_true",
        help="启用 rclone --fast-list；通常更快但会使用更多内存",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要删除的空目录，不实际执行删除",
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

    try:
        root_dirs = list_dirs(args.root)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        LOG.error("failed to list root directories under %s: %s", args.root, exc)
        return 1

    year_dirs, skipped_dirs = select_year_dirs(root_dirs)
    if skipped_dirs:
        LOG.info("skipped non-year root dirs: %s", ", ".join(skipped_dirs))
    if not year_dirs:
        LOG.error("no year directories under %s", args.root)
        return 1

    LOG.info(
        "root=%s years=%s workers=%d dry_run=%s fast_list=%s",
        args.root,
        year_dirs,
        args.workers,
        args.dry_run,
        args.fast_list,
    )

    cleaned = failed = 0
    for year in year_dirs:
        year_path = join_remote(args.root, year)
        LOG.info("==> [%s] start", year)
        try:
            proc = rmdirs_year(
                year_path,
                workers=args.workers,
                dry_run=args.dry_run,
                fast_list=args.fast_list,
            )
        except subprocess.CalledProcessError as exc:
            failed += 1
            LOG.error(
                "<== [%s] failed :: %s",
                year,
                (exc.stderr or exc.stdout or str(exc)).strip(),
            )
            continue

        cleaned += 1
        _log_rclone_output(proc)
        LOG.info("<== [%s] done", year)

    LOG.info("=== finished years=%d cleaned=%d failed=%d ===", len(year_dirs), cleaned, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
