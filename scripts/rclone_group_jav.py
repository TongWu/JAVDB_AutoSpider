#!/usr/bin/env python3
"""Reorganize JAV-Sync directories: insert a per-番号 directory at level 3.

目录结构（传入的 root 被视为根目录）::

    <root>/<年份>/<演员>/<番号 [打码-字幕]>   # 原结构

重组后::

    <root>/<年份>/<演员>/<番号>/<打码-字幕>   # 新结构

也就是：

1. 在第三层为每个 ``番号`` 新建一个目录。
2. 原来第三层的 ``番号 [打码-字幕]`` 目录被移动到 ``番号/`` 之下，成为第四层。
3. 第四层目录名去掉 ``番号``、空格和括号，只保留括号内的内容（例如
   ``有码-中字`` / ``流出-中字``）。同时兼容 ``[]`` 与 ``()`` 两种括号，
   因此可以在已运行过 ``rclone_rename_jav.py`` 的情况下继续使用。

用法示例::

    python scripts/rclone_group_jav.py gdrive:/不可以色色/JAV-Sync
    python scripts/rclone_group_jav.py gdrive:/不可以色色/JAV-Sync -w 32 --dry-run
    python scripts/rclone_group_jav.py gdrive:/不可以色色/JAV-Sync --year 2024 --year 2025

性能说明：
- 年份目录依次串行处理（便于分步/断点续跑与日志审阅）。
- 每个年份内部：
  * 并发列举所有演员目录下的番号目录；
  * 去重后并发执行 ``rclone mkdir`` 预建所有 ``番号/`` 目录，避免
    Google Drive 等后端因并发 moveto 自动创建父目录而产生同名重复；
  * 并发执行 ``rclone moveto`` 将原目录移入新路径（同 remote 内为
    服务器端重命名，不会产生数据传输）。
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
from typing import Dict, List, Optional, Set, Tuple

LOG = logging.getLogger("jav_group")

# 匹配末尾的 "[...]" 或 "(...)"。括号内容不允许再出现方/圆括号。
BRACKET_RE = re.compile(
    r"^(?P<base>.+?)\s*[\[\(](?P<inner>[^\[\]\(\)]+)[\]\)]\s*$"
)


# ---------------------------------------------------------------------------
# 名称解析
# ---------------------------------------------------------------------------
def plan_reorg(old_name: str) -> Optional[Tuple[str, str]]:
    """
    将原目录名拆分为 ``(番号, 括号内内容)``。
    不匹配则返回 ``None``。括号内部允许为例如 ``有码-中字`` 这类带 ``-`` 的内容。
    """
    match = BRACKET_RE.match(old_name)
    if not match:
        return None
    code = match.group("base").strip()
    inner = match.group("inner").strip()
    if not code or not inner:
        return None
    return code, inner


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


def mkdir_remote(remote_path: str) -> Tuple[str, bool, str]:
    try:
        run_rclone(["mkdir", remote_path], check=True, capture=True)
        return remote_path, True, "OK"
    except subprocess.CalledProcessError as exc:
        return remote_path, False, (exc.stderr or exc.stdout or str(exc)).strip()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReorgJob:
    actor_path: str  # 完整 remote 路径
    rel_dir: str     # 相对 root 的路径，例如 "2024/演员A"
    old_name: str    # 例如 "ABC-123 [有码-中字]"
    code: str        # 例如 "ABC-123"
    new_leaf: str    # 例如 "有码-中字"

    @property
    def src(self) -> str:
        return join_remote(self.actor_path, self.old_name)

    @property
    def code_dir(self) -> str:
        return join_remote(self.actor_path, self.code)

    @property
    def dst(self) -> str:
        return join_remote(self.code_dir, self.new_leaf)

    @property
    def rel_old(self) -> str:
        return f"{self.rel_dir}/{self.old_name}"

    @property
    def rel_new(self) -> str:
        return f"{self.rel_dir}/{self.code}/{self.new_leaf}"

    @property
    def rel_code_dir(self) -> str:
        return f"{self.rel_dir}/{self.code}"


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------
def execute_move(job: ReorgJob, dry_run: bool) -> Tuple[ReorgJob, bool, str]:
    if dry_run:
        return job, True, "DRY-RUN"
    try:
        run_rclone(["moveto", job.src, job.dst], check=True, capture=True)
        return job, True, "OK"
    except subprocess.CalledProcessError as exc:
        return job, False, (exc.stderr or exc.stdout or str(exc)).strip()
    except subprocess.TimeoutExpired:
        return job, False, "timeout"


def collect_jobs(
    year: str, year_path: str, actors: List[str], workers: int
) -> Tuple[List[ReorgJob], int, int]:
    """并发列举每个演员目录，收集所有 reorg 任务。

    返回 (jobs, scanned, skipped_empty)。skipped_empty 表示已经没有括号
    信息（例如第一轮清理后只剩 ``ABC-123``）或括号内容为空的条目数量。
    """
    jobs: List[ReorgJob] = []
    scanned = 0
    skipped = 0
    # 检测同一个 (actor, code, new_leaf) 出现多次的情况，避免目标路径冲突。
    seen_dst: Set[Tuple[str, str, str]] = set()
    collision = 0

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

            for old_name in code_dirs:
                scanned += 1
                plan = plan_reorg(old_name)
                if plan is None:
                    skipped += 1
                    LOG.debug("  skip (no bracket): %s/%s", rel_dir, old_name)
                    continue
                code, inner = plan

                # 若原名即是 "<code>"（没有括号后缀），plan_reorg 已返回 None；
                # 此处若 old_name 恰好等于 code（理论上不会走到这里），跳过。
                if old_name == code:
                    skipped += 1
                    continue

                key = (actor_path, code, inner)
                if key in seen_dst:
                    collision += 1
                    LOG.warning(
                        "  duplicate target: %s/%s/%s (from %s) — skipped",
                        rel_dir, code, inner, old_name,
                    )
                    continue
                seen_dst.add(key)

                jobs.append(
                    ReorgJob(
                        actor_path=actor_path,
                        rel_dir=rel_dir,
                        old_name=old_name,
                        code=code,
                        new_leaf=inner,
                    )
                )

    if collision:
        LOG.warning("  %d duplicate target(s) skipped", collision)
    return jobs, scanned, skipped


def pre_create_code_dirs(jobs: List[ReorgJob], workers: int, dry_run: bool) -> int:
    """预建所有唯一的 ``<actor>/<code>`` 目录，避免并发 moveto 时在
    Google Drive 等后端产生同名重复。

    返回失败数量（不阻断后续 moveto，失败条目会在 moveto 阶段再次暴露）。
    """
    # key: 完整 remote 路径；value: 相对 root 的显示路径
    unique: Dict[str, str] = {}
    for job in jobs:
        unique.setdefault(job.code_dir, job.rel_code_dir)

    if not unique:
        return 0

    LOG.info("    pre-mkdir %d code dirs", len(unique))
    if dry_run:
        for rel in unique.values():
            LOG.info("    [DRY-RUN mkdir] %s", rel)
        return 0

    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(mkdir_remote, path): rel for path, rel in unique.items()
        }
        for fut in as_completed(futures):
            rel = futures[fut]
            _, ok, msg = fut.result()
            if ok:
                LOG.debug("    [mkdir OK] %s", rel)
            else:
                failed += 1
                LOG.error("    [mkdir FAIL] %s :: %s", rel, msg)
    return failed


def process_year(
    root: str, year: str, workers: int, dry_run: bool
) -> Tuple[int, int, int, int]:
    """返回 (scanned, reorganized, skipped_no_bracket, failed)。"""
    year_path = join_remote(root, year)
    LOG.info("==> [%s] start", year)

    actors = list_dirs(year_path)
    LOG.info("    actors=%d", len(actors))
    if not actors:
        return 0, 0, 0, 0

    jobs, scanned, skipped = collect_jobs(year, year_path, actors, workers)
    LOG.info(
        "    scanned=%d candidates=%d skipped_no_bracket=%d",
        scanned, len(jobs), skipped,
    )

    if not jobs:
        LOG.info("<== [%s] nothing to reorganize", year)
        return scanned, 0, skipped, 0

    # 1) 预建所有 <actor>/<code> 目录
    pre_create_code_dirs(jobs, workers, dry_run)

    # 2) 并发执行 moveto，把原目录移入 <code>/<inner>
    reorganized = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute_move, job, dry_run) for job in jobs]
        for fut in as_completed(futures):
            job, ok, msg = fut.result()
            if ok:
                reorganized += 1
                LOG.info(
                    "    [%s] %s  ->  %s", msg, job.rel_old, job.rel_new,
                )
            else:
                failed += 1
                LOG.error(
                    "    [FAIL] %s  ->  %s :: %s",
                    job.rel_old, job.rel_new, msg,
                )

    LOG.info(
        "<== [%s] done scanned=%d reorganized=%d skipped=%d failed=%d",
        year, scanned, reorganized, skipped, failed,
    )
    return scanned, reorganized, skipped, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "把第三层 '番号 [打码-字幕]' 目录重组为 '番号/打码-字幕' 的两级结构。"
        ),
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
        help="仅打印将要执行的 mkdir / moveto，不实际调用 rclone",
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

    t_scan = t_reorg = t_skip = t_fail = 0
    for year in years:
        scanned, reorganized, skipped, failed = process_year(
            args.root, year, args.workers, args.dry_run
        )
        t_scan += scanned
        t_reorg += reorganized
        t_skip += skipped
        t_fail += failed

    LOG.info(
        "=== finished scanned=%d reorganized=%d skipped=%d failed=%d ===",
        t_scan, t_reorg, t_skip, t_fail,
    )
    return 0 if t_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
