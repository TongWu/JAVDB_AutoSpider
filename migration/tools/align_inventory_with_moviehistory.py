#!/usr/bin/env python3
"""Align rclone inventory with MovieHistory for missing movie codes.

Scope:
1) Only process movie codes present in RcloneInventory but missing in MovieHistory.
2) For each missing code, search JavDB by code, strictly match exact video code,
   parse detail page, and upsert MovieHistory/TorrentHistory.
3) Compare parsed torrent category vs current inventory category. If parsed is
   better, generate qBittorrent upgrade tasks and an rclone purge plan (direct delete).

Parallel mode (one worker per proxy) is used when ``--use-proxy`` is set and the
proxy pool is configured.  Falls back to single-threaded sequential mode otherwise.

HTTP fetching matches the main spider: initial requests do not force CF bypass;
``RequestHandler`` / global handler may enable bypass on fallback after failures.
Search uses **only the first results page** per video code.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue as queue_module
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)
sys.path.insert(0, project_root)

from api.parsers.common import normalize_javdb_href_path
from api.parsers.detail_parser import parse_detail_page
from api.parsers.index_parser import parse_index_page, find_exact_video_code_match
from scripts.spider.fallback import get_page_url
import scripts.spider.state as spider_state
from utils.config_helper import cfg
from utils.db import db_load_history, db_load_rclone_inventory, db_upsert_history, init_db
from utils.logging_config import get_logger, setup_logging
from utils.magnet_extractor import extract_magnets
from utils.path_helper import ensure_dated_dir
from utils.url_helper import build_search_url

setup_logging()
logger = get_logger(__name__)

_QB_FIELDNAMES = [
    'href',
    'video_code',
    'page',
    'hacked_subtitle',
    'hacked_no_subtitle',
    'subtitle',
    'no_subtitle',
]

_PURGE_PLAN_FIELDNAMES = [
    'video_code',
    'source_path',
    'existing_sensor',
    'existing_subtitle',
    'new_torrent_category',
    'reason',
]


@dataclass
class MissingProcessResult:
    video_code: str
    status: str
    href: str = ''
    detail_href: str = ''
    actor_name: str = ''
    chosen_upgrade_category: str = ''
    message: str = ''


def _normalize_code(value: str) -> str:
    return (value or '').strip().upper()


def compute_missing_codes(
    inventory: Dict[str, List[dict]],
    history_by_href: Dict[str, dict],
    only_codes: Optional[Iterable[str]] = None,
) -> List[str]:
    history_codes = {
        _normalize_code(v.get('VideoCode', ''))
        for v in history_by_href.values()
        if _normalize_code(v.get('VideoCode', ''))
    }
    inventory_codes = {_normalize_code(c) for c in inventory.keys() if _normalize_code(c)}
    missing = sorted(inventory_codes - history_codes)
    if only_codes:
        wanted = {_normalize_code(c) for c in only_codes if _normalize_code(c)}
        missing = [c for c in missing if c in wanted]
    return missing


def _parsed_category_rank(category: str) -> int:
    rank_map = {
        'hacked_subtitle': 40,      # 无码破解 + 中字
        'hacked_no_subtitle': 30,   # 无码破解 + 无字
        'subtitle': 20,             # 有码 + 中字
        'no_subtitle': 10,          # 有码 + 无字
    }
    return rank_map.get(category, 0)


def _inventory_entry_rank(entry: dict) -> int:
    sensor = (entry.get('SensorCategory') or entry.get('sensor_category') or '').strip()
    subtitle = (entry.get('SubtitleCategory') or entry.get('subtitle_category') or '').strip()
    if sensor == '有码':
        return 20 if subtitle == '中字' else 10
    if sensor == '无码破解':
        return 40 if subtitle == '中字' else 30
    if sensor == '无码':
        return 55 if subtitle == '中字' else 50
    if sensor == '无码流出':
        return 65 if subtitle == '中字' else 60
    return 0


def _best_inventory_rank(entries: List[dict]) -> int:
    if not entries:
        return 0
    return max(_inventory_entry_rank(e) for e in entries)


def _best_parsed_category(magnet_links: Dict[str, str]) -> str:
    candidates = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
    best = ''
    best_rank = 0
    for cat in candidates:
        if not magnet_links.get(cat):
            continue
        rank = _parsed_category_rank(cat)
        if rank > best_rank:
            best = cat
            best_rank = rank
    return best


def _to_qb_row(href: str, video_code: str, chosen_category: str, magnet_links: Dict[str, str]) -> dict:
    row = {
        'href': href,
        'video_code': video_code,
        'page': 1,
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
    }
    if chosen_category:
        row[chosen_category] = magnet_links.get(chosen_category, '')
    return row


def _to_purge_plan_rows(
    video_code: str,
    inventory_entries: List[dict],
    parsed_best_rank: int,
    new_torrent_category: str,
) -> List[dict]:
    """Rows for ``rclone purge`` (lower-quality inventory folders only)."""
    rows: List[dict] = []
    for entry in inventory_entries:
        src = (entry.get('FolderPath') or entry.get('folder_path') or '').strip()
        if not src:
            continue
        if _inventory_entry_rank(entry) >= parsed_best_rank:
            continue
        rows.append({
            'video_code': video_code,
            'source_path': src,
            'existing_sensor': entry.get('SensorCategory') or entry.get('sensor_category') or '',
            'existing_subtitle': entry.get('SubtitleCategory') or entry.get('subtitle_category') or '',
            'new_torrent_category': new_torrent_category,
            'reason': 'parsed_better_version',
        })
    return rows


def _build_db_upsert_kwargs(detail_href: str, video_code: str, magnet_links: dict,
                            actor_name: str, actor_gender: str, actor_link: str,
                            supporting_actors: str) -> dict:
    """Build keyword arguments for ``db_upsert_history``."""
    return {
        'href': detail_href,
        'video_code': video_code,
        'magnet_links': {
            'hacked_subtitle': magnet_links.get('hacked_subtitle', ''),
            'hacked_no_subtitle': magnet_links.get('hacked_no_subtitle', ''),
            'subtitle': magnet_links.get('subtitle', ''),
            'no_subtitle': magnet_links.get('no_subtitle', ''),
        },
        'size_links': {
            'hacked_subtitle': magnet_links.get('size_hacked_subtitle', ''),
            'hacked_no_subtitle': magnet_links.get('size_hacked_no_subtitle', ''),
            'subtitle': magnet_links.get('size_subtitle', ''),
            'no_subtitle': magnet_links.get('size_no_subtitle', ''),
        },
        'file_count_links': {
            'hacked_subtitle': int(magnet_links.get('file_count_hacked_subtitle', 0) or 0),
            'hacked_no_subtitle': int(magnet_links.get('file_count_hacked_no_subtitle', 0) or 0),
            'subtitle': int(magnet_links.get('file_count_subtitle', 0) or 0),
            'no_subtitle': int(magnet_links.get('file_count_no_subtitle', 0) or 0),
        },
        'resolution_links': {
            'hacked_subtitle': magnet_links.get('resolution_hacked_subtitle'),
            'hacked_no_subtitle': magnet_links.get('resolution_hacked_no_subtitle'),
            'subtitle': magnet_links.get('resolution_subtitle'),
            'no_subtitle': magnet_links.get('resolution_no_subtitle'),
        },
        'actor_name': actor_name,
        'actor_gender': actor_gender,
        'actor_link': actor_link,
        'supporting_actors': supporting_actors,
    }


def _init_spider_requester(use_proxy: bool) -> requests.Session:
    reports_dir = cfg('REPORTS_DIR', 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    ban_log = os.path.join(reports_dir, 'proxy_bans.csv')
    spider_state.setup_proxy_pool(ban_log_file=ban_log, use_proxy=use_proxy)
    spider_state.initialize_request_handler()
    return requests.Session()


def _fetch_html(
    session: requests.Session,
    url: str,
    *,
    use_proxy: bool,
) -> Optional[str]:
    # Same default as spider main: no forced CF bypass; handler may fall back.
    return spider_state.get_page(
        url,
        session=session,
        use_cookie=True,
        use_proxy=use_proxy,
        module_name='spider',
        max_retries=3,
        use_cf_bypass=False,
    )


def _write_csv(path: str, fieldnames: List[str], rows: List[dict]) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _enqueue_qb_from_csv(csv_path: str, use_proxy: bool, category_override: str = '') -> bool:
    from scripts import qb_uploader

    qb_uploader.initialize_proxy_helper(use_proxy)
    if not qb_uploader.test_qbittorrent_connection(use_proxy):
        logger.error("qBittorrent is not reachable")
        return False
    session = requests.Session()
    if not qb_uploader.login_to_qbittorrent(session, use_proxy):
        logger.error("qBittorrent login failed")
        return False

    torrents, exists = qb_uploader.read_csv_file(csv_path)
    if not exists:
        logger.error("qB input CSV not found: %s", csv_path)
        return False
    if not torrents:
        logger.info("No upgrade torrents to enqueue")
        return True

    existing_hashes = qb_uploader.get_existing_torrents(session, use_proxy)
    added = 0
    for t in torrents:
        if qb_uploader.is_torrent_exists(t['magnet'], existing_hashes):
            continue
        ok = qb_uploader.add_torrent_to_qbittorrent(
            session,
            t['magnet'],
            t['title'],
            mode='adhoc',
            use_proxy=use_proxy,
            category_override=category_override or None,
        )
        if ok:
            added += 1
    logger.info("qB enqueue complete: %d/%d added", added, len(torrents))
    return True


# ---------------------------------------------------------------------------
# Parallel alignment — one worker per proxy (mirrors BackfillWorker)
# ---------------------------------------------------------------------------


def _requeue_front(q: queue_module.Queue, item) -> None:
    """Put *item* at the front of a Queue so it gets picked up next."""
    with q.mutex:
        q.queue.appendleft(item)
        q.not_empty.notify()


@dataclass
class AlignTask:
    video_code: str
    inventory_entries: list
    search_url: str
    base_url: str
    entry_index: str
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)


@dataclass
class AlignResult:
    task: AlignTask
    process_result: MissingProcessResult
    qb_rows: list
    purge_plan_rows: list
    db_upsert_kwargs: dict | None
    parse_success: bool
    is_skipped: bool = False


def _apply_one_parallel_align_result(
    result: AlignResult,
    *,
    completed_codes: set[str],
    completed_lock: threading.Lock,
    dry_run: bool,
    process_results: list[MissingProcessResult],
    qb_rows: list[dict],
    purge_plan_rows: list[dict],
) -> tuple[int, int, int]:
    """Persist one worker result. Returns ``(processed, failed, skipped)``."""
    task = result.task
    if result.is_skipped:
        return 0, 0, 1

    process_results.append(result.process_result)

    if not result.parse_success:
        logger.warning(
            "[%s] Failed alignment for %s: %s",
            task.entry_index, task.video_code, result.process_result.message,
        )
        return 0, 1, 0

    with completed_lock:
        completed_codes.add(task.video_code)

    if result.db_upsert_kwargs and not dry_run:
        db_upsert_history(**result.db_upsert_kwargs)

    qb_rows.extend(result.qb_rows)
    purge_plan_rows.extend(result.purge_plan_rows)
    return 1, 0, 0


def _drain_parallel_align_result_queue_on_interrupt(
    result_queue: queue_module.Queue,
    *,
    completed_codes: set[str],
    completed_lock: threading.Lock,
    dry_run: bool,
    process_results: list[MissingProcessResult],
    qb_rows: list[dict],
    purge_plan_rows: list[dict],
    phase: str,
) -> tuple[int, int, int]:
    """Apply every ``AlignResult`` currently waiting on *result_queue* (non-blocking)."""
    dp = df = ds = 0
    drained = 0
    while True:
        try:
            try:
                result: AlignResult = result_queue.get_nowait()
            except queue_module.Empty:
                break
        except KeyboardInterrupt:
            logger.warning(
                "Second interrupt while draining result queue (%s); stopping flush early",
                phase,
            )
            break
        drained += 1
        p, f, s = _apply_one_parallel_align_result(
            result,
            completed_codes=completed_codes,
            completed_lock=completed_lock,
            dry_run=dry_run,
            process_results=process_results,
            qb_rows=qb_rows,
            purge_plan_rows=purge_plan_rows,
        )
        dp += p
        df += f
        ds += s
    if drained:
        logger.info(
            "Flushed %d pending align result(s) from queue (%s)",
            drained, phase,
        )
    return dp, df, ds


def _drain_align_task_queue_preserve(
    q: queue_module.Queue,
    sink: List[AlignTask],
) -> None:
    """Remove all pending ``AlignTask`` items from *q* (skip stray ``None``)."""
    while True:
        try:
            item = q.get_nowait()
        except queue_module.Empty:
            break
        if item is not None:
            sink.append(item)


def _parallel_align_signal_shutdown(
    *,
    stop_event: threading.Event,
    task_queue: queue_module.Queue[AlignTask | None],
    login_queue: queue_module.Queue[AlignTask],
    num_workers: int,
    not_started_tasks: List[AlignTask],
) -> None:
    """Wake workers, reclaim queued work, send stop sentinels."""
    stop_event.set()
    _drain_align_task_queue_preserve(task_queue, not_started_tasks)
    _drain_align_task_queue_preserve(login_queue, not_started_tasks)
    for _ in range(num_workers):
        task_queue.put(None)


_align_login_lock = threading.Lock()
_align_logged_in_worker_id: int | None = None


class AlignWorker(threading.Thread):
    """Worker thread bound to a single proxy for inventory-history alignment.

    Architecture mirrors ``BackfillWorker`` from ``migrate_v7_to_v8``:
    each proxy gets its own ``RequestHandler``, ``MovieSleepManager``,
    and CF-bypass tracking.  Workers share a task queue and result queue;
    the main thread collects results and writes to the DB.
    """

    def __init__(
        self,
        worker_id: int,
        proxy_config: dict,
        task_queue: queue_module.Queue[AlignTask | None],
        result_queue: queue_module.Queue[AlignResult],
        login_queue: queue_module.Queue[AlignTask],
        total_workers: int,
        use_cookie: bool,
        movie_sleep_min: float,
        movie_sleep_max: float,
        ban_log_file: str,
        all_workers: list,
        completed_codes: set | None = None,
        completed_lock: threading.Lock | None = None,
        stop_event: threading.Event | None = None,
        shutdown_orphan_tasks: list[AlignTask] | None = None,
        shutdown_orphan_lock: threading.Lock | None = None,
    ):
        super().__init__(daemon=True, name=f"AlignWorker-{proxy_config.get('name', worker_id)}")
        self.worker_id = worker_id
        self.proxy_config = proxy_config
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.login_queue = login_queue
        self.total_workers = total_workers
        self.use_cookie = use_cookie
        self.all_workers = all_workers
        self.completed_codes = completed_codes if completed_codes is not None else set()
        self.completed_lock = completed_lock if completed_lock is not None else threading.Lock()
        self._stop_event = stop_event or threading.Event()
        self._shutdown_orphan_tasks = shutdown_orphan_tasks
        self._shutdown_orphan_lock = shutdown_orphan_lock

        # Match spider main: start without forced CF; enable after successful bypass fallback.
        self.needs_cf_bypass = False
        self._first_request = True

        from scripts.spider.sleep_manager import MovieSleepManager
        self._sleep_mgr = MovieSleepManager(movie_sleep_min, movie_sleep_max)

        from utils.proxy_pool import create_proxy_pool_from_config
        from utils.request_handler import RequestHandler, RequestConfig
        from scripts.spider.config_loader import (
            BASE_URL, CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED,
            CF_TURNSTILE_COOLDOWN, FALLBACK_COOLDOWN,
            JAVDB_SESSION_COOKIE, PROXY_POOL_COOLDOWN_SECONDS,
            PROXY_POOL_MAX_FAILURES,
        )

        self._proxy_pool = create_proxy_pool_from_config(
            [proxy_config],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES,
            ban_log_file=ban_log_file,
        )
        self._handler = RequestHandler(
            proxy_pool=self._proxy_pool,
            config=RequestConfig(
                base_url=BASE_URL,
                cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
                cf_bypass_enabled=CF_BYPASS_ENABLED,
                cf_bypass_max_failures=3,
                cf_turnstile_cooldown=CF_TURNSTILE_COOLDOWN,
                fallback_cooldown=FALLBACK_COOLDOWN,
                javdb_session_cookie=JAVDB_SESSION_COOKIE,
                proxy_http=proxy_config.get('http'),
                proxy_https=proxy_config.get('https'),
                proxy_modules=['all'],
                proxy_mode='single',
            ),
        )

    # -- internal helpers --------------------------------------------------

    def _fetch_html_raw(self, url: str, use_cf: bool) -> str | None:
        return self._handler.get_page(
            url, use_cookie=self.use_cookie, use_proxy=True,
            module_name='spider', max_retries=1, use_cf_bypass=use_cf,
        )

    def _orphan_current_task(self, task: AlignTask) -> None:
        if self._shutdown_orphan_tasks is None or self._shutdown_orphan_lock is None:
            return
        with self._shutdown_orphan_lock:
            self._shutdown_orphan_tasks.append(task)

    def _interruptible_movie_sleep(self) -> bool:
        """Sleep like ``MovieSleepManager`` but return True if shutdown was requested."""
        t = self._sleep_mgr.get_sleep_time()
        logger.debug("[%s] Movie sleep: %.1fs (interruptible)", self.proxy_name, t)
        deadline = time.monotonic() + t
        chunk = 0.5
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._stop_event.wait(timeout=min(chunk, remaining)):
                return True
        return False

    def _try_fetch_page(self, url: str, use_cf: bool, context: str):
        """Fetch URL with login detection. Returns ``(html, needs_login)``."""
        from scripts.spider.session import is_login_page

        logger.debug("[%s] %s", self.proxy_name, context)
        try:
            html = self._fetch_html_raw(url, use_cf)
            if html:
                if is_login_page(html):
                    logger.warning("[%s] Login page: %s", self.proxy_name, context)
                    return None, True
                return html, False
            logger.debug("[%s] No HTML: %s", self.proxy_name, context)
        except Exception as e:
            logger.debug("[%s] Error in %s: %s", self.proxy_name, context, e)
        return None, False

    def _fetch_with_cf_fallback(self, url: str, context: str):
        """Try direct then CF bypass. Returns ``(html, used_cf, needs_login)``."""
        if self.needs_cf_bypass:
            html, login = self._try_fetch_page(url, True, f"{context} CF Bypass (marked)")
            return html, True, login

        html, login = self._try_fetch_page(url, False, f"{context} Direct")
        if html:
            return html, False, False
        if login:
            return None, False, True

        html, login = self._try_fetch_page(url, True, f"{context} CF Bypass")
        if html:
            self.needs_cf_bypass = True
            logger.info("[%s] CF Bypass succeeded — marked for runtime", self.proxy_name)
            return html, True, False
        return None, False, login

    # -- task processing ---------------------------------------------------

    def _process_task(self, task: AlignTask) -> tuple[AlignResult | None, bool]:
        """Full search → detail → parse pipeline.

        Returns ``(result, needs_login)``:
        * ``result`` is set → put on result_queue (success or definitive miss)
        * ``result is None, needs_login=True`` → route to login queue
        * ``result is None, needs_login=False`` → proxy failure, re-queue
        """
        from utils.url_helper import get_page_url as _get_page_url

        # 1) Search for exact video code match (first results page only)
        page_num = 1
        paged_url = _get_page_url(page_num, task.base_url, custom_url=task.search_url)
        html, _used_cf, needs_login = self._fetch_with_cf_fallback(
            paged_url, f"[{task.entry_index}] search p{page_num} {task.video_code}",
        )
        exact_entry = None
        if needs_login:
            return None, True
        if html:
            parsed = parse_index_page(html, page_num=page_num)
            if parsed.has_movie_list and parsed.movies:
                exact_entry = find_exact_video_code_match(parsed.movies, task.video_code)
        else:
            return None, False

        if exact_entry is None:
            return AlignResult(
                task=task,
                process_result=MissingProcessResult(
                    video_code=task.video_code,
                    status='search_miss',
                    message='exact_video_code_not_found',
                ),
                qb_rows=[], purge_plan_rows=[],
                db_upsert_kwargs=None, parse_success=True,
            ), False

        # 2) Fetch detail page
        detail_href = normalize_javdb_href_path(exact_entry.href)
        detail_url = urljoin(task.base_url + '/', detail_href.lstrip('/'))
        html, _used_cf, needs_login = self._fetch_with_cf_fallback(
            detail_url, f"[{task.entry_index}] detail {task.video_code}",
        )
        if needs_login:
            return None, True
        if not html:
            return None, False

        # 3) Parse detail page
        detail = parse_detail_page(html)
        magnets_payload = [m.to_dict() for m in detail.magnets]
        magnet_links = extract_magnets(magnets_payload, index=task.video_code)
        actor_name = detail.get_first_actor_name()
        actor_gender = detail.get_first_actor_gender()
        actor_link = detail.get_first_actor_href()
        supporting_actors = detail.get_supporting_actors_json()

        db_kwargs = _build_db_upsert_kwargs(
            detail_href, task.video_code, magnet_links,
            actor_name, actor_gender, actor_link, supporting_actors,
        )

        # 4) Compare ranks
        qb_rows: list[dict] = []
        purge_plan_rows: list[dict] = []
        parsed_best_cat = _best_parsed_category(magnet_links)
        parsed_best_rank = _parsed_category_rank(parsed_best_cat)
        inventory_best_rank = _best_inventory_rank(task.inventory_entries)

        chosen_upgrade = ''
        if parsed_best_cat and parsed_best_rank > inventory_best_rank:
            chosen_upgrade = parsed_best_cat
            qb_rows.append(_to_qb_row(detail_href, task.video_code, parsed_best_cat, magnet_links))
            purge_plan_rows.extend(_to_purge_plan_rows(
                task.video_code, task.inventory_entries, parsed_best_rank,
                parsed_best_cat,
            ))

        return AlignResult(
            task=task,
            process_result=MissingProcessResult(
                video_code=task.video_code, status='ok',
                href=detail_href, detail_href=detail_href,
                actor_name=actor_name,
                chosen_upgrade_category=chosen_upgrade,
            ),
            qb_rows=qb_rows, purge_plan_rows=purge_plan_rows,
            db_upsert_kwargs=db_kwargs, parse_success=True,
        ), False

    # -- login helpers (mirrored from BackfillWorker) ----------------------

    def _try_login_refresh(self) -> bool:
        global _align_logged_in_worker_id
        import scripts.spider.state as st
        from scripts.spider.session import attempt_login_refresh

        with _align_login_lock:
            if st.login_attempted:
                if st.refreshed_session_cookie is not None:
                    self._handler.config.javdb_session_cookie = st.refreshed_session_cookie
                    _align_logged_in_worker_id = self.worker_id
                    return True
                return False

            proxy_for_login = {
                k: v for k, v in {
                    'http': self.proxy_config.get('http'),
                    'https': self.proxy_config.get('https'),
                }.items() if v
            } or None

            success, new_cookie, _ = attempt_login_refresh(
                explicit_proxies=proxy_for_login,
                explicit_proxy_name=self.proxy_name,
            )
            if success and new_cookie:
                self._handler.config.javdb_session_cookie = new_cookie
                _align_logged_in_worker_id = self.worker_id
                return True
            return False

    def _handle_login_required(self, task: AlignTask):
        global _align_logged_in_worker_id
        import scripts.spider.state as st
        from scripts.spider.session import can_attempt_login

        with _align_login_lock:
            if _align_logged_in_worker_id is not None:
                if _align_logged_in_worker_id != self.worker_id:
                    logged_in_proxy = self.all_workers[_align_logged_in_worker_id].proxy_name
                    task.failed_proxies.discard(logged_in_proxy)
                    self.login_queue.put(task)
                    logger.info(
                        "[%s] [%s] Login required for %s, routing to [%s]",
                        self.proxy_name, task.entry_index, task.video_code, logged_in_proxy,
                    )
                    return
                logger.warning(
                    "[%s] [%s] Own session stale — requeueing %s",
                    self.proxy_name, task.entry_index, task.video_code,
                )
                _align_logged_in_worker_id = None
                st.refreshed_session_cookie = None
                st.logged_in_proxy_name = None
                task.failed_proxies.add(self.proxy_name)
                _requeue_front(self.task_queue, task)
                return

        if can_attempt_login(True, is_index_page=False):
            if self._try_login_refresh():
                logger.info("[%s] Logged in, becoming login worker", self.proxy_name)
                self.login_queue.put(task)
                return

        logger.warning("[%s] [%s] Login unavailable, marking failed", self.proxy_name, task.entry_index)
        self.result_queue.put(AlignResult(
            task=task,
            process_result=MissingProcessResult(
                video_code=task.video_code, status='login_required',
                message='login_unavailable',
            ),
            qb_rows=[], purge_plan_rows=[],
            db_upsert_kwargs=None, parse_success=False,
        ))

    def _get_next_task(self) -> AlignTask | None:
        while True:
            with _align_login_lock:
                am_logged_in = (_align_logged_in_worker_id == self.worker_id)

            if am_logged_in:
                try:
                    return self.login_queue.get_nowait()
                except queue_module.Empty:
                    pass

            try:
                return self.task_queue.get(timeout=0.3 if am_logged_in else None)
            except queue_module.Empty:
                continue

    # -- main loop ---------------------------------------------------------

    def run(self):
        while True:
            task = self._get_next_task()
            if task is None:
                break

            with self.completed_lock:
                if task.video_code in self.completed_codes:
                    self.result_queue.put(AlignResult(
                        task=task,
                        process_result=MissingProcessResult(
                            video_code=task.video_code, status='skipped',
                        ),
                        qb_rows=[], purge_plan_rows=[],
                        db_upsert_kwargs=None, parse_success=False, is_skipped=True,
                    ))
                    continue

            if self.proxy_name in task.failed_proxies:
                if len(task.failed_proxies) >= self.total_workers:
                    self.result_queue.put(AlignResult(
                        task=task,
                        process_result=MissingProcessResult(
                            video_code=task.video_code, status='all_proxies_failed',
                            message=f'failed on {len(task.failed_proxies)} proxies',
                        ),
                        qb_rows=[], purge_plan_rows=[],
                        db_upsert_kwargs=None, parse_success=False,
                    ))
                    continue
                _requeue_front(self.task_queue, task)
                time.sleep(0.1)
                continue

            if not self._first_request:
                if self._interruptible_movie_sleep():
                    self._orphan_current_task(task)
                    continue
            self._first_request = False

            if self._stop_event.is_set():
                self._orphan_current_task(task)
                continue

            result, needs_login = self._process_task(task)
            if result is not None:
                if result.parse_success and result.process_result.status == 'ok':
                    logger.info(
                        "[%s] Parsed %s [%s]",
                        task.entry_index, task.video_code, self.proxy_name,
                    )
                elif result.process_result.status == 'search_miss':
                    logger.info(
                        "[%s] No exact match for %s [%s]",
                        task.entry_index, task.video_code, self.proxy_name,
                    )
                self.result_queue.put(result)
            elif needs_login:
                self._handle_login_required(task)
            else:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                _requeue_front(self.task_queue, task)
                logger.info(
                    "[%s] [%s] Failed %s, re-queued (%d/%d proxies)",
                    self.proxy_name, task.entry_index, task.video_code,
                    len(task.failed_proxies), self.total_workers,
                )


# ---------------------------------------------------------------------------
# Alignment entry-point
# ---------------------------------------------------------------------------


def run_alignment(args: argparse.Namespace) -> int:
    global _align_logged_in_worker_id

    init_db(force=True)
    history = db_load_history()
    inventory = db_load_rclone_inventory()
    only_codes = []
    if args.codes:
        only_codes = [c.strip() for c in args.codes.split(',') if c.strip()]

    missing_codes = compute_missing_codes(inventory, history, only_codes=only_codes)
    if args.limit and args.limit > 0:
        missing_codes = missing_codes[: args.limit]

    total = len(missing_codes)
    logger.info("Missing movie codes to align: %d", total)
    if not missing_codes:
        return 0

    # Common network setup
    reports_dir = cfg('REPORTS_DIR', 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    ban_log = os.path.join(reports_dir, 'proxy_bans.csv')
    use_proxy = args.use_proxy
    spider_state.setup_proxy_pool(ban_log_file=ban_log, use_proxy=use_proxy)
    spider_state.initialize_request_handler()
    base_url = cfg('BASE_URL', 'https://javdb.com').rstrip('/')

    process_results: List[MissingProcessResult] = []
    qb_rows: List[dict] = []
    purge_plan_rows: List[dict] = []
    rc = 0

    from scripts.spider.config_loader import PROXY_POOL
    from scripts.spider.sleep_manager import movie_sleep_mgr

    # ------------------------------------------------------------------
    # Parallel mode: one AlignWorker per proxy
    # ------------------------------------------------------------------
    if use_proxy and PROXY_POOL:
        _align_logged_in_worker_id = None
        movie_sleep_mgr.apply_volume_multiplier(total)

        completed_codes: set[str] = set()
        completed_lock = threading.Lock()
        stop_event = threading.Event()
        shutdown_orphan_tasks: list[AlignTask] = []
        shutdown_orphan_lock = threading.Lock()

        task_queue: queue_module.Queue[AlignTask | None] = queue_module.Queue()
        result_queue: queue_module.Queue[AlignResult] = queue_module.Queue()
        login_queue: queue_module.Queue[AlignTask] = queue_module.Queue()

        all_workers: list[AlignWorker] = []
        for idx, proxy_cfg in enumerate(PROXY_POOL):
            w = AlignWorker(
                worker_id=idx,
                proxy_config=proxy_cfg,
                task_queue=task_queue,
                result_queue=result_queue,
                login_queue=login_queue,
                total_workers=len(PROXY_POOL),
                use_cookie=True,
                movie_sleep_min=movie_sleep_mgr.sleep_min,
                movie_sleep_max=movie_sleep_mgr.sleep_max,
                ban_log_file=ban_log,
                all_workers=all_workers,
                completed_codes=completed_codes,
                completed_lock=completed_lock,
                stop_event=stop_event,
                shutdown_orphan_tasks=shutdown_orphan_tasks,
                shutdown_orphan_lock=shutdown_orphan_lock,
            )
            all_workers.append(w)

        for i, code in enumerate(missing_codes, 1):
            task_queue.put(AlignTask(
                video_code=code,
                inventory_entries=inventory.get(code, []),
                search_url=build_search_url(code, f='all', base_url=base_url),
                base_url=base_url,
                entry_index=f"align-{i}/{total}",
            ))

        logger.info(
            "Starting %d workers for %d alignment tasks (search + detail per code)",
            len(all_workers), total,
        )
        for w in all_workers:
            w.start()

        processed = 0
        failed = 0
        skipped = 0
        results_received = 0
        parallel_interrupted = False

        try:
            while results_received < total:
                result: AlignResult = result_queue.get()
                results_received += 1
                p, f, s = _apply_one_parallel_align_result(
                    result,
                    completed_codes=completed_codes,
                    completed_lock=completed_lock,
                    dry_run=args.dry_run,
                    process_results=process_results,
                    qb_rows=qb_rows,
                    purge_plan_rows=purge_plan_rows,
                )
                processed += p
                failed += f
                skipped += s
        except KeyboardInterrupt:
            parallel_interrupted = True
            not_started: list[AlignTask] = []
            logger.warning(
                "Keyboard interrupt — signalling workers, draining queues …",
            )
            _parallel_align_signal_shutdown(
                stop_event=stop_event,
                task_queue=task_queue,
                login_queue=login_queue,
                num_workers=len(all_workers),
                not_started_tasks=not_started,
            )
            ep, ef, es = _drain_parallel_align_result_queue_on_interrupt(
                result_queue,
                completed_codes=completed_codes,
                completed_lock=completed_lock,
                dry_run=args.dry_run,
                process_results=process_results,
                qb_rows=qb_rows,
                purge_plan_rows=purge_plan_rows,
                phase="before worker shutdown",
            )
            processed += ep
            failed += ef
            skipped += es

            for w in all_workers:
                w.join(timeout=30)

            ep2, ef2, es2 = _drain_parallel_align_result_queue_on_interrupt(
                result_queue,
                completed_codes=completed_codes,
                completed_lock=completed_lock,
                dry_run=args.dry_run,
                process_results=process_results,
                qb_rows=qb_rows,
                purge_plan_rows=purge_plan_rows,
                phase="after worker shutdown",
            )
            processed += ep2
            failed += ef2
            skipped += es2

            with shutdown_orphan_lock:
                n_orphan = len(shutdown_orphan_tasks)
            n_queued = len(not_started)
            logger.info(
                "Alignment interrupted (parallel, %d workers). "
                "Processed: %d, Skipped: %d, Failed: %d — "
                "%d tasks reclaimed, %d released mid-worker",
                len(all_workers), processed, skipped, failed, n_queued, n_orphan,
            )
        else:
            for _ in all_workers:
                task_queue.put(None)
            for w in all_workers:
                w.join(timeout=10)

            logger.info(
                "Alignment done (parallel, %d workers). "
                "Processed: %d, Skipped: %d, Failed: %d",
                len(all_workers), processed, skipped, failed,
            )

        if parallel_interrupted:
            rc = 130

    else:
        # ------------------------------------------------------------------
        # Sequential fallback (no proxy or no PROXY_POOL configured)
        # ------------------------------------------------------------------
        session = requests.Session()

        for idx, code in enumerate(missing_codes, 1):
            logger.info("[%d/%d] Processing %s", idx, total, code)
            search_url = build_search_url(code, f='all', base_url=base_url)
            page_num = 1
            paged_url = get_page_url(page_num, custom_url=search_url)
            search_html = _fetch_html(session, paged_url, use_proxy=use_proxy)
            exact_entry = None
            if search_html:
                parsed = parse_index_page(search_html, page_num=page_num)
                if parsed.has_movie_list and parsed.movies:
                    exact_entry = find_exact_video_code_match(parsed.movies, code)

            if exact_entry is None:
                process_results.append(
                    MissingProcessResult(
                        video_code=code,
                        status='search_miss',
                        message='exact_video_code_not_found',
                    )
                )
                continue

            detail_href = normalize_javdb_href_path(exact_entry.href)
            detail_url = urljoin(base_url + '/', detail_href.lstrip('/'))
            detail_html = _fetch_html(session, detail_url, use_proxy=use_proxy)
            if not detail_html:
                process_results.append(
                    MissingProcessResult(
                        video_code=code,
                        status='detail_fetch_failed',
                        href=detail_href,
                        detail_href=detail_href,
                    )
                )
                continue

            detail = parse_detail_page(detail_html)
            magnets_payload = [m.to_dict() for m in detail.magnets]
            magnet_links = extract_magnets(magnets_payload, index=code)
            actor_name = detail.get_first_actor_name()
            actor_gender = detail.get_first_actor_gender()
            actor_link = detail.get_first_actor_href()
            supporting_actors = detail.get_supporting_actors_json()

            if not args.dry_run:
                db_upsert_history(**_build_db_upsert_kwargs(
                    detail_href, code, magnet_links,
                    actor_name, actor_gender, actor_link, supporting_actors,
                ))

            parsed_best_cat = _best_parsed_category(magnet_links)
            parsed_best_rank = _parsed_category_rank(parsed_best_cat)
            inventory_entries = inventory.get(code, [])
            inventory_best_rank = _best_inventory_rank(inventory_entries)

            if parsed_best_cat and parsed_best_rank > inventory_best_rank:
                qb_rows.append(_to_qb_row(detail_href, code, parsed_best_cat, magnet_links))
                purge_plan_rows.extend(
                    _to_purge_plan_rows(
                        code,
                        inventory_entries,
                        parsed_best_rank,
                        parsed_best_cat,
                    )
                )

            process_results.append(
                MissingProcessResult(
                    video_code=code,
                    status='ok',
                    href=detail_href,
                    detail_href=detail_href,
                    actor_name=actor_name,
                    chosen_upgrade_category=parsed_best_cat,
                )
            )

    # ------------------------------------------------------------------
    # Write outputs (common for both paths)
    # ------------------------------------------------------------------
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = ensure_dated_dir(args.output_dir)
    process_csv = os.path.join(out_dir, f'InventoryHistoryAlign_Result_{timestamp}.csv')
    qb_csv = os.path.join(out_dir, f'InventoryHistoryAlign_QBUpgrade_{timestamp}.csv')
    purge_plan_csv = os.path.join(out_dir, f'InventoryHistoryAlign_PurgePlan_{timestamp}.csv')
    summary_json = os.path.join(out_dir, f'InventoryHistoryAlign_Summary_{timestamp}.json')

    _write_csv(
        process_csv,
        ['video_code', 'status', 'href', 'detail_href', 'actor_name', 'chosen_upgrade_category', 'message'],
        [r.__dict__ for r in process_results],
    )
    _write_csv(qb_csv, _QB_FIELDNAMES, qb_rows)
    _write_csv(purge_plan_csv, _PURGE_PLAN_FIELDNAMES, purge_plan_rows)

    summary = {
        'missing_codes_total': len(missing_codes),
        'processed_ok': sum(1 for r in process_results if r.status == 'ok'),
        'search_miss': sum(1 for r in process_results if r.status == 'search_miss'),
        'detail_fetch_failed': sum(1 for r in process_results if r.status == 'detail_fetch_failed'),
        'qb_upgrade_rows': len(qb_rows),
        'purge_plan_rows': len(purge_plan_rows),
        'dry_run': args.dry_run,
        'files': {
            'result_csv': process_csv,
            'qb_upgrade_csv': qb_csv,
            'purge_plan_csv': purge_plan_csv,
        },
    }
    with open(summary_json, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info("Alignment summary saved: %s", summary_json)
    logger.info("Result CSV: %s", process_csv)
    logger.info("qB Upgrade CSV: %s", qb_csv)
    logger.info("Purge-plan CSV: %s", purge_plan_csv)

    if rc != 0:
        return rc

    if args.enqueue_qb and qb_rows:
        qb_ok = _enqueue_qb_from_csv(qb_csv, use_proxy=use_proxy, category_override=args.qb_category)
        if not qb_ok:
            logger.error("qB enqueue failed")
            return 1

    if args.execute_delete and purge_plan_rows:
        from scripts.rclone_manager import run_execute_inventory_purge_from_csv

        sdrc = run_execute_inventory_purge_from_csv(
            purge_plan_csv,
            dry_run=args.dry_run,
        )
        if sdrc != 0:
            logger.error("Rclone purge execution failed")
            return sdrc

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Align inventory-only movie codes into MovieHistory with JavDB search/detail enrichment.',
    )
    parser.add_argument('--dry-run', action='store_true', help='Parse and plan only; do not write DB.')
    parser.add_argument('--limit', type=int, default=0, help='Max number of missing codes to process (0=all).')
    parser.add_argument('--codes', type=str, default='', help='Comma-separated codes to process.')
    parser.add_argument('--use-proxy', action='store_true', help='Use spider proxy configuration.')
    parser.add_argument('--output-dir', type=str, default=cfg('MIGRATION_REPORT_DIR', 'reports/Migration'))
    parser.add_argument('--enqueue-qb', action='store_true', help='Enqueue upgrade magnets to qBittorrent.')
    parser.add_argument('--qb-category', type=str, default=cfg('TORRENT_CATEGORY_ADHOC', 'Ad Hoc'))
    parser.add_argument(
        '--execute-delete',
        action='store_true',
        help='After planning, run rclone purge on each source_path in the purge-plan CSV (destructive).',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_alignment(args)


if __name__ == '__main__':
    raise SystemExit(main())
