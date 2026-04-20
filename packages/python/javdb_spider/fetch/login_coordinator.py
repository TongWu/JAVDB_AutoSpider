"""Shared login-queue routing and login coordination for parallel workers.

Used by ``ProxyWorker`` (spider), ``BackfillWorker`` (migration), and
``AlignWorker`` (inventory alignment) — single source of truth for all
login retry / proxy-switch / budget logic.
"""

from __future__ import annotations

import queue as queue_module
import threading
from typing import Optional

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_spider.fetch.session import (
    attempt_login_refresh,
    verify_login_via_fixed_pages,
)
from packages.python.javdb_spider.runtime.config import (
    LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
    LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH,
    LOGIN_VERIFICATION_URLS,
)
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


def _task_worker_ctx(entry_index: str, worker_name: str) -> str:
    """Unified task log prefix: entry first, then worker."""
    return f"[{entry_index}][worker={worker_name}]"


def _get_login_verified(task) -> bool:
    """Read ``task.login_verified_after_refresh`` defensively.

    The coordinator is duck-typed — callers may pass :class:`EngineTask` or
    legacy tasks (e.g. ``DetailTask``) that do not declare the flag.  A
    direct attribute access would raise :class:`AttributeError` and abort
    the login state machine halfway through.
    """
    return bool(getattr(task, "login_verified_after_refresh", False))


def _set_login_verified(task, value: bool) -> None:
    """Write ``task.login_verified_after_refresh``.

    See :func:`_get_login_verified` for the duck-typing rationale on reads.
    Writes use a direct attribute assignment — if the task type restricts
    attributes via ``__slots__`` the resulting :class:`AttributeError`
    should surface so the misconfiguration is noticed rather than silently
    dropping the verified flag.
    """
    task.login_verified_after_refresh = value


# ---------------------------------------------------------------------------
# Low-level helpers (shared across all parallel workers)
# ---------------------------------------------------------------------------


def requeue_front(q: queue_module.Queue, item) -> None:
    """Put *item* at the front of a Queue so it gets picked up next.

    For priority queues (``_is_priority_queue``), a regular ``put()`` is used
    instead — the item's ``priority`` field already determines dequeue order.
    """
    if getattr(q, '_is_priority_queue', False):
        q.put(item)
        return
    with q.mutex:
        q.queue.appendleft(item)
        q.not_empty.notify()


def use_login_queue_priority(
    login_proxy_name: Optional[str],
    worker_proxy_name: str,
    logged_in_worker_id: Optional[int],
    worker_id: int,
) -> bool:
    """True if this worker should drain ``login_queue`` before the shared task queue."""
    if logged_in_worker_id is not None and logged_in_worker_id == worker_id:
        return True
    if login_proxy_name and worker_proxy_name == login_proxy_name:
        return True
    return False


def should_delegate_login_task(
    login_proxy_name: Optional[str],
    worker_proxy_name: str,
) -> bool:
    """True if login-required work must be forwarded to the named login proxy worker."""
    return bool(login_proxy_name and worker_proxy_name != login_proxy_name)


# ---------------------------------------------------------------------------
# LoginCoordinator — the shared login state machine
# ---------------------------------------------------------------------------


