"""Reusable parallel fetch engine with proxy workers, login coordination,
CF bypass fallback, and adaptive sleep management.

Provides a single ``FetchEngine`` class that external scripts (spider main,
migration backfill, inventory alignment, …) can use to process arbitrary
detail-page URLs with the full spider infrastructure: multi-proxy workers,
automatic login retry, CF bypass cascade, and adaptive sleep/throttle.

Usage (simple mode — single-URL fetch + user-defined parse)::

    from packages.python.javdb_spider.fetch.fetch_engine import FetchEngine, EngineTask

    def my_parse(html: str, task: EngineTask) -> dict | None:
        ...  # return parsed data or None on failure
        return {'key': 'value'}

    engine = FetchEngine.simple(parse_fn=my_parse, use_cookie=True)
    engine.start()
    engine.submit('https://javdb.com/v/abc', meta={'code': 'ABC-123'})
    engine.mark_done()
    for result in engine.results():
        print(result.data)
    engine.shutdown()

Usage (advanced mode — multi-step fetch inside process_fn)::

    from packages.python.javdb_spider.fetch.fetch_engine import FetchEngine, WorkerContext, EngineTask

    def my_process(ctx: WorkerContext, task: EngineTask) -> dict | None:
        html = ctx.fetch(task.url)      # raises LoginRequired on auth wall
        detail_html = ctx.fetch(other)  # can call fetch() multiple times
        return {'result': ...}

    engine = FetchEngine(process_fn=my_process, use_cookie=True)
    engine.start()
    ...
"""

from __future__ import annotations

import queue as queue_module
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.proxy_ban_manager import get_ban_manager
from packages.python.javdb_platform.proxy_pool import create_proxy_pool_from_config
from packages.python.javdb_platform.request_handler import (
    RequestHandler, RequestConfig, ProxyBannedError, ProxyExhaustedError,
)

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_spider.fetch.session import is_login_page
from packages.python.javdb_spider.fetch.login_coordinator import LoginCoordinator, requeue_front
from packages.python.javdb_spider.fetch.backend import FetchBackend, FetchRuntimeState
from packages.python.javdb_spider.runtime.sleep import (
    MovieSleepManager,
    movie_sleep_mgr as _global_sleep_mgr,
    PenaltyTracker,
    TripleWindowThrottle,
    penalty_tracker as _shared_penalty_tracker,
    _interpolate_multiplier,
)
from packages.python.javdb_spider.runtime.config import (
    BASE_URL,
    CF_BYPASS_SERVICE_PORT,
    CF_BYPASS_ENABLED,
    CF_BYPASS_PORT_MAP,
    JAVDB_SESSION_COOKIE,
    PROXY_POOL,
    PROXY_POOL_MAX_FAILURES,
    LOGIN_PROXY_NAME,
)

logger = get_logger(__name__)

__all__ = [
    'FetchBackend', 'FetchRuntimeState',
    'EngineTask', 'EngineResult', 'LoginRequired',
    'WorkerContext', 'ParallelFetchBackend', 'FetchEngine',
]

# ---------------------------------------------------------------------------
# Engine-internal timing constants
# ---------------------------------------------------------------------------
_STARTUP_JITTER_BASE = (0.5, 2.0)
_STARTUP_JITTER_PER_WORKER = (1.5, 3.0)
_REQUEUE_BACKOFF_FACTOR = 0.3
_REQUEUE_BACKOFF_CAP = 2.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EngineTask:
    """Generic task for the fetch engine.

    ``url`` is the target to fetch.  ``meta`` carries arbitrary caller data
    that is round-tripped back in the corresponding :class:`EngineResult`.
    ``entry_index`` and ``failed_proxies`` satisfy the duck-typing contract
    required by :class:`~scripts.spider.fetch.login_coordinator.LoginCoordinator`.
    """

    url: str
    entry_index: str = ''
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)
    meta: dict = field(default_factory=dict)


