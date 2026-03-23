"""Parallel detail-page processing with one worker per proxy."""

import random
import time
import threading
import queue as queue_module
from dataclasses import dataclass, field
from typing import Optional, List
from urllib.parse import urljoin

from utils.logging_config import get_logger
from utils.config_helper import use_sqlite
from utils.parser import parse_detail
from utils.magnet_extractor import extract_magnets
from utils.db import db_batch_update_movie_actors
from utils.history_manager import (
    has_complete_subtitles, should_skip_recent_yesterday_release,
    should_skip_recent_today_release, should_process_movie,
    save_parsed_movie_to_history, batch_update_last_visited,
    check_redownload_upgrade,
)
from utils.csv_writer import write_csv
from utils.proxy_pool import create_proxy_pool_from_config
from utils.request_handler import RequestHandler, RequestConfig

import scripts.spider.state as state
from scripts.spider.session import is_login_page
from scripts.spider.parallel_login import LoginCoordinator, requeue_front as _requeue_front
from scripts.spider.sleep_manager import (
    MovieSleepManager, movie_sleep_mgr,
    penalty_tracker as _shared_penalty_tracker,
    dual_window_throttle as _shared_throttle,
    PenaltyTracker, DualWindowThrottle,
)
from scripts.spider.csv_builder import (
    create_csv_row_with_history_filter, check_torrent_status, collect_new_magnet_links,
    create_redownload_row,
)
from scripts.spider.config_loader import (
    BASE_URL,
    CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED,
    CF_BYPASS_PORT_MAP,
    CF_TURNSTILE_COOLDOWN, FALLBACK_COOLDOWN,
    JAVDB_SESSION_COOKIE,
    PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES,
    LOGIN_PROXY_NAME,
)
from scripts.spider.dedup_checker import (
    should_skip_from_rclone,
    check_dedup_upgrade,
    append_dedup_record,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DetailTask:
    """A detail page to be fetched by a worker thread."""
    url: str
    entry: dict
    phase: int
    entry_index: str
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)


@dataclass
class DetailResult:
    """Result produced by a worker after processing a DetailTask."""
    task: DetailTask
    magnets: list
    actor_info: str
    actor_gender: str
    actor_link: str
    supporting_actors: str
    parse_success: bool
    used_cf_bypass: bool


# ---------------------------------------------------------------------------
# ProxyWorker
# ---------------------------------------------------------------------------


