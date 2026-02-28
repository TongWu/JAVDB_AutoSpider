"""Parallel detail-page processing with one worker per proxy."""

import time
import threading
import queue as queue_module
from dataclasses import dataclass, field
from typing import Optional, List
from urllib.parse import urljoin

from utils.logging_config import get_logger
from utils.parser import parse_detail
from utils.magnet_extractor import extract_magnets
from utils.history_manager import has_complete_subtitles, should_skip_recent_yesterday_release, should_process_movie, save_parsed_movie_to_history, batch_update_last_visited
from utils.csv_writer import write_csv
from utils.proxy_pool import create_proxy_pool_from_config
from utils.request_handler import RequestHandler, RequestConfig

import scripts.spider.state as state
from scripts.spider.session import is_login_page, can_attempt_login, attempt_login_refresh
from scripts.spider.sleep_manager import MovieSleepManager
from scripts.spider.csv_builder import (
    create_csv_row_with_history_filter, check_torrent_status, collect_new_magnet_links,
)
from scripts.spider.config_loader import (
    BASE_URL,
    CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED,
    CF_TURNSTILE_COOLDOWN, FALLBACK_COOLDOWN,
    JAVDB_SESSION_COOKIE,
    MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX,
    PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES,
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
    parse_success: bool
    used_cf_bypass: bool


# Lock protecting login_attempted / refreshed_session_cookie across workers
_login_lock = threading.Lock()

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
        total_workers: int,
        use_cookie: bool,
        is_adhoc_mode: bool,
        movie_sleep_min: float,
        movie_sleep_max: float,
        fallback_cooldown: float,
        ban_log_file: str,
        all_workers: list,
    ):
        super().__init__(daemon=True, name=f"ProxyWorker-{proxy_config.get('name', worker_id)}")
        self.worker_id = worker_id
        self.proxy_config = proxy_config
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self.detail_queue = detail_queue
        self.result_queue = result_queue
        self.total_workers = total_workers
        self.use_cookie = use_cookie
        self.is_adhoc_mode = is_adhoc_mode
        self._sleep_mgr = MovieSleepManager(movie_sleep_min, movie_sleep_max)
        self.fallback_cooldown = fallback_cooldown
        self.all_workers = all_workers

        self.needs_cf_bypass = False
        self._first_request = True

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
        """Attempt fetch + parse_detail; returns (magnets, actor, success)."""
        logger.debug(f"[{self.proxy_name}] [{task.entry_index}] {context}")
        try:
            html = self._fetch_html(task.url, use_cf)
            if html:
                if is_login_page(html):
                    logger.warning(
                        f"[{self.proxy_name}] [{task.entry_index}] Login page detected: {context}")
                    if can_attempt_login(self.is_adhoc_mode, is_index_page=False):
                        if self._try_login_refresh():
                            html = self._fetch_html(task.url, use_cf)
                            if html and not is_login_page(html):
                                magnets, actor_info, ok = parse_detail(
                                    html, task.entry_index, skip_sleep=True)
                                if ok:
                                    logger.info(
                                        f"[{self.proxy_name}] [{task.entry_index}] "
                                        f"Login refresh succeeded: {context}")
                                    return magnets, actor_info, True
                            else:
                                logger.warning(
                                    f"[{self.proxy_name}] [{task.entry_index}] "
                                    f"Still login page after refresh")
                    return [], '', False

                magnets, actor_info, ok = parse_detail(html, task.entry_index, skip_sleep=True)
                if ok:
                    return magnets, actor_info, True
                logger.debug(f"[{self.proxy_name}] [{task.entry_index}] parse failed: {context}")
            else:
                logger.debug(f"[{self.proxy_name}] [{task.entry_index}] no HTML: {context}")
        except Exception as e:
            logger.debug(f"[{self.proxy_name}] [{task.entry_index}] error in {context}: {e}")
        return [], '', False

    def _try_direct_then_cf(self, task: DetailTask):
        """Try direct, then CF bypass. Returns (magnets, actor, success, used_cf)."""
        if self.needs_cf_bypass:
            m, a, ok = self._try_fetch_and_parse(task, True, "CF Bypass (marked)")
            return m, a, ok, True

        m, a, ok = self._try_fetch_and_parse(task, False, "Direct")
        if ok:
            return m, a, True, False

        m, a, ok = self._try_fetch_and_parse(task, True, "CF Bypass")
        if ok:
            self.needs_cf_bypass = True
            logger.info(f"[{self.proxy_name}] CF Bypass succeeded — marking proxy for this runtime")
            return m, a, True, True
        return [], '', False, False

    def _try_login_refresh(self):
        """Thread-safe global login; returns True on success."""
        with _login_lock:
            if state.login_attempted:
                return state.refreshed_session_cookie is not None
            success, new_cookie = attempt_login_refresh()
            if success and new_cookie:
                for w in self.all_workers:
                    w._handler.config.javdb_session_cookie = new_cookie
                return True
            return False

    # -- main loop ---------------------------------------------------------

    def run(self):
        while True:
            task = self.detail_queue.get()
            if task is None:
                break

            if self.proxy_name in task.failed_proxies:
                if len(task.failed_proxies) >= self.total_workers:
                    if can_attempt_login(self.is_adhoc_mode, is_index_page=False):
                        if self._try_login_refresh():
                            task.failed_proxies.clear()
                            task.retry_count += 1
                            self.detail_queue.put(task)
                            continue
                    self.result_queue.put(DetailResult(
                        task=task, magnets=[], actor_info='',
                        parse_success=False, used_cf_bypass=False,
                    ))
                    continue
                self.detail_queue.put(task)
                time.sleep(0.1)
                continue

            if not self._first_request:
                self._sleep_mgr.sleep()
            self._first_request = False

            magnets, actor_info, success, used_cf = self._try_direct_then_cf(task)
            if success:
                cf_tag = " +CF" if used_cf else ""
                logger.info(
                    f"[{task.entry_index}] "
                    f"Parsed {task.entry.get('video_code', '')}{cf_tag} "
                    f"[{self.proxy_name}]"
                )
                self.result_queue.put(DetailResult(
                    task=task, magnets=magnets, actor_info=actor_info,
                    parse_success=True, used_cf_bypass=used_cf,
                ))
            else:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                self.detail_queue.put(task)
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
) -> dict:
    """Process detail entries in parallel using one worker per proxy.

    Returns a dict with statistics keys:
        rows, skipped_history, failed, no_new_torrents
    """
    total_entries = len(entries)

    detail_queue: queue_module.Queue[Optional[DetailTask]] = queue_module.Queue()
    result_queue: queue_module.Queue[DetailResult] = queue_module.Queue()

    all_workers: List[ProxyWorker] = []
    for idx, proxy_cfg in enumerate(PROXY_POOL):
        w = ProxyWorker(
            worker_id=idx,
            proxy_config=proxy_cfg,
            detail_queue=detail_queue,
            result_queue=result_queue,
            total_workers=len(PROXY_POOL),
            use_cookie=use_cookie,
            is_adhoc_mode=is_adhoc_mode,
            movie_sleep_min=MOVIE_SLEEP_MIN,
            movie_sleep_max=MOVIE_SLEEP_MAX,
            fallback_cooldown=FALLBACK_COOLDOWN,
            ban_log_file=ban_log_file,
            all_workers=all_workers,
        )
        all_workers.append(w)

    tasks_submitted = 0
    local_parsed_links: set = set()

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        if href in state.parsed_links or href in local_parsed_links:
            continue
        local_parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — already has subtitle and hacked_subtitle in history"
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
        return {'rows': [], 'skipped_history': skipped_history, 'failed': 0, 'no_new_torrents': 0}

    if len(all_workers) == 0:
        logger.warning(
            "PROXY_POOL is empty; cannot run parallel detail workers. No tasks will be processed."
        )
        return {
            'rows': [],
            'skipped_history': skipped_history,
            'failed': tasks_submitted,
            'no_new_torrents': 0,
        }

    logger.info(
        f"Phase {phase}: Starting {len(all_workers)} workers for {tasks_submitted} detail tasks "
        f"({skipped_history} skipped by history)"
    )
    for w in all_workers:
        w.start()

    rows: list = []
    phase_rows: list = []
    visited_hrefs: set = set()
    failed = 0
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
            logger.error(f"[{idx_str}] [Page {page_num}] Failed after all workers exhausted")
            failed += 1
            continue

        visited_hrefs.add(href)
        magnet_links = extract_magnets(result.magnets, idx_str)

        should_process, history_torrent_types = should_process_movie(
            href, history_data, phase, magnet_links,
        )
        if not should_process:
            skipped_history += 1
            continue

        row = create_csv_row_with_history_filter(href, entry, page_num, '', magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            rows.append(row)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(
                        history_file, href, phase, entry['video_code'], new_magnet_links,
                    )
        else:
            no_new_torrents += 1

    for _ in all_workers:
        detail_queue.put(None)
    for w in all_workers:
        w.join(timeout=10)

    if use_history_for_saving and not dry_run and visited_hrefs:
        batch_update_last_visited(history_file, visited_hrefs)

    logger.info(
        f"Phase {phase} parallel completed: {total_entries} discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'no_new_torrents': no_new_torrents,
    }
