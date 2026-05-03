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
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional, Union
from urllib.parse import urlparse

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.login_state_client import LoginStateUnavailable
from packages.python.javdb_platform.proxy_coordinator_client import _normalize_proxy_id
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
    LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
)

logger = get_logger(__name__)

__all__ = [
    'FetchBackend', 'FetchRuntimeState',
    'EngineTask', 'EngineResult', 'LoginRequired',
    'WorkerContext', 'ParallelFetchBackend', 'FetchEngine',
    'PER_WORKER_TASK_CAP_ERROR',
]

# ---------------------------------------------------------------------------
# Engine-internal timing constants
# ---------------------------------------------------------------------------
_STARTUP_JITTER_BASE = (0.5, 2.0)
_STARTUP_JITTER_PER_WORKER = (1.5, 3.0)
_REQUEUE_BACKOFF_FACTOR = 0.3
_REQUEUE_BACKOFF_CAP = 2.0

# Emitted when :meth:`ParallelFetchBackend.results` drains tasks left in queue
# after every worker thread has stopped (e.g. per-worker task cap).
PER_WORKER_TASK_CAP_ERROR = "per_worker_task_cap"


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

    ``priority`` controls dequeue order when the engine uses a priority queue
    (lower values are dequeued first).  Default ``0`` preserves FIFO behaviour
    when all tasks share the same priority.

    ``_deadline`` is set internally by the worker when a task-level time
    budget is configured; it is **not** meant to be supplied by callers.

    ``_speculative`` is ``True`` when the task was created by the engine's
    speculative execution mechanism (idle workers racing on an in-flight
    task).  Speculative tasks are never re-queued on failure.

    ``login_verified_after_refresh`` is set by
    :class:`~scripts.spider.fetch.login_coordinator.LoginCoordinator` after a
    successful auto-login + fixed-page verification cycle.  Once set, any
    further :class:`LoginRequired` raised while the *logged-in worker* is
    processing this task is treated as a page/proxy issue (re-routed to a
    different proxy) instead of triggering yet another login attempt — the
    cookie is provably valid against fixed login-required pages, so the
    failure must be specific to this URL or this proxy IP.
    """

    url: str
    entry_index: str = ''
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)
    meta: dict = field(default_factory=dict)
    priority: int = 0
    login_verified_after_refresh: bool = False
    _deadline: Optional[float] = field(default=None, repr=False, compare=False)
    _speculative: bool = field(default=False, repr=False, compare=False)


@dataclass
class EngineResult:
    """Result produced by the engine for each submitted task.

    ``data`` holds whatever the caller's *process_fn* returned.

    When ``per_worker_cap_reached`` is True, this result is the worker's last
    successful task before it stops (per-worker task limit). Callers should log
    their normal per-task line first, then emit the cap message so logs stay in
    film order.
    """

    task: EngineTask
    success: bool
    data: Any = None
    used_cf: bool = False
    error: Optional[str] = None
    worker_name: str = ''
    per_worker_cap_reached: bool = False
    per_worker_cap_limit: int = 0
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


# ---------------------------------------------------------------------------
# Priority task queue (opt-in, used by index-page parallel fetch)
# ---------------------------------------------------------------------------

class _PriorityTaskQueue:
    """Drop-in ``Queue`` replacement that dequeues by ``EngineTask.priority``.

    Lower priority values are dequeued first.  A monotonic sequence number
    breaks ties so that tasks with equal priority preserve insertion order.
    ``None`` sentinels (used by shutdown) are given ``sys.maxsize`` priority
    so they are consumed only after all real tasks.
    """

    _is_priority_queue = True

    def __init__(self) -> None:
        self._pq: queue_module.PriorityQueue = queue_module.PriorityQueue()
        self._counter = 0
        self._counter_lock = threading.Lock()

    def _next_seq(self) -> int:
        with self._counter_lock:
            seq = self._counter
            self._counter += 1
            return seq

    def put(self, item: Any, block: bool = True, timeout: Any = None) -> None:
        priority = sys.maxsize if item is None else getattr(item, 'priority', 0)
        self._pq.put((priority, self._next_seq(), item), block, timeout)

    def put_nowait(self, item: Any) -> None:
        priority = sys.maxsize if item is None else getattr(item, 'priority', 0)
        self._pq.put_nowait((priority, self._next_seq(), item))

    def get(self, block: bool = True, timeout: Any = None) -> Any:
        _, _, item = self._pq.get(block, timeout)
        return item

    def get_nowait(self) -> Any:
        _, _, item = self._pq.get_nowait()
        return item

    def qsize(self) -> int:
        return self._pq.qsize()

    def empty(self) -> bool:
        return self._pq.empty()


# Type alias for the task queue (regular or priority).
_TaskQueue = Union[queue_module.Queue, _PriorityTaskQueue]

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

    def __init__(self, worker: '_EngineWorker', task: EngineTask):
        self._worker = worker
        self._current_task = task
        self._last_used_cf: bool = False

    @property
    def proxy_name(self) -> str:
        return self._worker.proxy_name

    @property
    def worker_id(self) -> int:
        return self._worker.worker_id

    @property
    def queue_pressure(self) -> str:
        """Queue pressure indicator (``'low'`` or ``'normal'``).

        When ``'low'``, the task queue is nearly empty while many workers
        are idle.  Callers should prefer a fast re-queue over an
        expensive CF fallback cascade so that another worker with a
        different proxy can attempt the direct path.
        """
        return self._worker._queue_pressure

    @property
    def is_expired(self) -> bool:
        """``True`` when the current task has exceeded its time budget.

        Custom ``process_fn`` implementations should check this between
        expensive steps and return ``None`` early to allow a re-queue.
        """
        return self._worker._is_task_expired(self._current_task)

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

        if self.is_expired:
            return None

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

        # Skip expensive CF bypass when queue pressure is low, UNLESS
        # this task has already exhausted most proxies via direct path
        # (tail-task scenario where CF bypass may be the only way).
        if self.queue_pressure == 'low':
            active = worker._active_workers
            if len(self._current_task.failed_proxies) < max(1, active - 1):
                return None

        if self.is_expired:
            return None

        worker._sleep_mgr.sleep()

        if self.is_expired:
            return None

        html = worker._fetch_html(url, True)
        if html:
            if is_login_page(html):
                raise LoginRequired()
            worker._mark_cf_bypass()
            self._last_used_cf = True
            return html

        return None


# ---------------------------------------------------------------------------
# Coordinator addressing
# ---------------------------------------------------------------------------


def _stable_proxy_id(proxy_config: dict, worker_id: int) -> str:
    """Derive a stable identifier for the cross-instance proxy coordinator.

    Different GH Actions runners (and successive runs on the same runner)
    must hash to the same Durable Object for a given physical proxy,
    otherwise the per-proxy mutex silently splits and we lose the global
    rate-limit guarantee. Resolution order:

    1. ``proxy_config['name']`` — explicitly configured identity wins.
    2. ``proxy-<sha1(host:port)[:16]>`` via the coordinator client's shared
       normalisation rule.
    3. ``Proxy-{worker_id}`` — last-resort label. Unstable across runs
       because ``worker_id`` is just an enumeration index, but at least
       keeps the spider running when neither name nor URL is configured.

    The ordinal label remains in :attr:`_EngineWorker.proxy_name` for
    human-readable logs / ban manager / thread name; coordination uses
    only the value returned here.
    """
    name = proxy_config.get('name')
    if isinstance(name, str) and name.strip():
        return name.strip()[:256]
    url = proxy_config.get('https') or proxy_config.get('http')
    if isinstance(url, str) and url.strip():
        parsed = urlparse(url.strip())
        try:
            port = parsed.port
        except ValueError:
            port = None
        if parsed.hostname and port is not None:
            fallback_seed = f"{parsed.hostname.lower()}:{port}"
            return _normalize_proxy_id(None, fallback_seed=fallback_seed)
    return f"Proxy-{worker_id}"


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
        capped_proxies: set,
        drain_lock: threading.Lock,
        drain_done: List[bool],
        stop_event: Optional[threading.Event] = None,
        per_worker_task_limit: int = 0,
        task_timeout: float = 0,
        in_flight: Optional[dict] = None,
        in_flight_lock: Optional[threading.Lock] = None,
        completed_entries: Optional[set] = None,
        completed_lock: Optional[threading.Lock] = None,
    ):
        super().__init__(
            daemon=True,
            name=f"EngineWorker-{proxy_config.get('name', worker_id)}",
        )
        self.worker_id = worker_id
        self._per_worker_task_limit = max(0, int(per_worker_task_limit))
        self._per_worker_completed = 0
        self._task_timeout = max(0.0, float(task_timeout))
        self.proxy_config = proxy_config
        # proxy_name is the human-readable label (logs, ban manager, thread
        # name) and intentionally falls back to the ordinal index. The
        # coordinator addressing key is computed separately by
        # _stable_proxy_id() so DO routing stays consistent across runs.
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self._coordinator_proxy_id: str = _stable_proxy_id(proxy_config, worker_id)
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
        self._capped_proxies = capped_proxies
        self._drain_lock = drain_lock
        self._drain_done = drain_done

        # Speculative execution shared state (all workers share the same
        # dict/set/lock instances, created by ParallelFetchBackend).
        self._in_flight: dict = in_flight if in_flight is not None else {}
        self._in_flight_lock = in_flight_lock or threading.Lock()
        self._completed_entries: set = completed_entries if completed_entries is not None else set()
        self._completed_lock = completed_lock or threading.Lock()

        self._cf_bypass_since: Optional[float] = None
        self._first_request = True
        self._startup_jitter = (
            random.uniform(*_STARTUP_JITTER_BASE)
            + worker_id * random.uniform(*_STARTUP_JITTER_PER_WORKER)
        )

        # One PenaltyTracker per engine (passed in): CF/failure events from any
        # worker must raise the penalty factor for all workers' adaptive sleep.
        # Per-worker TripleWindowThrottle stays isolated (independent proxy IPs).
        # When a cross-instance proxy coordinator is configured, each worker
        # passes its STABLE coordinator id (configured name / hashed URL) as
        # proxy_id so the per-proxy DO mutex serialises requests across all GH
        # Actions runners holding this proxy regardless of worker startup
        # order. Coordinator absence (None) preserves the local-only path.
        self._sleep_mgr = MovieSleepManager(
            sleep_min, sleep_max,
            penalty_tracker=penalty_tracker,
            throttle=TripleWindowThrottle(),
            proxy_label=self.proxy_name,
            coordinator=state.global_proxy_coordinator,
            proxy_id=self._coordinator_proxy_id,
        )

        self._proxy_pool = create_proxy_pool_from_config(
            [proxy_config],
            max_failures=PROXY_POOL_MAX_FAILURES,
        )
        _cd = self._sleep_mgr.get_cooldown()

        # Per-proxy CF/failure callback wires into the cross-instance
        # coordinator so other GH Actions runners using this same proxy
        # also see the elevated penalty_factor on their next /lease call.
        # Captures the STABLE coordinator id (NOT proxy_name, which can fall
        # back to an ordinal index) so reports route to the same DO every run.
        coordinator = state.global_proxy_coordinator
        coord_proxy_id = self._coordinator_proxy_id
        if coordinator is not None and coord_proxy_id:
            # Per-worker callback is pinned to a single proxy via closure;
            # the positional arg from RequestHandler is intentionally ignored
            # (only the global fallback handler uses it).
            def _cf_event_cb(_unused_proxy_name=None, _c=coordinator, _p=coord_proxy_id):
                _c.report_async(_p, "cf")
        else:
            _cf_event_cb = None

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
            on_cf_event=_cf_event_cb,
        )

    # -- task deadline -------------------------------------------------------

    def _stamp_deadline(self, task: EngineTask) -> None:
        """Set the task's deadline based on the configured timeout.

        Also propagates the deadline to the ``RequestHandler`` so the
        CF fallback cascade can bail out early.
        """
        if self._task_timeout > 0:
            task._deadline = time.monotonic() + self._task_timeout
        else:
            task._deadline = None
        self._handler.config.task_deadline = task._deadline

    def _is_task_expired(self, task: EngineTask) -> bool:
        """Return ``True`` when the task has exceeded its time budget."""
        return task._deadline is not None and time.monotonic() > task._deadline

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

    def _try_speculative_task(self) -> Optional[EngineTask]:
        """Create a speculative copy of an in-flight task if possible.

        Only fires when the task queue is empty and another worker is
        actively processing a task that this worker's proxy hasn't
        tried yet.  The copy is marked ``_speculative=True`` so the
        run-loop knows not to re-queue on failure.
        """
        with self._in_flight_lock:
            for entry_idx, task in self._in_flight.items():
                with self._completed_lock:
                    if entry_idx in self._completed_entries:
                        continue
                if self.proxy_name in task.failed_proxies:
                    continue
                spec = EngineTask(
                    url=task.url,
                    entry_index=task.entry_index,
                    retry_count=task.retry_count,
                    failed_proxies=set(task.failed_proxies),
                    meta=dict(task.meta),
                    priority=task.priority,
                    login_verified_after_refresh=task.login_verified_after_refresh,
                    _deadline=task._deadline,
                    _speculative=True,
                )
                return spec
        return None

    def _is_entry_completed(self, entry_index: str) -> bool:
        if not entry_index:
            return False
        with self._completed_lock:
            return entry_index in self._completed_entries

    def _mark_entry_completed(self, entry_index: str) -> bool:
        """Atomically mark an entry as completed.

        Returns ``True`` if this call was the one that set it (i.e. first
        to complete).  Returns ``False`` if it was already completed.
        """
        if not entry_index:
            return True
        with self._completed_lock:
            if entry_index in self._completed_entries:
                return False
            self._completed_entries.add(entry_index)
            return True

    def _register_in_flight(self, task: EngineTask) -> None:
        if task._speculative or not task.entry_index:
            return
        with self._in_flight_lock:
            self._in_flight[task.entry_index] = task

    def _unregister_in_flight(self, task: EngineTask) -> None:
        if task._speculative or not task.entry_index:
            return
        with self._in_flight_lock:
            self._in_flight.pop(task.entry_index, None)

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
                spec = self._try_speculative_task()
                if spec is not None:
                    return spec
                continue

    def _handle_login_required(self, task: EngineTask) -> None:
        self._coordinator.handle_login_required(
            worker=self,
            task=task,
            video_code=task.meta.get('video_code', ''),
            login_queue=self.login_queue,
            task_queue=self.task_queue,
        )

    def _reassign_logged_in_worker_before_cap_exit(self) -> None:
        """If this worker holds the session for login_queue, hand off before the thread exits.

        Otherwise ``logged_in_worker_id`` would point at a dead worker, login-required
        tasks would sit in ``login_queue`` with no live worker prioritising it, and
        :meth:`FetchEngine.results` could stall until every worker had exited.
        """
        coord = self._coordinator
        with coord.lock:
            if coord.logged_in_worker_id != self.worker_id:
                return
            cookie = str(
                getattr(self._handler.config, 'javdb_session_cookie', None) or '',
            ).strip()
            replacement = None
            for w in self.all_workers:
                if w.worker_id == self.worker_id:
                    continue
                if w.proxy_name in self._banned_proxies or w.proxy_name in self._capped_proxies:
                    continue
                if not w.is_alive():
                    continue
                replacement = w
                break
            if replacement is not None:
                if cookie:
                    replacement._handler.config.javdb_session_cookie = cookie
                coord.logged_in_worker_id = replacement.worker_id
                logger.info(
                    "[%s] Per-worker task cap: transferring logged-in session to [%s]",
                    self.proxy_name,
                    replacement.proxy_name,
                )
                return
            coord.logged_in_worker_id = None
            drained = 0
            while True:
                try:
                    t = self.login_queue.get_nowait()
                except queue_module.Empty:
                    break
                requeue_front(self.task_queue, t)
                drained += 1
            if drained:
                logger.warning(
                    "[%s] Per-worker task cap: was logged-in worker; no peer to reassign — "
                    "cleared login designation and re-queued %d login_queue task(s) to task_queue",
                    self.proxy_name,
                    drained,
                )
            else:
                logger.warning(
                    "[%s] Per-worker task cap: was logged-in worker; no peer to reassign — "
                    "cleared login designation",
                    self.proxy_name,
                )

    # -- main loop -----------------------------------------------------------

    @property
    def _active_workers(self) -> int:
        return self.total_workers - len(self._banned_proxies) - len(self._capped_proxies)

    @property
    def _queue_pressure(self) -> str:
        """Estimate queue pressure relative to idle worker capacity.

        Returns ``'low'`` when the task queue is nearly empty and most
        workers are likely idle — callers can use this to skip expensive
        fallback paths (e.g. CF bypass cascade) and re-queue immediately
        so that another worker with a different proxy can try the direct
        path instead.
        """
        qsize = self.task_queue.qsize()
        active = self._active_workers
        if qsize <= 1 and active > 2:
            return 'low'
        return 'normal'

    def run(self) -> None:  # noqa: C901 – complexity from speculative paths
        while True:
            task = self._get_next_task()
            if task is None:
                break

            if self._stop_event.is_set():
                if not task._speculative:
                    self.task_queue.put(task)
                continue

            # Speculative tasks whose entry was already completed are stale.
            if task._speculative and self._is_entry_completed(task.entry_index):
                continue

            if self.proxy_name in task.failed_proxies:
                if task._speculative:
                    continue
                active = self._active_workers
                failed_non_active = task.failed_proxies - self._banned_proxies - self._capped_proxies
                if active <= 0 or len(failed_non_active) >= active:
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
                    if not task._speculative:
                        self.task_queue.put(task)
                    continue
                self._first_request = False
            else:
                # Consult the cross-instance proxy coordinator (when
                # configured) before sleeping.  ``plan_sleep`` returns the
                # total wait we should observe and a flag indicating
                # whether the cross-instance throttle was already enforced
                # — in that case we skip the local TripleWindowThrottle to
                # avoid double-counting waits.  Fall-open path (no
                # coordinator, or coordinator unreachable) preserves the
                # original local sleep + throttle behaviour.
                sleep_time, used_coordinator = self._sleep_mgr.plan_sleep()
                if self._interruptible_sleep(sleep_time):
                    if not task._speculative:
                        self.task_queue.put(task)
                    continue
                if not used_coordinator and self._sleep_mgr._throttle:
                    self._sleep_mgr._throttle.wait_if_needed()

            if self._stop_event.is_set():
                if not task._speculative:
                    self.task_queue.put(task)
                continue

            # Check again before the expensive process_fn call.
            if task._speculative and self._is_entry_completed(task.entry_index):
                continue

            self._stamp_deadline(task)
            self._register_in_flight(task)
            ctx = WorkerContext(self, task)
            try:
                data = self._process_fn(ctx, task)
                if data is not None:
                    if not self._mark_entry_completed(task.entry_index):
                        # Another worker (speculative or original) already
                        # produced a result for this entry — discard ours.
                        logger.debug(
                            "%s discarding duplicate result (already completed)",
                            _task_worker_ctx(task.entry_index, self.proxy_name),
                        )
                        continue
                    self._per_worker_completed += 1
                    cap_now = (
                        self._per_worker_task_limit > 0
                        and self._per_worker_completed
                        >= self._per_worker_task_limit
                    )
                    self.result_queue.put(EngineResult(
                        task=task, success=True,
                        data=data, used_cf=ctx._last_used_cf,
                        worker_name=self.proxy_name,
                        per_worker_cap_reached=cap_now,
                        per_worker_cap_limit=(
                            self._per_worker_task_limit if cap_now else 0
                        ),
                    ))
                    if cap_now:
                        self._reassign_logged_in_worker_before_cap_exit()
                        with self._drain_lock:
                            self._capped_proxies.add(self.proxy_name)
                        break
                else:
                    if task._speculative:
                        # Propagate the failure to the original in-flight task
                        # so _try_speculative_task skips this proxy next time.
                        with self._in_flight_lock:
                            orig = self._in_flight.get(task.entry_index)
                            if orig is not None:
                                orig.failed_proxies.add(self.proxy_name)
                        continue
                    if self._is_entry_completed(task.entry_index):
                        continue
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
                if not task._speculative:
                    self._handle_login_required(task)
            except ProxyBannedError:
                if task._speculative:
                    with self._in_flight_lock:
                        orig = self._in_flight.get(task.entry_index)
                        if orig is not None:
                            orig.failed_proxies.add(self.proxy_name)
                self._handle_proxy_banned(task, _requeue=not task._speculative)
                break
            except ProxyExhaustedError:
                if task._speculative:
                    with self._in_flight_lock:
                        orig = self._in_flight.get(task.entry_index)
                        if orig is not None:
                            orig.failed_proxies.add(self.proxy_name)
                    continue
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
                if task._speculative:
                    with self._in_flight_lock:
                        orig = self._in_flight.get(task.entry_index)
                        if orig is not None:
                            orig.failed_proxies.add(self.proxy_name)
                    continue
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
            finally:
                self._unregister_in_flight(task)

    def _handle_proxy_banned(self, task: EngineTask, *, _requeue: bool = True) -> None:
        """Handle proxy ban: stop this worker and re-route tasks.

        When active workers remain, dynamically re-calculate and apply
        volume multipliers for all surviving workers so that the
        increased per-worker load triggers appropriately higher sleep.

        When ``_requeue`` is ``False`` (speculative task), the ban is
        recorded and volume rebalanced but the task is not re-queued or
        emitted as a failure result.
        """
        task.failed_proxies.add(self.proxy_name)

        # Reclaim this proxy's unused login attempts from the global budget
        # so banned workers no longer reserve credits they cannot spend.
        state.deduct_proxy_login_budget(self.proxy_name)

        with self._drain_lock:
            self._banned_proxies.add(self.proxy_name)
            active = self._active_workers

            logger.warning(
                "[worker=%s] Proxy banned (HTTP 403) — worker stopped "
                "(%d active workers remain)",
                self.proxy_name, active,
            )

            if active > 0:
                # Queue depth + in-flight tasks in worker threads (approximate).
                remaining_est = self.task_queue.qsize() + active
                volume_total = remaining_est
                if self._per_worker_task_limit > 0:
                    cap = self._per_worker_task_limit * active
                    volume_total = min(remaining_est, cap)
                for w in self.all_workers:
                    if w.proxy_name not in self._banned_proxies:
                        w._sleep_mgr.apply_volume_multiplier(
                            volume_total, num_workers=active, quiet=True,
                        )
                per_worker = max(1, -(-volume_total // max(1, active)))
                min_m, max_m = _interpolate_multiplier(per_worker)
                if min_m > 1.0 or max_m > 1.0:
                    sample_w = next(
                        (w for w in self.all_workers
                         if w.proxy_name not in self._banned_proxies), None,
                    )
                    cap_note = ""
                    if (
                        self._per_worker_task_limit > 0
                        and volume_total < remaining_est
                    ):
                        cap_note = (
                            f" [sleep volume capped: queue_est={remaining_est}, "
                            f"per_worker_task_limit={self._per_worker_task_limit}]"
                        )
                    if sample_w is not None:
                        sm = sample_w._sleep_mgr
                        logger.info(
                            "Volume-based sleep adjustment (ban rebalance): "
                            "total=%d, workers=%d, per_worker=%d → "
                            "volume_factor %.2fx/%.2fx, "
                            "sleep range ~[%.2f, %.2f] (base ~[%.2f, %.2f])%s",
                            volume_total, active, per_worker, min_m, max_m,
                            sm.sleep_min, sm.sleep_max,
                            sm.base_min, sm.base_max,
                            cap_note,
                        )
                    else:
                        logger.info(
                            "Volume-based sleep adjustment (ban rebalance): "
                            "total=%d, workers=%d, per_worker=%d → "
                            "volume_factor %.2fx/%.2fx%s",
                            volume_total, active, per_worker, min_m, max_m,
                            cap_note,
                        )
                if _requeue:
                    requeue_front(self.task_queue, task)
            else:
                if _requeue:
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
        use_priority_queue: bool = False,
        per_worker_task_limit: int = 0,
        task_timeout: float = 0,
    ):
        self._process_fn = process_fn
        self._use_cookie = use_cookie
        self._per_worker_task_limit = max(0, int(per_worker_task_limit))
        self._task_timeout = max(0.0, float(task_timeout))
        self._stop_event = stop_event or threading.Event()
        self._sleep_min = (
            sleep_min if sleep_min is not None else _global_sleep_mgr.base_min
        )
        self._sleep_max = (
            sleep_max if sleep_max is not None else _global_sleep_mgr.base_max
        )

        self._task_queue: _TaskQueue = (
            _PriorityTaskQueue() if use_priority_queue
            else queue_module.Queue()
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
        self._stale_queue_flushed = False

        # Speculative execution: shared across all workers.
        self._in_flight: dict = {}
        self._in_flight_lock = threading.Lock()
        self._completed_entries: set = set()
        self._completed_lock = threading.Lock()

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
        pre_banned_names: List[str] = []
        for cfg in proxy_configs:
            name = cfg.get('name', '')
            if ban_mgr.is_proxy_banned(name):
                pre_banned_count += 1
                pre_banned_names.append(name)
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

        # Recompute the global login budget so it reflects only the proxies
        # we will actually run (matches the per-proxy x active rule used at
        # state-init time).  Only safe before any login attempt has fired.
        if state.login_total_attempts == 0:
            new_budget = len(active_configs) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT
            if new_budget != state.login_total_budget:
                logger.info(
                    "Login budget adjusted at startup: %d -> %d "
                    "(%d active proxies, %d pre-banned)",
                    state.login_total_budget, new_budget,
                    len(active_configs), pre_banned_count,
                )
                state.login_total_budget = new_budget
            for name in pre_banned_names:
                # Mark as already accounted for so a later runtime ban is a no-op.
                state._login_budget_deducted_proxies.add(name)
        else:
            for name in pre_banned_names:
                state.deduct_proxy_login_budget(name)

        total_workers = len(active_configs)

        self._coordinator = LoginCoordinator(
            all_workers=self._workers,
            login_proxy_name=LOGIN_PROXY_NAME,
        )

        capped_proxies: set = set()
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
                capped_proxies=capped_proxies,
                drain_lock=drain_lock,
                drain_done=drain_done,
                stop_event=self._stop_event,
                per_worker_task_limit=self._per_worker_task_limit,
                task_timeout=self._task_timeout,
                in_flight=self._in_flight,
                in_flight_lock=self._in_flight_lock,
                completed_entries=self._completed_entries,
                completed_lock=self._completed_lock,
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
        """Propagate index-phase or cross-runtime login to the matching worker.

        Two sources of login state, in order of preference:

        1. **Cross-runtime DO** (when configured): another GitHub Actions
           runner may have already published a fresh cookie via
           :class:`GlobalLoginState`.  Pulling it here lets this runner
           skip its own re-login entirely on startup, mirroring the
           cookie + proxy_name into ``state`` so the existing per-worker
           injection path below picks it up.
        2. **Index phase** (legacy single-runtime path): when the index
           fetcher inside *this* runner just performed a login, the
           cookie is already in ``state.refreshed_session_cookie``.

        DO failures fall through silently — the legacy path remains the
        source of truth in that case (per the fail-open contract).
        """
        # 1. Pull the latest published login state from the DO singleton
        #    so a fresh runner can adopt a peer's cookie without paying
        #    the cost of its own login.  Skip when the DO is not
        #    configured or when this runner has already observed the
        #    same version (e.g. via a poller tick that happened earlier).
        do_client = state.global_login_state_client
        if do_client is not None:
            try:
                snapshot = do_client.get_state()
            except LoginStateUnavailable as exc:
                logger.warning(
                    "Engine startup: DO get_state failed (%s) — "
                    "falling back to index-phase login state",
                    exc,
                )
            else:
                already_local = state.current_login_state_version or 0
                if (
                    snapshot.proxy_name
                    and snapshot.cookie
                    and snapshot.version > already_local
                ):
                    state.logged_in_proxy_name = snapshot.proxy_name
                    state.refreshed_session_cookie = snapshot.cookie
                    state.current_login_state_version = snapshot.version
                    logger.info(
                        "Engine startup: adopted cross-runtime login state "
                        "from DO (proxy=%s, version=%d)",
                        snapshot.proxy_name, snapshot.version,
                    )

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

        Workers start from the raw base range; this reapplies volume scaling
        using the same total the global singleton last saw and the engine's
        worker count, so per-worker multipliers match the real parallelism
        (copying ``_last_per_worker_n`` from a different worker count would
        under-throttle).
        """
        gm = _global_sleep_mgr
        with gm._lock:
            vol_min = gm._volume_min_mult
            vol_max = gm._volume_max_mult
            last_total = gm._last_volume_total
        if vol_min <= 1.0 and vol_max <= 1.0:
            return
        if last_total <= 0:
            return
        nw = max(1, num_workers)
        for w in self._workers:
            w._sleep_mgr.apply_volume_multiplier(
                last_total, num_workers=nw, quiet=True,
            )
        eff_min, eff_max = vol_min, vol_max
        if self._workers:
            w0 = self._workers[0]
            with w0._sleep_mgr._lock:
                eff_min = w0._sleep_mgr._volume_min_mult
                eff_max = w0._sleep_mgr._volume_max_mult
        logger.info(
            "Inherited global volume to %d workers (total=%d): "
            "volume_factor %.2fx/%.2fx",
            len(self._workers), last_total, eff_min, eff_max,
        )

    # -- task submission -----------------------------------------------------

    def submit(
        self,
        url: str,
        *,
        meta: Optional[dict] = None,
        entry_index: str = '',
        priority: int = 0,
    ) -> None:
        """Submit a URL for processing.  Thread-safe."""
        if self._done:
            raise RuntimeError("Cannot submit after mark_done()")
        task = EngineTask(
            url=url, entry_index=entry_index, meta=meta or {},
            priority=priority,
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

    def _maybe_flush_stalled_tasks(self) -> None:
        """If every worker has exited but tasks remain, emit failure results.

        Otherwise :meth:`results` would block forever (e.g. per-worker task
        cap stopped all workers while the shared queue still holds tasks).
        """
        if self._stale_queue_flushed or not self._workers:
            return
        if any(w.is_alive() for w in self._workers):
            return
        with self._count_lock:
            if not self._done:
                return
            pending = self._submitted - self._received
        if pending <= 0:
            return

        flushed = 0
        for q in (self._task_queue, self._login_queue):
            while True:
                try:
                    item = q.get_nowait()
                except queue_module.Empty:
                    break
                if item is None:
                    continue
                self._result_queue.put(
                    EngineResult(
                        task=item,
                        success=False,
                        error=PER_WORKER_TASK_CAP_ERROR,
                        worker_name="engine",
                    ),
                )
                flushed += 1

        if flushed:
            self._stale_queue_flushed = True
            logger.warning(
                "FetchEngine: all workers stopped with %d task(s) still queued — "
                "emitted failure results (%s)",
                flushed,
                PER_WORKER_TASK_CAP_ERROR,
            )

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
                self._maybe_flush_stalled_tasks()
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

            if ctx.is_expired:
                return None

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

            # When most workers are idle and few tasks remain, skip the
            # expensive CF bypass cascade and re-queue immediately so
            # another worker can attempt the direct path with a different
            # proxy — unless this task has already exhausted most proxies
            # via direct path (tail-task scenario).
            if ctx.queue_pressure == 'low':
                active = ctx._worker._active_workers
                if len(task.failed_proxies) < max(1, active - 1):
                    logger.debug(
                        "%s low queue pressure — skipping CF fallback, re-queuing",
                        _task_worker_ctx(task.entry_index, ctx.proxy_name),
                    )
                    return None

            if ctx.is_expired:
                logger.debug(
                    "%s task time budget exceeded before CF fallback",
                    _task_worker_ctx(task.entry_index, ctx.proxy_name),
                )
                return None

            # Adaptive sleep before CF bypass (mirrors WorkerContext.fetch
            # and the sequential fallback's _sleep_between_fetches pattern).
            ctx.sleep()

            if ctx.is_expired:
                return None

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
        priority: int = 0,
    ) -> None:
        self._backend.submit(url, meta=meta, entry_index=entry_index, priority=priority)

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