class ProxyWorker(threading.Thread):
    """Worker thread bound to a single proxy (ARM server + local CF bypass)."""

    def __init__(
        self,
        worker_id: int,
        proxy_config: dict,
        detail_queue: 'queue_module.Queue[Optional[DetailTask]]',
        result_queue: 'queue_module.Queue[DetailResult]',
        login_queue: 'queue_module.Queue[DetailTask]',
        total_workers: int,
        use_cookie: bool,
        is_adhoc_mode: bool,
        movie_sleep_min: float,
        movie_sleep_max: float,
        fallback_cooldown: float,
        ban_log_file: str,
        all_workers: list,
        coordinator: LoginCoordinator,
        shared_penalty_tracker: PenaltyTracker = None,
        shared_throttle: DualWindowThrottle = None,
    ):
        super().__init__(daemon=True, name=f"ProxyWorker-{proxy_config.get('name', worker_id)}")
        self.worker_id = worker_id
        self.proxy_config = proxy_config
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self.detail_queue = detail_queue
        self.result_queue = result_queue
        self.login_queue = login_queue
        self.total_workers = total_workers
        self.use_cookie = use_cookie
        self.is_adhoc_mode = is_adhoc_mode
        self._sleep_mgr = MovieSleepManager(
            movie_sleep_min, movie_sleep_max,
            penalty_tracker=shared_penalty_tracker,
            throttle=shared_throttle,
        )
        self.fallback_cooldown = fallback_cooldown
        self.all_workers = all_workers
        self._coordinator = coordinator

        self._cf_bypass_since: Optional[float] = None
        self._first_request = True
        self._startup_jitter = random.uniform(0.5, 2.0) + worker_id * random.uniform(1.5, 3.0)

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
                cf_bypass_port_map=CF_BYPASS_PORT_MAP,
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
            penalty_tracker=shared_penalty_tracker,
        )

    # -- internal helpers --------------------------------------------------

    def _fetch_html(self, url: str, use_cf: bool) -> Optional[str]:
        return self._handler.get_page(
            url,
            use_cookie=self.use_cookie,
            use_proxy=True,
            module_name='spider',
            max_retries=1,
            use_cf_bypass=use_cf,
        )

    def _try_fetch_and_parse(self, task: DetailTask, use_cf: bool, context: str):
        """Attempt fetch + parse_detail.

        Returns (magnets, actor_info, actor_gender, actor_link, supporting, success, needs_login).
        Login decisions are made at the run() level, not here.
        """
        logger.debug(f"[{self.proxy_name}] [{task.entry_index}] {context}")
        try:
            html = self._fetch_html(task.url, use_cf)
            if html:
                if is_login_page(html):
                    logger.warning(
                        f"[{self.proxy_name}] [{task.entry_index}] Login page detected: {context}")
                    return [], '', '', '', '', False, True

                m = parse_detail(html, task.entry_index, skip_sleep=True)
                magnets, actor_info, actor_gender, actor_link, supporting, ok = m
                if ok:
                    return magnets, actor_info, actor_gender, actor_link, supporting, True, False
                logger.debug(f"[{self.proxy_name}] [{task.entry_index}] parse failed: {context}")
            else:
                logger.debug(f"[{self.proxy_name}] [{task.entry_index}] no HTML: {context}")
        except Exception as e:
            logger.debug(f"[{self.proxy_name}] [{task.entry_index}] error in {context}: {e}")
        return [], '', '', '', '', False, False

    def _try_direct_then_cf(self, task: DetailTask):
        """Try direct, then CF bypass.

        Returns (magnets, actor_info, actor_gender, actor_link, supporting, success, used_cf, needs_login).
        Short-circuits CF bypass if login is required (CF won't fix auth).
        """
        always_bypass_time = state.always_bypass_time
        should_short_circuit = False
        if always_bypass_time is not None and self._cf_bypass_since is not None:
            if always_bypass_time == 0:
                should_short_circuit = True
            else:
                window_seconds = always_bypass_time * 60
                if time.time() - self._cf_bypass_since <= window_seconds:
                    should_short_circuit = True
                else:
                    self._cf_bypass_since = None

        if should_short_circuit:
            m, a, ag, al, sup, ok, needs_login = self._try_fetch_and_parse(
                task, True, "CF Bypass (marked)")
            return m, a, ag, al, sup, ok, True, needs_login

        m, a, ag, al, sup, ok, needs_login = self._try_fetch_and_parse(task, False, "Direct")
        if ok:
            return m, a, ag, al, sup, True, False, False
        if needs_login:
            return m, a, ag, al, sup, False, False, True

        m, a, ag, al, sup, ok, needs_login = self._try_fetch_and_parse(task, True, "CF Bypass")
        if ok:
            if always_bypass_time is not None:
                self._cf_bypass_since = time.time()
                if always_bypass_time == 0:
                    logger.info(f"[{self.proxy_name}] CF Bypass succeeded — marking proxy for this runtime")
                else:
                    logger.info(
                        f"[{self.proxy_name}] CF Bypass succeeded — marking proxy for {always_bypass_time} minute(s)"
                    )
            return m, a, ag, al, sup, True, True, False
        return [], '', '', '', '', False, False, needs_login

    def _handle_login_required(self, task: DetailTask):
        """Delegate to the shared LoginCoordinator."""
        self._coordinator.handle_login_required(
            worker=self,
            task=task,
            video_code=task.entry.get('video_code', ''),
            login_queue=self.login_queue,
            task_queue=self.detail_queue,
        )

    def _get_next_task(self) -> Optional[DetailTask]:
        """Get next task. Logged-in worker checks login_queue with priority."""
        while True:
            with self._coordinator.lock:
                am_logged_in = self._coordinator.is_login_worker(
                    self.proxy_name, self.worker_id,
                )

            if am_logged_in:
                try:
                    return self.login_queue.get_nowait()
                except queue_module.Empty:
                    pass

            try:
                task = self.detail_queue.get(timeout=0.3 if am_logged_in else None)
                return task
            except queue_module.Empty:
                continue

    # -- main loop ---------------------------------------------------------

    def run(self):
        while True:
            task = self._get_next_task()
            if task is None:
                break

            if self.proxy_name in task.failed_proxies:
                if len(task.failed_proxies) >= self.total_workers:
                    self.result_queue.put(DetailResult(
                        task=task, magnets=[], actor_info='', actor_gender='', actor_link='',
                        supporting_actors='', parse_success=False, used_cf_bypass=False,
                    ))
                    continue
                _requeue_front(self.detail_queue, task)
                backoff = min(2.0, 0.3 * len(task.failed_proxies))
                time.sleep(backoff)
                continue

            if self._first_request:
                logger.debug(
                    "[%s] Startup jitter: %.1fs", self.proxy_name, self._startup_jitter,
                )
                time.sleep(self._startup_jitter)
                self._first_request = False
            else:
                self._sleep_mgr.sleep()

            magnets, actor_info, actor_gender, actor_link, supporting, success, used_cf, needs_login = (
                self._try_direct_then_cf(task))
            if success:
                cf_tag = " +CF" if used_cf else ""
                logger.info(
                    f"[{task.entry_index}] "
                    f"Parsed {task.entry.get('video_code', '')}{cf_tag} "
                    f"[{self.proxy_name}]"
                )
                self.result_queue.put(DetailResult(
                    task=task, magnets=magnets, actor_info=actor_info,
                    actor_gender=actor_gender or '', actor_link=actor_link or '',
                    supporting_actors=supporting or '', parse_success=True, used_cf_bypass=used_cf,
                ))
            elif needs_login:
                self._handle_login_required(task)
            else:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                _requeue_front(self.detail_queue, task)
                logger.info(
                    f"[{self.proxy_name}] [{task.entry_index}] "
                    f"Failed {task.entry.get('video_code', '')}, re-queued "
                    f"(tried {len(task.failed_proxies)}/{self.total_workers} proxies)"
                )

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def process_detail_entries_parallel(
    entries: List[dict],
    phase: int,
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    use_cookie: bool,
    is_adhoc_mode: bool,
    ban_log_file: str,
    rclone_inventory: dict = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
) -> dict:
    """Process detail entries in parallel using one worker per proxy.

    Returns a dict with statistics keys:
        rows, skipped_history, failed, failed_movies, no_new_torrents
    """
    total_entries = len(entries)

    detail_queue: queue_module.Queue[Optional[DetailTask]] = queue_module.Queue()
    result_queue: queue_module.Queue[DetailResult] = queue_module.Queue()
    login_queue: queue_module.Queue[DetailTask] = queue_module.Queue()

    all_workers: List[ProxyWorker] = []
    coordinator = LoginCoordinator(
        all_workers=all_workers, login_proxy_name=LOGIN_PROXY_NAME,
    )

    for idx, proxy_cfg in enumerate(PROXY_POOL):
        w = ProxyWorker(
            worker_id=idx,
            proxy_config=proxy_cfg,
            detail_queue=detail_queue,
            result_queue=result_queue,
            login_queue=login_queue,
            total_workers=len(PROXY_POOL),
            use_cookie=use_cookie,
            is_adhoc_mode=is_adhoc_mode,
            movie_sleep_min=movie_sleep_mgr.sleep_min,
            movie_sleep_max=movie_sleep_mgr.sleep_max,
            fallback_cooldown=FALLBACK_COOLDOWN,
            ban_log_file=ban_log_file,
            all_workers=all_workers,
            coordinator=coordinator,
            shared_penalty_tracker=_shared_penalty_tracker,
            shared_throttle=_shared_throttle,
        )
        all_workers.append(w)

    if state.logged_in_proxy_name and state.refreshed_session_cookie:
        if LOGIN_PROXY_NAME and state.logged_in_proxy_name != LOGIN_PROXY_NAME:
            logger.warning(
                f"Index login proxy [{state.logged_in_proxy_name}] differs from "
                f"LOGIN_PROXY_NAME [{LOGIN_PROXY_NAME}] — session may not match detail workers"
            )
        for w in all_workers:
            if w.proxy_name == state.logged_in_proxy_name:
                w._handler.config.javdb_session_cookie = state.refreshed_session_cookie
                coordinator.logged_in_worker_id = w.worker_id
                logger.info(
                    f"Index page login inherited: worker [{w.proxy_name}] "
                    f"set as the logged-in worker for login-required pages"
                )
                break
        if coordinator.logged_in_worker_id is None:
            logger.warning(
                f"Index page logged in via [{state.logged_in_proxy_name}] "
                f"but no matching parallel worker found"
            )

    tasks_submitted = 0
    local_parsed_links: set = set()

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        if href in state.parsed_links or href in local_parsed_links:
            continue
        local_parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            skip_complete = True
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (should_skip_recent_today_release(href, history_data, is_today)
                        or should_skip_recent_yesterday_release(href, history_data, is_yesterday)):
                    skip_complete = False
                    logger.debug(
                        f"[{i}/{total_entries}] [Page {entry['page']}] "
                        f"{entry['video_code']} has complete subtitles but re-download check enabled"
                    )
            if skip_complete:
                logger.info(
                    f"[{i}/{total_entries}] [Page {entry['page']}] "
                    f"Skipping {entry['video_code']} — already has subtitle and hacked_subtitle in history"
                )
                continue

        if rclone_filter and rclone_inventory and should_skip_from_rclone(entry.get('video_code', ''), rclone_inventory, enable_dedup):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — already exists in rclone inventory with 中字"
            )
            continue

        if not is_adhoc_mode and should_skip_recent_yesterday_release(
            href, history_data, entry.get('is_yesterday_release', False)
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — yesterday release, recently updated in history"
            )
            continue

        if not is_adhoc_mode and should_skip_recent_today_release(
            href, history_data, entry.get('is_today_release', False)
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — today release, already visited today"
            )
            continue

        detail_url = urljoin(BASE_URL, href)
        entry_index = f"{i}/{total_entries}"
        logger.debug(f"[{entry_index}] [Page {entry['page']}] Queued {entry['video_code'] or href}")
        detail_queue.put(DetailTask(
            url=detail_url,
            entry=entry,
            phase=phase,
            entry_index=entry_index,
        ))
        tasks_submitted += 1

    state.parsed_links.update(local_parsed_links)

    skipped_history = len(local_parsed_links) - tasks_submitted

    if tasks_submitted == 0:
        logger.info(f"Phase {phase}: No detail tasks to process (all filtered)")
        return {'rows': [], 'skipped_history': skipped_history, 'failed': 0, 'failed_movies': [], 'no_new_torrents': 0}

    logger.info(
        f"Phase {phase}: Starting {len(all_workers)} workers for {tasks_submitted} detail tasks "
        f"({skipped_history} skipped by history)"
    )
    for w in all_workers:
        w.start()

    rows: list = []
    phase_rows: list = []
    visited_hrefs: set = set()
    actor_updates: List[tuple] = []
    failed = 0
    failed_movies: list = []
    no_new_torrents = 0
    results_received = 0

    while results_received < tasks_submitted:
        result: DetailResult = result_queue.get()
        results_received += 1
        task = result.task
        entry = task.entry
        href = entry['href']
        page_num = entry['page']
        idx_str = task.entry_index

        if not result.parse_success:
            detail_url = urljoin(BASE_URL, href)
            logger.error(f"[{idx_str}] [Page {page_num}] Failed: {entry.get('video_code', '?')} ({detail_url})")
            failed += 1
            failed_movies.append({'video_code': entry.get('video_code', '?'), 'url': detail_url, 'phase': phase})
            continue

        visited_hrefs.add(href)
        actor_updates.append((
            href, result.actor_info or '', result.actor_gender or '',
            result.actor_link or '', result.supporting_actors or '',
        ))
        magnet_links = extract_magnets(result.magnets, idx_str)

        should_process, history_torrent_types = should_process_movie(
            href, history_data, phase, magnet_links,
        )

        redownload_cats = []
        if not should_process:
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (should_skip_recent_today_release(href, history_data, is_today)
                        or should_skip_recent_yesterday_release(href, history_data, is_yesterday)):
                    redownload_cats = check_redownload_upgrade(
                        href, history_data, magnet_links, redownload_threshold,
                    )
            if not redownload_cats:
                skipped_history += 1
                continue

        # Dedup upgrade detection against rclone inventory
        if enable_dedup and rclone_inventory and entry.get('video_code'):
            vc = entry['video_code'].upper()
            rclone_entries = rclone_inventory.get(vc, [])
            if rclone_entries:
                torrent_types = {
                    'subtitle': bool(magnet_links.get('subtitle')),
                    'hacked_subtitle': bool(magnet_links.get('hacked_subtitle')),
                    'hacked_no_subtitle': bool(magnet_links.get('hacked_no_subtitle')),
                    'no_subtitle': bool(magnet_links.get('no_subtitle')),
                }
                dedup_records = check_dedup_upgrade(vc, torrent_types, rclone_entries)
                for rec in dedup_records:
                    if not dry_run and dedup_csv_path:
                        append_dedup_record(dedup_csv_path, rec)
                    logger.info(f"[{idx_str}] DEDUP: {rec.video_code} – {rec.deletion_reason}")

        if redownload_cats:
            row = create_redownload_row(
                href, entry, page_num, result.actor_info, magnet_links, redownload_cats)
        else:
            row = create_csv_row_with_history_filter(
                href, entry, page_num, result.actor_info, magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            rows.append(row)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links, new_sizes, new_fc, new_res = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(
                        history_file, href, phase, entry['video_code'],
                        new_magnet_links, size_links=new_sizes,
                        file_count_links=new_fc, resolution_links=new_res,
                        actor_name=result.actor_info or '',
                        actor_gender=result.actor_gender or '',
                        actor_link=result.actor_link or '',
                        supporting_actors=result.supporting_actors or '',
                    )
        else:
            no_new_torrents += 1

    for _ in all_workers:
        detail_queue.put(None)
    for w in all_workers:
        w.join(timeout=10)

    if use_history_for_saving and not dry_run and visited_hrefs:
        if use_sqlite() and actor_updates:
            db_batch_update_movie_actors(actor_updates)
        batch_update_last_visited(history_file, visited_hrefs)

    logger.info(
        f"Phase {phase} completed: {total_entries} movies discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'failed_movies': failed_movies,
        'no_new_torrents': no_new_torrents,
    }