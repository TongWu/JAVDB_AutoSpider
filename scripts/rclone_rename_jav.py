#!/usr/bin/env python3
"""Rename JAV-Sync directory names on an rclone remote.

目录结构（传入的 root 被视为根目录）::

    <root>/<年份>/<演员>/<番号 [打码-字幕]>

本脚本会遍历第三层目录并按以下规则重命名：

1. ``[ ... ]`` 改写为 ``( ... )``
2. 打码字段若为 ``有码`` → 删除该字段及其后的 ``-``
3. 打码字段若为 ``无码流出`` → 改为 ``流出``
4. 字幕字段若为 ``无字`` → 删除该字段及其前的 ``-``
5. 若两个字段都被删除 → 一并删除整对括号及其前的空格

用法示例::

    python scripts/rclone_rename_jav.py gdrive:/不可以色色/JAV-Sync
    python scripts/rclone_rename_jav.py gdrive:/不可以色色/JAV-Sync -w 32 --dry-run
    python scripts/rclone_rename_jav.py gdrive:/不可以色色/JAV-Sync --year 2024 --year 2025

性能说明：
- 年份目录依次串行处理（便于分步/断点续跑与日志审阅）。
- 每个年份内部，列举演员目录和执行重命名都使用线程池并发执行。
- rclone 对同 remote 内的目录重命名为服务器端操作，不会产生数据传输。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Tuple

LOG = logging.getLogger("jav_rename")

# 匹配末尾的 "[打码-字幕]"。打码/字幕字段本身不包含 "-"、"[" 或 "]"。
BRACKET_RE = re.compile(
    r"^(?P<base>.+?)\s*\[(?P<mosaic>[^\[\]\-]+)-(?P<sub>[^\[\]\-]+)\]\s*$"
)


# ---------------------------------------------------------------------------
# 名称变换
# ---------------------------------------------------------------------------
def transform_name(old_name: str) -> Optional[str]:
    """返回重命名后的新名称；若无需修改则返回 ``None``。"""
    match = BRACKET_RE.match(old_name)
    if not match:
        return None

    base = match.group("base").rstrip()
    mosaic = match.group("mosaic").strip()
    sub = match.group("sub").strip()

    # if mosaic == "有码":
    #     mosaic_out = ""
    # else:
    #     mosaic_out = mosaic
    mosaic_out = mosaic

    # sub_out = "" if sub == "无字" else sub
    sub_out = sub

    parts = [p for p in (mosaic_out, sub_out) if p]
    new_name = f"{base} ({'-'.join(parts)})" if parts else base

    return new_name if new_name != old_name else None


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
        timeout=timeout,
    )


def join_remote(base: str, name: str) -> str:
    """在 rclone 路径后附加子名称。"""
    return base + name if base.endswith("/") else f"{base}/{name}"


def list_dirs(remote_path: str) -> List[str]:
    """列出某个 remote 路径下的一级子目录名。"""
    try:
        proc = run_rclone(
            ["lsjson", "--dirs-only", "--no-modtime", "--no-mimetype", remote_path]
        )
    except subprocess.CalledProcessError as exc:
        LOG.error("lsjson failed for %s: %s", remote_path, (exc.stderr or "").strip())
        return []

    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        LOG.error("malformed rclone JSON for %s", remote_path)
        return []

    return [it["Name"] for it in items if it.get("IsDir")]


# ---------------------------------------------------------------------------
# 任务与执行
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RenameJob:
    actor_path: str   # 完整 remote 路径，例如 "gdrive:/.../JAV-Sync/2024/演员A"
    rel_dir: str      # 相对 root 的路径，例如 "2024/演员A"
    old_name: str
    new_name: str

    @property
    def src(self) -> str:
        return join_remote(self.actor_path, self.old_name)

    @property
    def dst(self) -> str:
        return join_remote(self.actor_path, self.new_name)

    @property
    def rel_old(self) -> str:
        return f"{self.rel_dir}/{self.old_name}"

    @property
    def rel_new(self) -> str:
        return f"{self.rel_dir}/{self.new_name}"


def execute_rename(job: RenameJob, dry_run: bool) -> Tuple[RenameJob, bool, str]:
    if dry_run:
        return job, True, "DRY-RUN"
    try:
        run_rclone(["moveto", job.src, job.dst], check=True, capture=True)
        return job, True, "OK"
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        return job, False, err
    except subprocess.TimeoutExpired:
        return job, False, "timeout"


def collect_jobs(
    year: str, year_path: str, actors: List[str], workers: int
) -> Tuple[List[RenameJob], int]:
    """并发列举每个演员目录，收集所有需要重命名的任务。"""
    jobs: List[RenameJob] = []
    scanned = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(list_dirs, join_remote(year_path, actor)): actor
            for actor in actors
        }
        for fut in as_completed(futures):
            actor = futures[fut]
            actor_path = join_remote(year_path, actor)
            rel_dir = f"{year}/{actor}"
            try:
                code_dirs = fut.result()
            except Exception as exc:  # noqa: BLE001
                LOG.error("  listing %s failed: %s", rel_dir, exc)
                continue

            for code in code_dirs:
                scanned += 1
                new_name = transform_name(code)
                if new_name:
                    jobs.append(
                        RenameJob(
                            actor_path=actor_path,
                            rel_dir=rel_dir,
                            old_name=code,
                            new_name=new_name,
                        )
                    )

    return jobs, scanned


def process_year(
    root: str, year: str, workers: int, dry_run: bool
) -> Tuple[int, int, int]:
    year_path = join_remote(root, year)
    LOG.info("==> [%s] start", year)

    actors = list_dirs(year_path)
    LOG.info("    actors=%d", len(actors))
    if not actors:
        return 0, 0, 0

    jobs, scanned = collect_jobs(year, year_path, actors, workers)
    LOG.info("    scanned=%d candidates=%d", scanned, len(jobs))

    if not jobs:
        LOG.info("<== [%s] nothing to rename", year)
        return scanned, 0, 0

    renamed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute_rename, job, dry_run) for job in jobs]
        for fut in as_completed(futures):
            job, ok, msg = fut.result()
            if ok:
                renamed += 1
                LOG.info("    [%s] %s  ->  %s", msg, job.rel_old, job.rel_new)
            else:
                failed += 1
                LOG.error(
                    "    [FAIL] %s  ->  %s :: %s", job.rel_old, job.rel_new, msg
                )

    LOG.info(
        "<== [%s] done scanned=%d renamed=%d failed=%d",
        year, scanned, renamed, failed,
    )
    return scanned, renamed, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename JAV-Sync directory names on an rclone remote.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        help="rclone 远端根路径，例如 gdrive:/不可以色色/JAV-Sync",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=16,
        help="并发 worker 数量（默认 16）",
    )
    parser.add_argument(
        "--year",
        action="append",
        metavar="YEAR",
        help="只处理指定年份，可重复指定；不传则处理全部",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的重命名，不实际调用 rclone moveto",
    )
    parser.add_argument(
        "-v", "--verbose",
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

    years = list_dirs(args.root)
    if not years:
        LOG.error("no year directories under %s", args.root)
        return 1

    if args.year:
        wanted = set(args.year)
        missing = wanted - set(years)
        if missing:
            LOG.warning("requested years not found: %s", ", ".join(sorted(missing)))
        years = [y for y in years if y in wanted]
        if not years:
            LOG.error("no matching year directories under %s", args.root)
            return 1

    years.sort()
    LOG.info(
        "root=%s years=%s workers=%d dry_run=%s",
        args.root, years, args.workers, args.dry_run,
    )

    total_scanned = total_renamed = total_failed = 0
    for year in years:
        scanned, renamed, failed = process_year(
            args.root, year, args.workers, args.dry_run
        )
        total_scanned += scanned
        total_renamed += renamed
        total_failed += failed

    LOG.info(
        "=== finished scanned=%d renamed=%d failed=%d ===",
        total_scanned, total_renamed, total_failed,
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