class LoginCoordinator:
    """Shared login retry / proxy-switch / budget state machine.

    Each parallel orchestrator (spider, backfill, alignment) creates its own
    ``LoginCoordinator`` instance.  All workers within that orchestrator share
    the same instance, ensuring coordinated login across proxies.

    Workers must expose these attributes (duck-typed)::

        worker_id:    int
        proxy_name:   str
        proxy_config: dict
        _handler.config.javdb_session_cookie   (writable)

    Tasks must expose::

        entry_index:    str
        failed_proxies: set
    """

    def __init__(
        self,
        all_workers: list,
        login_proxy_name: str | None = None,
        lock: threading.Lock | None = None,
    ):
        self._lock = lock or threading.Lock()
        self._all_workers = all_workers
        self._login_proxy_name = login_proxy_name
        self.logged_in_worker_id: int | None = None

    @property
    def lock(self) -> threading.Lock:
        """Expose the lock for ``_get_next_task`` synchronization."""
        return self._lock

    # -- login execution helpers (must hold lock) --------------------------

    def _do_login_for_proxy(self, proxy_config: dict, proxy_name: str):
        """Call ``attempt_login_refresh`` for a specific proxy.

        Always uses the worker's own ``proxy_config`` so the login endpoint
        matches the proxy that will later carry the authenticated requests.
        """
        proxy_for_login = {
            'http': proxy_config.get('http'),
            'https': proxy_config.get('https'),
        }
        proxy_for_login = {k: v for k, v in proxy_for_login.items() if v}
        if not proxy_for_login:
            proxy_for_login = None

        return attempt_login_refresh(
            explicit_proxies=proxy_for_login,
            explicit_proxy_name=proxy_name,
            spider_uses_proxy=True,
        )

    def _login_and_verify(self, worker) -> tuple[bool, str | None]:
        """Run a login refresh on *worker* and verify it via fixed pages.

        Updates the worker's request handler with the new cookie when login
        succeeds.  Verification is performed through the same handler so the
        check uses the very session the spider will subsequently use.

        On verification failure the freshly issued cookie is cleared from
        both the worker's request handler *and* the global login state
        (:data:`state.refreshed_session_cookie` and
        :data:`state.logged_in_proxy_name`).  This prevents downstream code
        (e.g. ``fetch_engine`` cookie seeding) from handing a rejected
        cookie to other workers.

        Returns ``(verified, new_cookie)`` where ``verified`` is ``True``
        only when both login and fixed-page verification succeeded.
        """
        success, new_cookie, _ = self._do_login_for_proxy(
            worker.proxy_config, worker.proxy_name,
        )
        if not (success and new_cookie):
            return False, None

        worker._handler.config.javdb_session_cookie = new_cookie

        if not LOGIN_VERIFICATION_URLS:
            return True, new_cookie

        verified = verify_login_via_fixed_pages(
            worker._handler, worker.proxy_name,
            urls=LOGIN_VERIFICATION_URLS,
        )
        if verified:
            return True, new_cookie

        logger.warning(
            "[%s] Login response succeeded but fixed-page verification failed "
            "— discarding cookie on this worker",
            worker.proxy_name,
        )
        worker._handler.config.javdb_session_cookie = ''
        # ``attempt_login_refresh`` publishes the freshly-minted cookie on the
        # global state (see fetch/session.py).  A verification failure means
        # that cookie is untrusted, so clear it from the globals too —
        # otherwise downstream code (e.g. fetch_engine cookie seeding) would
        # hand the rejected cookie to other workers.  Use ``None`` (not an
        # empty string) to match the ``Optional[str]`` declaration in
        # ``state.py`` and the other clears in this module.
        state.refreshed_session_cookie = None
        state.logged_in_proxy_name = None
        return False, None

    def _find_and_login_next_worker(self, exclude: set | None = None) -> int | None:
        """Find the next proxy with remaining budget, login through it.

        On success sets ``logged_in_worker_id`` and updates the winning
        worker's cookie.  Returns the worker id, or ``None`` on failure.
        """
        exclude = exclude or set()

        for w in self._all_workers:
            if w.proxy_name in exclude:
                continue
            proxy_attempts = state.login_attempts_per_proxy.get(w.proxy_name, 0)
            if proxy_attempts >= LOGIN_ATTEMPTS_PER_PROXY_LIMIT:
                continue

            state.login_failures_per_proxy[w.proxy_name] = 0
            logger.info(
                "Switching login proxy to [%s] (logins: %d/%d)",
                w.proxy_name, proxy_attempts, LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
            )
            verified, _ = self._login_and_verify(w)
            if verified:
                self.logged_in_worker_id = w.worker_id
                logger.info(
                    "[%s] Logged in successfully, "
                    "becoming the logged-in worker for login-required pages",
                    w.proxy_name,
                )
                return w.worker_id

        return None

    # -- public API --------------------------------------------------------

    def is_login_worker(self, worker_proxy_name: str, worker_id: int) -> bool:
        """Check if *worker_id* should prioritise the login queue.

        Must be called while holding :attr:`lock`.
        """
        return use_login_queue_priority(
            self._login_proxy_name, worker_proxy_name,
            self.logged_in_worker_id, worker_id,
        )

    def handle_login_required(
        self,
        worker,
        task,
        video_code: str,
        login_queue: queue_module.Queue,
        task_queue: queue_module.Queue,
    ) -> None:
        """Full login state machine: re-login / switch / budget / requeue.

        This is a 1-to-1 extraction of ``ProxyWorker._handle_login_required``
        from ``scripts.spider.detail.parallel_mode``.  All branch logic, counters, and log
        messages are identical — the only difference is that ``worker``,
        ``task``, and ``queue`` references are parameterised so the same code
        works for spider, backfill, and alignment workers.
        """
        with self._lock:
            # -- Route to existing logged-in worker (not self) -------------
            if (
                self.logged_in_worker_id is not None
                and self.logged_in_worker_id != worker.worker_id
            ):
                logged_in_proxy = self._all_workers[
                    self.logged_in_worker_id
                ].proxy_name
                task.failed_proxies.discard(logged_in_proxy)
                login_queue.put(task)
                logger.info(
                    "%s Login required for %s, "
                    "routing to logged-in worker [%s]",
                    _task_worker_ctx(task.entry_index, worker.proxy_name),
                    video_code, logged_in_proxy,
                )
                return

            # -- Budget exhausted ------------------------------------------
            if (
                state.login_total_budget > 0
                and state.login_total_attempts >= state.login_total_budget
            ):
                logger.warning(
                    "%s Login budget exhausted (%d/%d), "
                    "treating %s as normal failure",
                    _task_worker_ctx(task.entry_index, worker.proxy_name),
                    state.login_total_attempts, state.login_total_budget,
                    video_code,
                )
                self.logged_in_worker_id = None
                state.refreshed_session_cookie = None
                state.logged_in_proxy_name = None
                task.failed_proxies.add(worker.proxy_name)
                requeue_front(task_queue, task)
                return

            # -- Self is the logged-in worker but session went stale -------
            if self.logged_in_worker_id == worker.worker_id:
                # If this task was already requeued after a verified login,
                # the session has been proven valid against fixed pages —
                # so a fresh login wall on this URL is *not* a session
                # problem.  Treat it as a normal page/proxy failure and let
                # another proxy try, instead of burning more login budget.
                if _get_login_verified(task):
                    logger.info(
                        "%s Login wall on %s after a verified login refresh "
                        "— treating as page/proxy failure (no extra login attempt)",
                        _task_worker_ctx(task.entry_index, worker.proxy_name),
                        video_code,
                    )
                    # Release login ownership so that if the cookie is
                    # actually invalid (e.g. quickly revoked after
                    # verification), other workers that hit LoginRequired
                    # will no longer delegate back to this worker via
                    # ``login_queue`` — otherwise the task could ping-pong
                    # between workers without ever triggering another login
                    # attempt.  The next login wall will go through the
                    # "no logged-in worker yet" branch and can pick a fresh
                    # proxy for re-login.
                    self.logged_in_worker_id = None
                    state.refreshed_session_cookie = None
                    state.logged_in_proxy_name = None
                    task.failed_proxies.add(worker.proxy_name)
                    requeue_front(task_queue, task)
                    return

                stale_count = (
                    state.login_failures_per_proxy.get(worker.proxy_name, 0) + 1
                )
                state.login_failures_per_proxy[worker.proxy_name] = stale_count
                proxy_attempts = state.login_attempts_per_proxy.get(
                    worker.proxy_name, 0,
                )

                need_switch = (
                    stale_count >= LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH
                    or proxy_attempts >= LOGIN_ATTEMPTS_PER_PROXY_LIMIT
                )

                if not need_switch:
                    logger.info(
                        "%s Session stale for %s, attempting re-login "
                        "(stale: %d/%d, logins: %d/%d)",
                        _task_worker_ctx(task.entry_index, worker.proxy_name),
                        video_code,
                        stale_count, LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH,
                        proxy_attempts, LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
                    )
                    verified, _ = self._login_and_verify(worker)
                    if verified:
                        self.logged_in_worker_id = worker.worker_id
                        _set_login_verified(task, True)
                        login_queue.put(task)
                        return
                    logger.warning(
                        "[%s] Re-login failed (or fixed-page verification failed), "
                        "switching proxy",
                        worker.proxy_name,
                    )
                else:
                    logger.info(
                        "%s Proxy reached switch threshold for %s "
                        "(stale: %d, logins: %d), switching to next proxy",
                        _task_worker_ctx(task.entry_index, worker.proxy_name),
                        video_code,
                        stale_count, proxy_attempts,
                    )

                # Try to switch to another proxy
                self.logged_in_worker_id = None
                state.refreshed_session_cookie = None
                state.logged_in_proxy_name = None

                next_wid = self._find_and_login_next_worker(
                    exclude={worker.proxy_name},
                )
                if next_wid is not None:
                    task.failed_proxies.discard(
                        self._all_workers[next_wid].proxy_name,
                    )
                    _set_login_verified(task, True)
                    login_queue.put(task)
                    return

                # No other proxy — give current proxy another round if budget
                # remains.
                cur_attempts = state.login_attempts_per_proxy.get(
                    worker.proxy_name, 0,
                )
                if cur_attempts < LOGIN_ATTEMPTS_PER_PROXY_LIMIT:
                    state.login_failures_per_proxy[worker.proxy_name] = 0
                    verified, _ = self._login_and_verify(worker)
                    if verified:
                        self.logged_in_worker_id = worker.worker_id
                        _set_login_verified(task, True)
                        login_queue.put(task)
                        return

                # Exhausted — normal proxy failure path
                task.failed_proxies.add(worker.proxy_name)
                requeue_front(task_queue, task)
                return

            # -- No logged-in worker yet — try to become one ---------------

            # Honour LOGIN_PROXY_NAME delegation if that proxy still viable
            if (
                self._login_proxy_name
                and worker.proxy_name != self._login_proxy_name
            ):
                lp_attempts = state.login_attempts_per_proxy.get(
                    self._login_proxy_name, 0,
                )
                if lp_attempts < LOGIN_ATTEMPTS_PER_PROXY_LIMIT:
                    task.failed_proxies.discard(self._login_proxy_name)
                    login_queue.put(task)
                    logger.info(
                        "%s Login required for %s, "
                        "routing to LOGIN_PROXY_NAME worker [%s]",
                        _task_worker_ctx(task.entry_index, worker.proxy_name),
                        video_code, self._login_proxy_name,
                    )
                    return

            # Try login on own proxy
            own_attempts = state.login_attempts_per_proxy.get(
                worker.proxy_name, 0,
            )
            if own_attempts < LOGIN_ATTEMPTS_PER_PROXY_LIMIT:
                verified, _ = self._login_and_verify(worker)
                if verified:
                    self.logged_in_worker_id = worker.worker_id
                    logger.info(
                        "[%s] Logged in successfully, "
                        "becoming the logged-in worker for login-required pages",
                        worker.proxy_name,
                    )
                    _set_login_verified(task, True)
                    login_queue.put(task)
                    return

            # Try every other proxy
            next_wid = self._find_and_login_next_worker(
                exclude={worker.proxy_name},
            )
            if next_wid is not None:
                task.failed_proxies.discard(
                    self._all_workers[next_wid].proxy_name,
                )
                _set_login_verified(task, True)
                login_queue.put(task)
                return

            # Nothing worked — normal proxy failure path
            logger.warning(
                "%s Login required for %s but no proxy available, "
                "treating as normal failure",
                _task_worker_ctx(task.entry_index, worker.proxy_name),
                video_code,
            )
            task.failed_proxies.add(worker.proxy_name)
            requeue_front(task_queue, task)
