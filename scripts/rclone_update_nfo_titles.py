#!/usr/bin/env python3
"""Update JAV-Sync NFO title format on an rclone remote.

目录结构 (传入的 root 被视为根目录)::

    <root>/<年份>/<演员>/<番号>/<打码-字幕>/*.nfo

本脚本会遍历第四层 ``打码-字幕`` 目录里的 ``.nfo`` 文件, 并把
``movie.title`` 从::

    <![CDATA[{title} [video_code 打码-字幕]]]>

改写为::

    <![CDATA[[video_code first_actor] title (打码-字幕)]]>

其中 ``first_actor`` 来自第一个 ``<actor><name>...`` 字段. 后缀规则:

1. 字幕字段 ``无字`` / ``无字幕`` 不显示.
2. 字幕字段 ``有字`` / ``有字幕`` 显示为 ``中字``.
3. 打码字段 ``有码`` 不显示.
4. 只有一个字段显示时不加 ``-``; 两个字段都不显示时不加括号.

用法示例::

    python scripts/rclone_update_nfo_titles.py gdrive:/不可以色色/JAV-Sync
    python scripts/rclone_update_nfo_titles.py gdrive:/不可以色色/JAV-Sync -w 32 --dry-run
    python scripts/rclone_update_nfo_titles.py gdrive:/不可以色色/JAV-Sync --year 2024 --year 2025

如果某个第四层目录没有任何 ``.nfo`` 文件, 脚本会把该目录内大于 100M 的文件
移动到 ``<root>/temp/<唯一目录>/``, 然后删除原目录.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import subprocess
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

LOG = logging.getLogger("jav_nfo_title")

MIN_TEMP_FILE_SIZE_BYTES = 100 * 1024 * 1024
EXCLUDED_ROOT_DIRS = {"temp"}
UNKNOWN_YEAR_DIR = "未知"

TITLE_CDATA_RE = re.compile(
    r"(?P<prefix><title>\s*<!\[CDATA\[)(?P<title>.*?)(?P<suffix>\]\]>\s*</title>)",
    re.DOTALL,
)
TITLE_SUFFIX_RE = re.compile(
    r"^(?P<body>.*?)\s*\[(?P<code>\S+)\s+(?P<classification>[^\[\]]+)\]\s*$",
    re.DOTALL,
)
ACTOR_BLOCK_RE = re.compile(r"<actor\b[^>]*>.*?</actor>", re.DOTALL)
ACTOR_NAME_RE = re.compile(r"<name\b[^>]*>(?P<name>.*?)</name>", re.DOTALL)

NO_SUBTITLES = {"无字", "无字幕"}
CHINESE_SUBTITLES = {"有字", "有字幕"}


def validate_year(value: str) -> int:
    if not re.fullmatch(r"\d{4}", value):
        raise argparse.ArgumentTypeError("must be a 4-digit year")
    return int(value)


def validate_requested_year(value: str) -> str:
    if value == UNKNOWN_YEAR_DIR or re.fullmatch(r"\d{4}", value):
        return value
    raise argparse.ArgumentTypeError("must be a 4-digit year or 未知")


# ---------------------------------------------------------------------------
# NFO title 解析与变换
# ---------------------------------------------------------------------------
def normalize_classification(classification: str) -> str:
    """按展示规则规范化 ``打码-字幕`` 后缀."""
    parts = [part.strip() for part in classification.split("-")]
    mosaic = parts[0] if parts else ""
    subtitle = parts[1] if len(parts) > 1 else ""

    mosaic_out = "" if mosaic == "有码" else mosaic
    if subtitle in NO_SUBTITLES:
        subtitle_out = ""
    elif subtitle in CHINESE_SUBTITLES:
        subtitle_out = "中字"
    else:
        subtitle_out = subtitle

    return "-".join(part for part in (mosaic_out, subtitle_out) if part)


def parse_year_dir(name: str) -> Optional[int]:
    """返回目录名表示的年份; 非纯数字年份目录返回 ``None``."""
    return int(name) if re.fullmatch(r"\d{4}", name) else None


def year_sort_key(name: str) -> Tuple[int, int, str]:
    """数字年份排前面, 非年份目录按未知年份排在最后."""
    year = parse_year_dir(name)
    if year is None:
        return 1, 0, name
    return 0, year, name


def select_year_dirs(
    dirs: List[str],
    *,
    requested_years: Optional[List[str]] = None,
    start_from: Optional[int] = None,
) -> Tuple[List[str], Set[str]]:
    """筛选 root 下需要处理的一级目录.

    ``temp`` 永远排除. 仅处理四位数字年份目录和显式 ``未知`` 目录;
    使用 ``start_from`` 时, ``未知`` 仍会保留.
    """
    candidates = [
        name for name in dirs
        if name.casefold() not in EXCLUDED_ROOT_DIRS
        and (parse_year_dir(name) is not None or name == UNKNOWN_YEAR_DIR)
    ]

    missing: Set[str] = set()
    if requested_years:
        wanted = set(requested_years)
        missing = wanted - set(candidates)
        candidates = [name for name in candidates if name in wanted]

    if start_from is not None:
        filtered = []
        for name in candidates:
            year = parse_year_dir(name)
            if year is None or year >= start_from:
                filtered.append(name)
        candidates = filtered

    return sorted(candidates, key=year_sort_key), missing


def transform_title(old_title: str, first_actor: str) -> Optional[str]:
    """返回改写后的 title; 若 title 不符合旧格式或无需修改则返回 ``None``."""
    match = TITLE_SUFFIX_RE.match(old_title)
    if not match:
        return None

    body = match.group("body").strip()
    code = match.group("code").strip()
    classification = match.group("classification").strip()
    suffix = normalize_classification(classification)

    new_title = f"[{code} {first_actor}] {body}"
    if suffix:
        new_title = f"{new_title} ({suffix})"

    return new_title if new_title != old_title else None


def extract_first_actor(nfo_text: str) -> Optional[str]:
    """提取第一个 actor/name."""
    actor_match = ACTOR_BLOCK_RE.search(nfo_text)
    if not actor_match:
        return None

    name_match = ACTOR_NAME_RE.search(actor_match.group(0))
    if not name_match:
        return None

    name = html.unescape(name_match.group("name")).strip()
    return name or None


def update_nfo_content(nfo_text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """返回 ``(new_content, old_title, new_title)``.

    ``new_content`` 为 ``None`` 表示无需修改或无法修改.
    """
    title_match = TITLE_CDATA_RE.search(nfo_text)
    if not title_match:
        return None, None, None

    first_actor = extract_first_actor(nfo_text)
    if not first_actor:
        return None, title_match.group("title"), None

    old_title = title_match.group("title")
    new_title = transform_title(old_title, first_actor)
    if not new_title:
        return None, old_title, None

    new_content = (
        nfo_text[: title_match.start("title")]
        + new_title
        + nfo_text[title_match.end("title") :]
    )
    return new_content, old_title, new_title


# ---------------------------------------------------------------------------
# rclone 封装
# ---------------------------------------------------------------------------
def run_rclone(
    args: List[str],
    *,
    check: bool = True,
    capture: bool = True,
    input_text: Optional[str] = None,
    timeout: Optional[float] = 300.0,
) -> subprocess.CompletedProcess:
    cmd = ["rclone", *args]
    LOG.debug("exec: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        input=input_text,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def join_remote(base: str, name: str) -> str:
    return base + name if base.endswith("/") else f"{base}/{name}"


def list_dirs(remote_path: str) -> List[str]:
    """列出某个 remote 路径下的一级子目录名."""
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


def list_files(remote_path: str) -> List[RemoteFile]:
    """列出某个 remote 路径下的一级文件名与大小."""
    try:
        proc = run_rclone(
            ["lsjson", "--files-only", "--no-modtime", "--no-mimetype", remote_path]
        )
    except subprocess.CalledProcessError as exc:
        LOG.error("lsjson failed for %s: %s", remote_path, (exc.stderr or "").strip())
        raise

    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        LOG.error("malformed rclone JSON for %s", remote_path)
        raise

    files: List[RemoteFile] = []
    for item in items:
        if item.get("IsDir"):
            continue
        try:
            size = int(item.get("Size") or 0)
        except (TypeError, ValueError):
            size = 0
        files.append(RemoteFile(name=str(item.get("Name", "")), size=size))
    return [file for file in files if file.name]


def list_nfo_files(remote_path: str) -> List[str]:
    """列出某个 remote 路径下的一级 .nfo 文件名."""
    return [file.name for file in list_files(remote_path) if file.name.lower().endswith(".nfo")]


def read_remote_text(remote_path: str) -> str:
    return run_rclone(["cat", remote_path], check=True, capture=True).stdout


def write_remote_text(remote_path: str, content: str) -> None:
    run_rclone(["rcat", remote_path], check=True, capture=True, input_text=content)


def mkdir_remote(remote_path: str) -> None:
    run_rclone(["mkdir", remote_path], check=True, capture=True)


def moveto_remote(src: str, dst: str) -> None:
    run_rclone(["moveto", src, dst], check=True, capture=True)


def purge_remote(remote_path: str) -> None:
    run_rclone(["purge", remote_path], check=True, capture=True)


# ---------------------------------------------------------------------------
# 任务与执行
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int


@dataclass(frozen=True)
class NfoJob:
    leaf_path: str
    rel_leaf: str
    nfo_name: str

    @property
    def remote_path(self) -> str:
        return join_remote(self.leaf_path, self.nfo_name)

    @property
    def rel_path(self) -> str:
        return f"{self.rel_leaf}/{self.nfo_name}"


@dataclass(frozen=True)
class CleanupJob:
    leaf_path: str
    rel_leaf: str


def cleanup_temp_path(root: str, job: CleanupJob) -> str:
    """Return a per-leaf temp directory for files rescued before purging."""
    temp_root = join_remote(root, "temp")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", job.rel_leaf).strip("._-")
    slug = slug[-80:] if slug else "leaf"
    checksum = zlib.crc32(job.rel_leaf.encode("utf-8")) & 0xFFFFFFFF
    return join_remote(temp_root, f"{slug}-{checksum:08x}")


def _list_code_dirs(year_path: str, year: str, actor: str) -> Tuple[str, str, List[str]]:
    actor_path = join_remote(year_path, actor)
    rel_actor = f"{year}/{actor}"
    return actor_path, rel_actor, list_dirs(actor_path)


def _list_leaf_dirs(code_path: str, rel_code: str) -> Tuple[str, str, List[str]]:
    return code_path, rel_code, list_dirs(code_path)


def _list_leaf_nfos(leaf_path: str, rel_leaf: str) -> Tuple[str, str, List[str]]:
    return leaf_path, rel_leaf, list_nfo_files(leaf_path)


def collect_jobs(
    year: str, year_path: str, actors: List[str], workers: int
) -> Tuple[List[NfoJob], List[CleanupJob], int, int, int]:
    """收集某年份所有 NFO 更新任务.

    返回 ``(nfo_jobs, cleanup_jobs, code_dirs, leaf_dirs, leaf_dirs_without_nfo)``.
    """
    jobs: List[NfoJob] = []
    cleanup_jobs: List[CleanupJob] = []
    code_dir_count = 0
    leaf_dir_count = 0
    leaf_without_nfo = 0
    code_dirs: List[Tuple[str, str, str]] = []
    leaf_dirs: List[Tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_list_code_dirs, year_path, year, actor) for actor in actors
        ]
        for fut in as_completed(futures):
            try:
                actor_path, rel_actor, codes = fut.result()
            except Exception as exc:  # noqa: BLE001
                LOG.error("  listing actor failed: %s", exc)
                continue

            for code in codes:
                code_dir_count += 1
                code_dirs.append((join_remote(actor_path, code), rel_actor, code))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_list_leaf_dirs, code_path, f"{rel_actor}/{code}")
            for code_path, rel_actor, code in code_dirs
        ]
        for fut in as_completed(futures):
            try:
                code_path, rel_code, leaves = fut.result()
            except Exception as exc:  # noqa: BLE001
                LOG.error("  listing code dir failed: %s", exc)
                continue

            for leaf in leaves:
                leaf_dir_count += 1
                leaf_dirs.append((join_remote(code_path, leaf), f"{rel_code}/{leaf}"))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_list_leaf_nfos, leaf_path, rel_leaf)
            for leaf_path, rel_leaf in leaf_dirs
        ]
        for fut in as_completed(futures):
            try:
                leaf_path, rel_leaf, nfo_files = fut.result()
            except Exception as exc:  # noqa: BLE001
                LOG.error("  listing leaf dir failed: %s", exc)
                continue

            if not nfo_files:
                leaf_without_nfo += 1
                cleanup_jobs.append(CleanupJob(leaf_path=leaf_path, rel_leaf=rel_leaf))
                LOG.debug("  cleanup candidate (no nfo): %s", rel_leaf)
                continue

            for nfo_name in nfo_files:
                jobs.append(NfoJob(leaf_path=leaf_path, rel_leaf=rel_leaf, nfo_name=nfo_name))

    return jobs, cleanup_jobs, code_dir_count, leaf_dir_count, leaf_without_nfo


def execute_update(job: NfoJob, dry_run: bool) -> Tuple[NfoJob, str, Optional[str]]:
    try:
        old_content = read_remote_text(job.remote_path)
        new_content, old_title, new_title = update_nfo_content(old_content)
    except subprocess.CalledProcessError as exc:
        return job, "FAIL", (exc.stderr or exc.stdout or str(exc)).strip()
    except Exception as exc:  # noqa: BLE001
        return job, "FAIL", str(exc)

    if new_content is None:
        if old_title is None:
            return job, "SKIP", "missing title CDATA"
        if new_title is None:
            return job, "SKIP", "missing actor or title already updated/unmatched"
        return job, "SKIP", "unchanged"

    if dry_run:
        LOG.debug("    title change: %s :: %s -> %s", job.rel_path, old_title, new_title)
        return job, "DRY-RUN", f"{old_title} -> {new_title}"

    try:
        LOG.debug("    title change: %s :: %s -> %s", job.rel_path, old_title, new_title)
        write_remote_text(job.remote_path, new_content)
    except subprocess.CalledProcessError as exc:
        return job, "FAIL", (exc.stderr or exc.stdout or str(exc)).strip()
    except Exception as exc:  # noqa: BLE001
        return job, "FAIL", str(exc)

    return job, "OK", f"{old_title} -> {new_title}"


def execute_cleanup(
    job: CleanupJob, root: str, dry_run: bool
) -> Tuple[CleanupJob, str, str]:
    unique_temp_path = cleanup_temp_path(root, job)
    try:
        large_files = [
            file
            for file in list_files(job.leaf_path)
            if file.size > MIN_TEMP_FILE_SIZE_BYTES
        ]
    except Exception as exc:  # noqa: BLE001
        return job, "FAIL", str(exc)

    if dry_run:
        for file in large_files:
            LOG.debug(
                "    cleanup move: %s/%s -> %s",
                job.rel_leaf,
                file.name,
                join_remote(unique_temp_path, file.name),
            )
        return (
            job,
            "DRY-RUN",
            f"move_large_files={len(large_files)} purge={job.rel_leaf}",
        )

    try:
        if large_files:
            mkdir_remote(unique_temp_path)
        for file in large_files:
            src = join_remote(job.leaf_path, file.name)
            dst = join_remote(unique_temp_path, file.name)
            LOG.debug("    cleanup move: %s -> %s", src, dst)
            moveto_remote(src, dst)
        purge_remote(job.leaf_path)
    except subprocess.CalledProcessError as exc:
        return job, "FAIL", (exc.stderr or exc.stdout or str(exc)).strip()
    except Exception as exc:  # noqa: BLE001
        return job, "FAIL", str(exc)

    return job, "OK", f"moved_large_files={len(large_files)} purged={job.rel_leaf}"


def process_year(
    root: str, year: str, workers: int, dry_run: bool
) -> Tuple[int, int, int, int, int]:
    """返回 ``(scanned_nfos, updated, skipped, cleaned, failed)``."""
    year_path = join_remote(root, year)
    LOG.info("==> [%s] start", year)

    actors = list_dirs(year_path)
    LOG.info("    actors=%d", len(actors))
    if not actors:
        return 0, 0, 0, 0, 0

    jobs, cleanup_jobs, code_dirs, leaf_dirs, leaf_without_nfo = collect_jobs(
        year, year_path, actors, workers
    )
    LOG.info(
        "    code_dirs=%d leaf_dirs=%d leaf_without_nfo=%d nfos=%d cleanup=%d",
        code_dirs,
        leaf_dirs,
        leaf_without_nfo,
        len(jobs),
        len(cleanup_jobs),
    )

    updated = skipped = cleaned = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute_update, job, dry_run) for job in jobs]
        for fut in as_completed(futures):
            job, status, msg = fut.result()
            if status in {"OK", "DRY-RUN"}:
                updated += 1
                LOG.info("    [%s] %s :: %s", status, job.rel_path, msg)
            elif status == "SKIP":
                skipped += 1
                LOG.debug("    [SKIP] %s :: %s", job.rel_path, msg)
            else:
                failed += 1
                LOG.error("    [FAIL] %s :: %s", job.rel_path, msg)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(execute_cleanup, job, root, dry_run) for job in cleanup_jobs
        ]
        for fut in as_completed(futures):
            job, status, msg = fut.result()
            if status in {"OK", "DRY-RUN"}:
                cleaned += 1
                LOG.info("    [%s cleanup] %s :: %s", status, job.rel_leaf, msg)
            else:
                failed += 1
                LOG.error("    [FAIL cleanup] %s :: %s", job.rel_leaf, msg)

    LOG.info(
        "<== [%s] done scanned=%d updated=%d skipped=%d cleaned=%d failed=%d",
        year,
        len(jobs),
        updated,
        skipped,
        cleaned,
        failed,
    )
    return len(jobs), updated, skipped, cleaned, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update JAV-Sync NFO movie.title format on an rclone remote.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root",
        help="rclone 远端根路径, 例如 gdrive:/不可以色色/JAV-Sync",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=16,
        help="并发 worker 数量 (默认 16)",
    )
    parser.add_argument(
        "--year",
        action="append",
        type=validate_requested_year,
        metavar="YEAR",
        help="只处理指定年份, 可重复指定; 不传则处理全部",
    )
    parser.add_argument(
        "--start-from",
        type=validate_year,
        metavar="YEAR",
        help='从指定年份开始处理; 显式"未知"目录排在所有年份之后',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅读取并打印将要更新/清理的内容, 不实际写回或删除 rclone 文件",
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

    root_dirs = list_dirs(args.root)
    if not root_dirs:
        LOG.error("no year directories under %s", args.root)
        return 1

    years, missing = select_year_dirs(
        root_dirs,
        requested_years=args.year,
        start_from=args.start_from,
    )
    if missing:
        LOG.warning("requested years not found: %s", ", ".join(sorted(missing)))
    if not years:
        LOG.error("no matching year directories under %s", args.root)
        return 1

    LOG.info(
        "root=%s years=%s start_from=%s workers=%d dry_run=%s",
        args.root,
        years,
        args.start_from,
        args.workers,
        args.dry_run,
    )

    total_scanned = total_updated = total_skipped = total_cleaned = total_failed = 0
    for year in years:
        scanned, updated, skipped, cleaned, failed = process_year(
            args.root, year, args.workers, args.dry_run
        )
        total_scanned += scanned
        total_updated += updated
        total_skipped += skipped
        total_cleaned += cleaned
        total_failed += failed

    LOG.info(
        "=== finished scanned=%d updated=%d skipped=%d cleaned=%d failed=%d ===",
        total_scanned,
        total_updated,
        total_skipped,
        total_cleaned,
        total_failed,
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