@dataclass
class EngineResult:
    """Result produced by the engine for each submitted task.

    ``data`` holds whatever the caller's *process_fn* returned.
    """

    task: EngineTask
    success: bool
    data: Any = None
    used_cf: bool = False
    error: Optional[str] = None
    worker_name: str = ''
    _ack_callback: Optional[Callable[[str, bool], None]] = field(
        default=None,
        repr=False,
        compare=False,
    )

    def acknowledge(
        self,
        outcome_status: str,
        *,
        runtime_state_changed: bool = False,
    ) -> None:
        """Notify the producing backend that this result was fully handled."""

        if self._ack_callback is not None:
            self._ack_callback(outcome_status, runtime_state_changed)


class LoginRequired(Exception):
    """Raised by :meth:`WorkerContext.fetch` when a login page is detected.

    The engine's internal run-loop catches this and routes the task to the
    shared :class:`~scripts.spider.fetch.login_coordinator.LoginCoordinator`.
    Callers should **not** catch this inside their *process_fn*.
    """


# Type alias for user-supplied processing functions.
ProcessFn = Callable[['WorkerContext', EngineTask], Any]


def _task_worker_ctx(entry_index: str, worker_name: str) -> str:
    """Unified task log prefix: entry first, then worker."""
    return f"[{entry_index}][worker={worker_name}]"


# ---------------------------------------------------------------------------
# WorkerContext — public interface passed to process_fn
# ---------------------------------------------------------------------------


class WorkerContext:
    """Execution context passed to *process_fn* inside each worker thread.

    Provides fetch methods backed by the worker's per-proxy
    :class:`~utils.infra.request_handler.RequestHandler`, with automatic CF bypass
    fallback and login-page detection.
    """

    def __init__(self, worker: '_EngineWorker'):
        self._worker = worker
        self._last_used_cf: bool = False

    @property
    def proxy_name(self) -> str:
        return self._worker.proxy_name

    @property
    def worker_id(self) -> int:
        return self._worker.worker_id

    # -- low-level -----------------------------------------------------------

    def fetch_html(self, url: str, *, use_cf: bool = False) -> Optional[str]:
        """Fetch raw HTML without fallback cascade or login detection."""
        return self._worker._fetch_html(url, use_cf)

    @staticmethod
    def check_login_page(html: str) -> bool:
        """Return ``True`` if *html* is a login/auth wall page."""
        return is_login_page(html)

    def sleep(self) -> float:
        """Delegate to the worker-local sleep manager.

        Use this for intra-task pauses (e.g. between search and detail
        fetches) so that each worker's independent throttle budget is
        respected instead of the global module-level singleton.
        """
        return self._worker._sleep_mgr.sleep()

    # -- high-level ----------------------------------------------------------

    def fetch(self, url: str) -> Optional[str]:
        """Fetch *url* with direct -> CF bypass fallback.

        * Raises :class:`LoginRequired` if the response is a login page.
        * Returns ``None`` when all fetch attempts fail.
        * Respects the ``--always-bypass-time`` CF sticky window.
        """
        worker = self._worker

        if worker._should_shortcircuit_cf():
            html = worker._fetch_html(url, True)
            if html:
                if is_login_page(html):
                    raise LoginRequired()
                self._last_used_cf = True
                return html
            return None

        html = worker._fetch_html(url, False)
        if html:
            if is_login_page(html):
                raise LoginRequired()
            self._last_used_cf = False
            return html

        worker._sleep_mgr.sleep()
        html = worker._fetch_html(url, True)
        if html:
            if is_login_page(html):
                raise LoginRequired()
            worker._mark_cf_bypass()
            self._last_used_cf = True
            return html

        return None


# ---------------------------------------------------------------------------
# _EngineWorker — internal worker thread (not part of public API)
# ---------------------------------------------------------------------------


class _EngineWorker(threading.Thread):
    """Worker thread bound to a single proxy.

    Satisfies the duck-typing contract of
    :class:`~scripts.spider.fetch.login_coordinator.LoginCoordinator`::

        worker_id:    int
        proxy_name:   str
        proxy_config: dict
        _handler.config.javdb_session_cookie   (writable)
    """

    def __init__(
        self,
        worker_id: int,
        proxy_config: dict,
        task_queue: queue_module.Queue,
        result_queue: queue_module.Queue,
        login_queue: queue_module.Queue,
        total_workers: int,
        use_cookie: bool,
        process_fn: ProcessFn,
        all_workers: list,
        coordinator: LoginCoordinator,
        sleep_min: float,
        sleep_max: float,
        penalty_tracker: PenaltyTracker,
        banned_proxies: set,
        drain_lock: threading.Lock,
        drain_done: List[bool],
        stop_event: Optional[threading.Event] = None,
    ):
        super().__init__(
            daemon=True,
            name=f"EngineWorker-{proxy_config.get('name', worker_id)}",
        )
        self.worker_id = worker_id
        self.proxy_config = proxy_config
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.login_queue = login_queue
        self.total_workers = total_workers
        self.use_cookie = use_cookie
        self._process_fn = process_fn
        self.all_workers = all_workers
        self._coordinator = coordinator
        self._stop_event = stop_event or threading.Event()
        self._banned_proxies = banned_proxies
        self._drain_lock = drain_lock
        self._drain_done = drain_done

        self._cf_bypass_since: Optional[float] = None
        self._first_request = True
        self._startup_jitter = (
            random.uniform(*_STARTUP_JITTER_BASE)
            + worker_id * random.uniform(*_STARTUP_JITTER_PER_WORKER)
        )

        # One PenaltyTracker per engine (passed in): CF/failure events from any
        # worker must raise the penalty factor for all workers' adaptive sleep.
        # Per-worker TripleWindowThrottle stays isolated (independent proxy IPs).
        self._sleep_mgr = MovieSleepManager(
            sleep_min, sleep_max,
            penalty_tracker=penalty_tracker,
            throttle=TripleWindowThrottle(),
        )

        self._proxy_pool = create_proxy_pool_from_config(
            [proxy_config],
            max_failures=PROXY_POOL_MAX_FAILURES,
        )
        _cd = self._sleep_mgr.get_cooldown()
        self._handler = RequestHandler(
            proxy_pool=self._proxy_pool,
            config=RequestConfig(
                base_url=BASE_URL,
                cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
                cf_bypass_port_map=CF_BYPASS_PORT_MAP,
                cf_bypass_enabled=CF_BYPASS_ENABLED,
                cf_bypass_max_failures=3,
                cf_turnstile_cooldown=_cd,
                fallback_cooldown=_cd,
                javdb_session_cookie=JAVDB_SESSION_COOKIE,
                proxy_http=proxy_config.get('http'),
                proxy_https=proxy_config.get('https'),
                proxy_modules=['all'],
                proxy_mode='single',
                between_attempt_sleep=self._sleep_mgr.sleep,
            ),
            penalty_tracker=penalty_tracker,
        )

    # -- fetch helpers -------------------------------------------------------

    def _fetch_html(self, url: str, use_cf: bool) -> Optional[str]:
        return self._handler.get_page(
            url,
            use_cookie=self.use_cookie,
            use_proxy=True,
            module_name='spider',
            max_retries=1,
            use_cf_bypass=use_cf,
        )

    def _should_shortcircuit_cf(self) -> bool:
        abt = state.always_bypass_time
        if abt is None or self._cf_bypass_since is None:
            return False
        if abt == 0:
            return True
        window_seconds = abt * 60
        if time.time() - self._cf_bypass_since <= window_seconds:
            return True
        self._cf_bypass_since = None
        return False

    def _mark_cf_bypass(self) -> None:
        abt = state.always_bypass_time
        if abt is None:
            return
        self._cf_bypass_since = time.time()
        if abt == 0:
            logger.info(
                "[%s] CF Bypass succeeded — marking proxy for this runtime",
                self.proxy_name,
            )
        else:
            logger.info(
                "[%s] CF Bypass succeeded — marking proxy for %d minute(s)",
                self.proxy_name, abt,
            )

    # -- sleep / shutdown helpers --------------------------------------------

    def _interruptible_sleep(self, duration: float) -> bool:
        """Sleep for *duration* seconds.  Returns ``True`` if interrupted."""
        deadline = time.monotonic() + duration
        chunk = 0.5
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._stop_event.wait(timeout=min(chunk, remaining)):
                return True
        return False

    # -- task routing --------------------------------------------------------

    def _get_next_task(self) -> Optional[EngineTask]:
        while True:
            if self._stop_event.is_set():
                try:
                    return self.task_queue.get_nowait()
                except queue_module.Empty:
                    return None

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
                return self.task_queue.get(timeout=0.3 if am_logged_in else 2.0)
            except queue_module.Empty:
                continue

    def _handle_login_required(self, task: EngineTask) -> None:
        self._coordinator.handle_login_required(
            worker=self,
            task=task,
            video_code=task.meta.get('video_code', ''),
            login_queue=self.login_queue,
            task_queue=self.task_queue,
        )

    # -- main loop -----------------------------------------------------------

    @property
    def _active_workers(self) -> int:
        return self.total_workers - len(self._banned_proxies)

    def run(self) -> None:
        while True:
            task = self._get_next_task()
            if task is None:
                break

            if self._stop_event.is_set():
                self.task_queue.put(task)
                continue

            if self.proxy_name in task.failed_proxies:
                active = self._active_workers
                failed_non_banned = task.failed_proxies - self._banned_proxies
                if active <= 0 or len(failed_non_banned) >= active:
                    self.result_queue.put(EngineResult(
                        task=task, success=False,
                        error='all_proxies_failed',
                        worker_name=self.proxy_name,
                    ))
                    continue
                requeue_front(self.task_queue, task)
                backoff = min(_REQUEUE_BACKOFF_CAP, _REQUEUE_BACKOFF_FACTOR * len(task.failed_proxies))
                if self._interruptible_sleep(backoff):
                    continue
                continue

            if self._first_request:
                logger.debug(
                    "[%s] Startup jitter: %.1fs",
                    self.proxy_name, self._startup_jitter,
                )
                if self._interruptible_sleep(self._startup_jitter):
                    self.task_queue.put(task)
                    continue
                self._first_request = False
            else:
                sleep_time = self._sleep_mgr.get_sleep_time()
                pf = (
                    self._sleep_mgr._penalty_tracker.get_penalty_factor()
                    if self._sleep_mgr._penalty_tracker else 1.0
                )
                logger.debug(
                    "Movie sleep: %.2fs (penalty=%.2f)", sleep_time, pf,
                )
                if self._interruptible_sleep(sleep_time):
                    self.task_queue.put(task)
                    continue
                if self._sleep_mgr._throttle:
                    self._sleep_mgr._throttle.wait_if_needed()

            if self._stop_event.is_set():
                self.task_queue.put(task)
                continue

            ctx = WorkerContext(self)
            try:
                data = self._process_fn(ctx, task)
                if data is not None:
                    self.result_queue.put(EngineResult(
                        task=task, success=True,
                        data=data, used_cf=ctx._last_used_cf,
                        worker_name=self.proxy_name,
                    ))
                else:
                    task.failed_proxies.add(self.proxy_name)
                    task.retry_count += 1
                    requeue_front(self.task_queue, task)
                    logger.info(
                        "%s Process returned None, re-queued "
                        "(%d/%d proxies)",
                        _task_worker_ctx(task.entry_index, self.proxy_name),
                        len(task.failed_proxies), self._active_workers,
                    )
            except LoginRequired:
                self._handle_login_required(task)
            except ProxyBannedError:
                self._handle_proxy_banned(task)
                break
            except ProxyExhaustedError:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                requeue_front(self.task_queue, task)
                logger.warning(
                    "%s Proxy pool exhausted, re-queued "
                    "(%d/%d proxies)",
                    _task_worker_ctx(task.entry_index, self.proxy_name),
                    len(task.failed_proxies), self._active_workers,
                )
            except Exception as exc:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                requeue_front(self.task_queue, task)
                logger.warning(
                    "%s process_fn error: %s — re-queued "
                    "(%d/%d proxies)",
                    _task_worker_ctx(task.entry_index, self.proxy_name),
                    exc,
                    len(task.failed_proxies), self._active_workers,
                )

    def _handle_proxy_banned(self, task: EngineTask) -> None:
        """Handle proxy ban: stop this worker and re-route tasks.

        When active workers remain, dynamically re-calculate and apply
        volume multipliers for all surviving workers so that the
        increased per-worker load triggers appropriately higher sleep.
        """
        task.failed_proxies.add(self.proxy_name)

        with self._drain_lock:
            self._banned_proxies.add(self.proxy_name)
            active = self._active_workers

            logger.warning(
                "[worker=%s] Proxy banned (HTTP 403) — worker stopped "
                "(%d active workers remain)",
                self.proxy_name, active,
            )

            if active > 0:
                remaining = self.task_queue.qsize() + active
                for w in self.all_workers:
                    if w.proxy_name not in self._banned_proxies:
                        w._sleep_mgr.apply_volume_multiplier(
                            remaining, num_workers=active, quiet=True,
                        )
                per_worker = max(1, -(-remaining // max(1, active)))
                min_m, max_m = _interpolate_multiplier(per_worker)
                if min_m > 1.0 or max_m > 1.0:
                    logger.info(
                        "Volume-based sleep adjustment (ban rebalance): "
                        "total=%d, workers=%d, per_worker=%d → "
                        "volume_factor %.2fx/%.2fx",
                        remaining, active, per_worker, min_m, max_m,
                    )
                requeue_front(self.task_queue, task)
            else:
                self.result_queue.put(EngineResult(
                    task=task, success=False,
                    error='all_proxies_banned',
                    worker_name=self.proxy_name,
                ))
                if not self._drain_done[0]:
                    self._drain_done[0] = True
                    self._drain_remaining_tasks()

    def _drain_remaining_tasks(self) -> None:
        """When all workers are banned, drain task and login queues as failures.

        Must only be called once across all workers (guarded by
        ``_drain_lock`` / ``_drain_done`` in ``_handle_proxy_banned``).
        """
        for q in (self.task_queue, self.login_queue):
            while True:
                try:
                    item = q.get_nowait()
                    if item is None:
                        break
                    self.result_queue.put(EngineResult(
                        task=item, success=False,
                        error='all_proxies_banned',
                        worker_name=self.proxy_name,
                    ))
                except queue_module.Empty:
                    break


# ---------------------------------------------------------------------------
# ParallelFetchBackend — public parallel orchestrator
# ---------------------------------------------------------------------------


class ParallelFetchBackend(FetchBackend):
    """Parallel fetch engine backed by one worker per proxy.

    Manages worker lifecycle, task/result queues, and
    :class:`~scripts.spider.fetch.login_coordinator.LoginCoordinator` integration.
    The caller supplies a *process_fn* that receives a :class:`WorkerContext`
    and an :class:`EngineTask` and returns an arbitrary result (or ``None``
    on failure).

    For the common case of "fetch one URL then parse HTML", use the
    :meth:`simple` class-method constructor which wraps a plain
    ``parse_fn(html, task)`` into the full direct→CF→login cascade.
    """

    def __init__(
        self,
        process_fn: ProcessFn,
        *,
        use_cookie: bool = False,
        stop_event: Optional[threading.Event] = None,
        sleep_min: Optional[float] = None,
        sleep_max: Optional[float] = None,
        runtime_state: Optional[FetchRuntimeState] = None,
    ):
        self._process_fn = process_fn
        self._use_cookie = use_cookie
        self._stop_event = stop_event or threading.Event()
        self._sleep_min = (
            sleep_min if sleep_min is not None else _global_sleep_mgr.base_min
        )
        self._sleep_max = (
            sleep_max if sleep_max is not None else _global_sleep_mgr.base_max
        )

        self._task_queue: queue_module.Queue[Optional[EngineTask]] = (
            queue_module.Queue()
        )
        self._result_queue: queue_module.Queue[EngineResult] = (
            queue_module.Queue()
        )
        self._login_queue: queue_module.Queue[EngineTask] = (
            queue_module.Queue()
        )

        self._workers: List[_EngineWorker] = []
        self._coordinator: Optional[LoginCoordinator] = None
        self._started = False
        self._runtime_state = runtime_state or FetchRuntimeState(
            use_proxy=bool(PROXY_POOL),
            use_cf_bypass=False,
        )

        self._submitted = 0
        self._received = 0
        self._done = False
        self._count_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Create and start one worker thread per proxy in ``PROXY_POOL``."""
        if self._started:
            return
        self._started = True

        proxy_configs = list(PROXY_POOL) if PROXY_POOL else []
        if not proxy_configs:
            raise RuntimeError(
                "FetchEngine requires at least one proxy in PROXY_POOL"
            )

        ban_mgr = get_ban_manager()
        banned_proxies: set = set()
        pre_banned_count = 0
        active_configs = []
        for cfg in proxy_configs:
            name = cfg.get('name', '')
            if ban_mgr.is_proxy_banned(name):
                pre_banned_count += 1
                logger.info(
                    "[startup] Proxy '%s' already banned — skipping worker",
                    name,
                )
            else:
                active_configs.append(cfg)

        if not active_configs:
            raise RuntimeError(
                "FetchEngine: all proxies are banned, cannot start"
            )

        total_workers = len(active_configs)

        self._coordinator = LoginCoordinator(
            all_workers=self._workers,
            login_proxy_name=LOGIN_PROXY_NAME,
        )

        drain_lock = threading.Lock()
        drain_done: List[bool] = [False]

        # Same instance as movie_sleep_mgr.penalty_tracker: all engine workers
        # share CF/failure history for coordinated backoff; aligns with other
        # spider stages using the module sleep manager (thread-safe).
        for idx, proxy_cfg in enumerate(active_configs):
            w = _EngineWorker(
                worker_id=idx,
                proxy_config=proxy_cfg,
                task_queue=self._task_queue,
                result_queue=self._result_queue,
                login_queue=self._login_queue,
                total_workers=total_workers,
                use_cookie=self._use_cookie,
                process_fn=self._process_fn,
                all_workers=self._workers,
                coordinator=self._coordinator,
                sleep_min=self._sleep_min,
                sleep_max=self._sleep_max,
                penalty_tracker=_shared_penalty_tracker,
                banned_proxies=banned_proxies,
                drain_lock=drain_lock,
                drain_done=drain_done,
                stop_event=self._stop_event,
            )
            self._workers.append(w)

        self._inherit_login_state()
        self._inherit_global_volume(len(active_configs))

        if pre_banned_count:
            logger.info(
                "FetchEngine: starting %d worker(s) (%d proxies pre-banned)",
                len(self._workers), pre_banned_count,
            )
        else:
            logger.info(
                "FetchEngine: starting %d worker(s)", len(self._workers),
            )
        for w in self._workers:
            w.start()

    def _inherit_login_state(self) -> None:
        """Propagate index-phase login to the matching worker."""
        if not (state.logged_in_proxy_name and state.refreshed_session_cookie):
            return

        if (
            LOGIN_PROXY_NAME
            and state.logged_in_proxy_name != LOGIN_PROXY_NAME
        ):
            logger.warning(
                "Index login proxy [%s] differs from LOGIN_PROXY_NAME [%s] "
                "— session may not match engine workers",
                state.logged_in_proxy_name, LOGIN_PROXY_NAME,
            )

        for w in self._workers:
            if w.proxy_name == state.logged_in_proxy_name:
                w._handler.config.javdb_session_cookie = (
                    state.refreshed_session_cookie
                )
                self._coordinator.logged_in_worker_id = w.worker_id
                logger.info(
                    "Index login inherited: worker [%s] set as logged-in "
                    "for login-required pages",
                    w.proxy_name,
                )
                return

        logger.warning(
            "Index login via [%s] but no matching engine worker found",
            state.logged_in_proxy_name,
        )

    def _inherit_global_volume(self, num_workers: int) -> None:
        """Propagate the global sleep manager's volume state to all workers.

        Workers are initialised with the raw (un-scaled) base range so that
        ``apply_volume_multiplier`` in ``_handle_proxy_banned`` replaces
        rather than compounds.  This method seeds each worker with the
        volume multiplier that was already applied to the global singleton
        (e.g. by ``_post_process_index_results`` or alignment setup).
        """
        gm = _global_sleep_mgr
        with gm._lock:
            vol_min = gm._volume_min_mult
            vol_max = gm._volume_max_mult
            per_worker_n = gm._last_per_worker_n
        if vol_min <= 1.0 and vol_max <= 1.0:
            return
        for w in self._workers:
            with w._sleep_mgr._lock:
                w._sleep_mgr._volume_min_mult = vol_min
                w._sleep_mgr._volume_max_mult = vol_max
                w._sleep_mgr._last_per_worker_n = per_worker_n
                w._sleep_mgr._recalc_range()
            if per_worker_n and w._sleep_mgr._throttle:
                w._sleep_mgr._throttle.tighten_short_window(per_worker_n)
        logger.info(
            "Inherited global volume state to %d workers: "
            "volume_factor %.2fx/%.2fx",
            len(self._workers), vol_min, vol_max,
        )

    # -- task submission -----------------------------------------------------

    def submit(
        self,
        url: str,
        *,
        meta: Optional[dict] = None,
        entry_index: str = '',
    ) -> None:
        """Submit a URL for processing.  Thread-safe."""
        if self._done:
            raise RuntimeError("Cannot submit after mark_done()")
        task = EngineTask(
            url=url, entry_index=entry_index, meta=meta or {},
        )
        with self._count_lock:
            self._submitted += 1
        self._task_queue.put(task)

    def submit_task(self, task: EngineTask) -> None:
        """Submit a pre-built :class:`EngineTask`.  Thread-safe."""
        if self._done:
            raise RuntimeError("Cannot submit after mark_done()")
        with self._count_lock:
            self._submitted += 1
        self._task_queue.put(task)

    def mark_done(self) -> None:
        """Signal that no more tasks will be submitted.

        :meth:`results` will stop iterating once all submitted tasks have
        produced results.
        """
        self._done = True

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    # -- result consumption --------------------------------------------------

    @property
    def pending(self) -> int:
        """Number of submitted tasks that have not yet produced a result."""
        with self._count_lock:
            return self._submitted - self._received

    def results(self) -> Iterator[EngineResult]:
        """Yield results as workers complete them.

        Blocks between results.  Stops when :meth:`mark_done` has been called
        **and** every submitted task has produced a result (success or
        failure).
        """
        while True:
            with self._count_lock:
                if self._done and self._received >= self._submitted:
                    return
            try:
                result = self._result_queue.get(timeout=1.0)
            except queue_module.Empty:
                continue
            with self._count_lock:
                self._received += 1
            yield result

    # -- shutdown ------------------------------------------------------------

    def shutdown(self, *, timeout: float = 10) -> List[EngineTask]:
        """Stop all workers and return tasks that were not completed.

        Safe to call even if :meth:`mark_done` was not called first.
        """
        self._done = True
        self._stop_event.set()

        for _ in self._workers:
            self._task_queue.put(None)
        for w in self._workers:
            w.join(timeout=timeout)

        orphaned: List[EngineTask] = []
        for q in (self._task_queue, self._login_queue):
            while True:
                try:
                    item = q.get_nowait()
                    if item is not None:
                        orphaned.append(item)
                except queue_module.Empty:
                    break

        return orphaned

    def runtime_state(self) -> FetchRuntimeState:
        return FetchRuntimeState(
            use_proxy=self._runtime_state.use_proxy,
            use_cf_bypass=self._runtime_state.use_cf_bypass,
        )

    def export_login_state(self) -> None:
        """Write the engine's login state back to the global ``state`` module.

        After index-phase parallel fetch, the login worker may have refreshed
        the session cookie.  This method propagates that information so the
        subsequent detail-phase engine can inherit it via
        ``_inherit_login_state``.
        """
        if not self._coordinator:
            return
        lid = self._coordinator.logged_in_worker_id
        if lid is None:
            return
        for w in self._workers:
            if w.worker_id == lid:
                cookie = w._handler.config.javdb_session_cookie
                if cookie:
                    state.logged_in_proxy_name = w.proxy_name
                    state.refreshed_session_cookie = cookie
                    logger.info(
                        "Exported engine login state: proxy=%s",
                        w.proxy_name,
                    )
                return

    # -- convenience constructors --------------------------------------------

    @classmethod
    def simple(
        cls,
        parse_fn: Callable[[str, EngineTask], Any],
        **kwargs: Any,
    ) -> 'ParallelFetchBackend':
        """Create an engine that fetches one URL per task then calls *parse_fn*.

        The wrapper replicates the ``ProxyWorker`` direct→CF cascade: if the
        direct fetch succeeds but *parse_fn* returns ``None`` (parse failure),
        the CF bypass path is attempted before giving up.

        ``parse_fn(html, task)`` should return a truthy value on success or
        ``None`` on failure.
        """

        def _simple_process(ctx: WorkerContext, task: EngineTask) -> Any:
            worker = ctx._worker

            # CF sticky short-circuit
            if worker._should_shortcircuit_cf():
                html = ctx.fetch_html(task.url, use_cf=True)
                if html:
                    if is_login_page(html):
                        raise LoginRequired()
                    data = parse_fn(html, task)
                    if data is not None:
                        ctx._last_used_cf = True
                        return data
                    logger.debug(
                        "%s parse failed: CF Bypass (marked)",
                        _task_worker_ctx(task.entry_index, ctx.proxy_name),
                    )
                return None

            # Direct attempt
            html = ctx.fetch_html(task.url, use_cf=False)
            if html:
                if is_login_page(html):
                    raise LoginRequired()
                data = parse_fn(html, task)
                if data is not None:
                    ctx._last_used_cf = False
                    return data
                logger.debug(
                    "%s parse failed: Direct",
                    _task_worker_ctx(task.entry_index, ctx.proxy_name),
                )

            # Adaptive sleep before CF bypass (mirrors WorkerContext.fetch
            # and the sequential fallback's _sleep_between_fetches pattern).
            ctx.sleep()

            # CF bypass attempt
            html = ctx.fetch_html(task.url, use_cf=True)
            if html:
                if is_login_page(html):
                    raise LoginRequired()
                data = parse_fn(html, task)
                if data is not None:
                    worker._mark_cf_bypass()
                    ctx._last_used_cf = True
                    return data
                logger.debug(
                    "%s parse failed: CF Bypass",
                    _task_worker_ctx(task.entry_index, ctx.proxy_name),
                )

            return None

        return cls(process_fn=_simple_process, **kwargs)


# ---------------------------------------------------------------------------
# FetchEngine — compatibility facade over ParallelFetchBackend
# ---------------------------------------------------------------------------


class FetchEngine:
    """Compatibility facade that preserves the legacy FetchEngine API."""

    def __init__(self, process_fn: ProcessFn, **kwargs: Any):
        self._backend = ParallelFetchBackend(process_fn=process_fn, **kwargs)

    @classmethod
    def _from_backend(cls, backend: ParallelFetchBackend) -> 'FetchEngine':
        inst = cls.__new__(cls)
        inst._backend = backend
        return inst

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    @property
    def worker_count(self) -> int:
        return self._backend.worker_count

    @property
    def pending(self) -> int:
        return self._backend.pending

    def start(self) -> None:
        self._backend.start()

    def submit(
        self,
        url: str,
        *,
        meta: Optional[dict] = None,
        entry_index: str = '',
    ) -> None:
        self._backend.submit(url, meta=meta, entry_index=entry_index)

    def submit_task(self, task: EngineTask) -> None:
        self._backend.submit_task(task)

    def mark_done(self) -> None:
        self._backend.mark_done()

    def results(self) -> Iterator[EngineResult]:
        return self._backend.results()

    def shutdown(self, *, timeout: float = 10) -> List[EngineTask]:
        return self._backend.shutdown(timeout=timeout)

    def runtime_state(self) -> FetchRuntimeState:
        return self._backend.runtime_state()

    @classmethod
    def simple(
        cls,
        parse_fn: Callable[[str, EngineTask], Any],
        **kwargs: Any,
    ) -> 'FetchEngine':
        return cls._from_backend(
            ParallelFetchBackend.simple(parse_fn=parse_fn, **kwargs)
        )
