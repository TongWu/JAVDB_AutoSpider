"""Shared login-queue routing and login coordination for parallel workers.

Used by ``ProxyWorker`` (spider), ``BackfillWorker`` (migration), and
``AlignWorker`` (inventory alignment) — single source of truth for all
login retry / proxy-switch / budget logic.

When the cross-runtime ``GlobalLoginState`` Durable Object is configured
(:data:`state.global_login_state_client` is not ``None``), all calls into
:meth:`_login_and_verify` are wrapped in a DO ``acquire_lease`` ↔
``release_lease`` mutex so that **at most one runner globally** performs
the actual login at a time.  Runners that lose the race **park** the
offending tasks in :attr:`LoginCoordinator._pending_login_tasks` and a
background daemon (`_poll_login_state_loop`) re-dispatches them once the
winning runner publishes the fresh cookie to the DO — meanwhile the rest
of the worker pool continues processing non-login tasks unimpeded.

Fail-open contract: when the DO is unreachable the coordinator silently
falls back to the legacy per-runner behaviour; nothing in this module
ever raises a ``LoginStateUnavailable`` to the caller.
"""

from __future__ import annotations

import queue as queue_module
import threading
import time
from collections import deque
from typing import Deque, Optional, Tuple

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_platform.login_state_client import (
    LoginStateUnavailable,
)
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


# ── Cross-runtime DO tuning ──────────────────────────────────────────────
# Lease TTL: a successful JavDB login + fixed-page verification typically
# completes in 5–30 s on this runner.  60 s gives ample headroom while
# still letting another runner reclaim within a minute if this process
# crashes mid-login.
_DO_LEASE_TTL_MS = 60_000
# Poll interval for the parked-task dispatcher.  Three seconds keeps
# perceived latency low for the user-facing tasks waiting on a re-login,
# while staying well within Cloudflare's free-tier request budget (a
# typical run parks ≤ a handful of tasks for a few seconds at most).
_POLL_INTERVAL_SEC = 3.0
# After the parked queue drains, the poll thread keeps running for this
# many idle iterations before exiting — so a quick succession of
# park-then-dispatch-then-park does not pay the thread spawn cost twice.
_POLL_IDLE_ITERATIONS_BEFORE_EXIT = 5


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
        # Tasks parked while another runner holds the DO re-login lease.
        # Each entry is ``(worker_proxy_name, task, login_queue)`` so the
        # poller knows where to re-dispatch once the winning runner
        # publishes a fresh cookie.  Always touched while holding
        # ``self._lock``.
        self._pending_login_tasks: Deque[
            Tuple[str, object, queue_module.Queue]
        ] = deque()
        # Daemon thread that drains :attr:`_pending_login_tasks`; lazily
        # started on first park, exits on its own after a few idle
        # iterations.  Touched only while holding ``self._lock`` for
        # creation; the thread itself uses ``self._lock`` to mutate state.
        self._poll_thread: Optional[threading.Thread] = None
        # P2-C — wall-clock ms epoch until which the cross-runner login
        # pool is in cooldown after repeated failures crossed the
        # Worker-side ``LOGIN_COOLDOWN_THRESHOLD`` inside
        # ``LOGIN_COOLDOWN_WINDOW_SEC``.  When non-zero and in the future,
        # this coordinator parks all ``LoginRequired`` tasks instead of
        # attempting another login — the poller resumes dispatching once
        # the cooldown expires (or another runner publishes a fresh
        # cookie, whichever happens first).  ``0`` means "no cooldown".
        # Always read/written under ``self._lock``.
        self._cooldown_until_ms: int = 0

    @property
    def lock(self) -> threading.Lock:
        """Expose the lock for ``_get_next_task`` synchronization."""
        return self._lock

    # -- DO lease / park / poll helpers ------------------------------------

    def _try_acquire_login_lease(self, hint_proxy_name: str) -> bool:
        """Try to take the cross-runtime re-login mutex.

        Returns:
            ``True`` when the caller may proceed with a real login attempt
            (DO not configured, lease freshly acquired, or this runner
            already owns it via an idempotent renewal).
            ``False`` when another runner currently owns the lease;
            caller should park the task instead.

        P2-C: even when ``acquired=True``, the response may carry
        ``cooldown_until_ms > server_time_ms`` after the cross-runner
        failure budget was exhausted within the rolling window.  In that
        case we record the cooldown deadline, immediately release the
        lease so other runners aren't blocked, and return ``False`` so
        the caller parks its task — the poller will replay parked tasks
        once the cooldown expires.
        """
        client = state.global_login_state_client
        if client is None:
            return True  # fail-open: pre-DO behaviour
        try:
            result = client.acquire_lease(
                state.runtime_holder_id,
                hint_proxy_name,
                _DO_LEASE_TTL_MS,
            )
        except LoginStateUnavailable as exc:
            logger.warning(
                "DO acquire_lease failed (%s) — proceeding with local login "
                "(fail-open)",
                exc,
            )
            return True
        # P2-C: honour the cooldown signal regardless of who owns the
        # lease.  Caller MUST be holding ``self._lock`` so this update
        # races safely with the poller.
        if (
            result.cooldown_until_ms > 0
            and result.cooldown_until_ms > result.server_time_ms
        ):
            previous = self._cooldown_until_ms
            self._cooldown_until_ms = max(previous, result.cooldown_until_ms)
            if previous != self._cooldown_until_ms:
                logger.warning(
                    "P2-C login cooldown active (until=%d, recent=%d) — "
                    "parking login task; daemon will not initiate another "
                    "attempt before cooldown lifts",
                    self._cooldown_until_ms,
                    result.recent_attempt_count,
                )
            # Release any lease we may have just acquired so peer runners
            # observing the same cooldown can still drain their queues
            # (the cooldown is informational; the lease is still
            # exclusive).  Best-effort — failure here is harmless.
            if result.acquired:
                try:
                    client.release_lease(state.runtime_holder_id)
                except LoginStateUnavailable:
                    pass
            return False
        if result.acquired:
            return True
        logger.info(
            "DO re-login lease held by %s (target=%s, expires_in=%dms) — "
            "parking task and waiting for the winner to publish",
            result.holder_id,
            result.target_proxy_name,
            max(0, result.lease_expires_at - result.server_time_ms),
        )
        return False

    def _record_login_attempt(
        self,
        proxy_name: str,
        outcome: str,
    ) -> None:
        """P2-C — best-effort `record_attempt` after each login attempt.

        Called from the lease-aware wrappers regardless of whether the
        attempt succeeded.  Uses the post-append ``cooldown_until_ms``
        from the response to update the local cooldown clock so a
        subsequent local-only ``handle_login_required`` (no DO acquire
        between calls) still parks correctly.  Never raises — the
        cooldown is a soft signal and a missed record is harmless.
        """
        client = state.global_login_state_client
        if client is None:
            return
        try:
            result = client.record_attempt(
                state.runtime_holder_id, proxy_name, outcome,
            )
        except LoginStateUnavailable as exc:
            logger.debug(
                "DO record_attempt(%s) failed (%s) — ignored", outcome, exc,
            )
            return
        except Exception as exc:  # noqa: BLE001 — never break callers on bookkeeping
            logger.debug(
                "DO record_attempt(%s) raised unexpectedly (%s) — ignored",
                outcome, exc,
            )
            return
        # ``result`` may be a MagicMock or any duck-typed object in tests;
        # coerce to int defensively so a non-numeric attribute can't
        # break the post-attempt path.  A failed coercion just skips
        # the cooldown update — no harm, the next acquire will resync
        # against the DO's authoritative view anyway.
        try:
            cooldown = int(result.cooldown_until_ms)
            server_now = int(result.server_time_ms)
        except (AttributeError, TypeError, ValueError):
            return
        if cooldown > server_now:
            with self._lock:
                self._cooldown_until_ms = max(
                    self._cooldown_until_ms, cooldown,
                )

    def _release_login_lease(self) -> None:
        """Best-effort release of the DO re-login mutex.

        Safe to call even when the lease was never acquired (DO not
        configured, or another runner already reclaimed an expired lease).
        Errors are swallowed — the lease will time out on its own at
        worst.
        """
        client = state.global_login_state_client
        if client is None:
            return
        try:
            client.release_lease(state.runtime_holder_id)
        except LoginStateUnavailable as exc:
            logger.warning(
                "DO release_lease failed (%s) — relying on TTL for cleanup",
                exc,
            )

    def _invalidate_do_state_if_owned(self) -> None:
        """Mark the current published cookie as stale.

        Called when the local logged-in worker observes a fresh login
        wall on a previously-verified session: the cookie we (or another
        runner) published is no longer valid, so other runners should
        stop trusting it ASAP.  Uses the optimistic version lock so we
        never wipe a newer cookie that someone else just published.
        """
        client = state.global_login_state_client
        version = state.current_login_state_version
        if client is None or version is None:
            return
        try:
            result = client.invalidate(version)
        except LoginStateUnavailable as exc:
            logger.warning("DO invalidate failed (%s) — ignored", exc)
            return
        if result.invalidated:
            state.current_login_state_version = result.current_version
            logger.info("DO invalidated stale cookie (new version=%d)", result.current_version)
        else:
            # Stale view: someone else already published a fresher cookie
            # while we were running this branch.  Pick it up via the
            # poller on its next tick.
            state.current_login_state_version = result.current_version
            logger.info(
                "DO invalidate no-op (current_version=%d > our %d) — "
                "polling will pick up the fresher cookie",
                result.current_version, version,
            )

    def _park_login_task(
        self,
        worker,
        task,
        login_queue: queue_module.Queue,
    ) -> None:
        """Add *task* to the pending queue and ensure the poller is running.

        Caller MUST hold :attr:`_lock`.  After this, the calling worker
        thread should ``return`` immediately — the parked task will be
        re-dispatched to ``login_queue`` by :meth:`_poll_login_state_loop`
        as soon as another runner publishes a fresh cookie to the DO.
        """
        self._pending_login_tasks.append((worker.proxy_name, task, login_queue))
        if self._poll_thread is None or not self._poll_thread.is_alive():
            self._poll_thread = threading.Thread(
                target=self._poll_login_state_loop,
                name="login-state-poller",
                daemon=True,
            )
            self._poll_thread.start()
            logger.info(
                "Started login-state poller (daemon) — %d task(s) parked",
                len(self._pending_login_tasks),
            )

    def _poll_login_state_loop(self) -> None:
        """Drain :attr:`_pending_login_tasks` as the DO publishes new cookies.

        Periodically polls the GlobalLoginState DO; whenever the observed
        ``version`` advances past our local view, injects the fresh cookie
        into the matching worker's request handler, sets that worker as
        the new ``logged_in_worker_id``, and dispatches every parked task
        to its ``login_queue``.

        P2-C — when the cross-runner login cooldown is active (see
        :attr:`_cooldown_until_ms`), the poller does NOT initiate a
        fresh login attempt; it waits for the cooldown to expire and
        then re-dispatches all parked tasks back through the normal
        ``handle_login_required`` flow (treating them as if they just
        hit a fresh login wall — the next ``acquire_lease`` will
        observe the cooldown has lifted).

        Exits after a few consecutive idle ticks once the parked queue is
        empty so the runtime does not leak threads after a brief contention
        episode.  A future park will respawn the thread.
        """
        idle_iterations = 0
        while True:
            time.sleep(_POLL_INTERVAL_SEC)

            with self._lock:
                if not self._pending_login_tasks:
                    idle_iterations += 1
                    if idle_iterations >= _POLL_IDLE_ITERATIONS_BEFORE_EXIT:
                        # Drop the reference so the next park will spawn a
                        # fresh thread rather than racing against an exiting one.
                        self._poll_thread = None
                        logger.info(
                            "Login-state poller exiting after %d idle ticks",
                            idle_iterations,
                        )
                        return
                    continue
                idle_iterations = 0
                # P2-C — if we know about an active cooldown, check
                # whether it has expired.  If so, drain parked tasks
                # back to their original ``login_queue`` and clear the
                # cooldown clock so the next ``handle_login_required``
                # cycle proceeds.  We re-queue at the front so the
                # post-cooldown attempts hit the workers as soon as
                # possible.
                now_ms = int(time.time() * 1000)
                if (
                    self._cooldown_until_ms > 0
                    and now_ms >= self._cooldown_until_ms
                ):
                    drained = list(self._pending_login_tasks)
                    self._pending_login_tasks.clear()
                    self._cooldown_until_ms = 0
                    logger.info(
                        "P2-C login cooldown lifted — re-dispatching %d "
                        "parked task(s) to retry login",
                        len(drained),
                    )
                    for proxy_name, task, login_queue in drained:
                        requeue_front(login_queue, task)
                    continue
                if (
                    self._cooldown_until_ms > 0
                    and now_ms < self._cooldown_until_ms
                ):
                    # Cooldown still active — skip the version-bump check
                    # so we don't burn DO calls during a back-off window
                    # that we already know will reject any acquire.
                    continue

            client = state.global_login_state_client
            if client is None:
                # DO went away mid-flight (extremely unlikely).  Drain the
                # parked tasks back to their original queues so the rest
                # of the run still progresses; treat them as proxy
                # failures and let the normal retry path take over.
                with self._lock:
                    drained = list(self._pending_login_tasks)
                    self._pending_login_tasks.clear()
                    self._poll_thread = None
                for proxy_name, task, login_queue in drained:
                    if hasattr(task, "failed_proxies"):
                        task.failed_proxies.add(proxy_name)
                    requeue_front(login_queue, task)
                logger.warning(
                    "DO disappeared while %d tasks were parked — "
                    "re-dispatched as proxy failures",
                    len(drained),
                )
                return

            try:
                snapshot = client.get_state()
            except LoginStateUnavailable as exc:
                logger.warning(
                    "Login-state poller: get_state failed (%s) — will retry",
                    exc,
                )
                continue

            current_version = state.current_login_state_version or 0
            if snapshot.version <= current_version:
                continue
            if not snapshot.proxy_name or not snapshot.cookie:
                # Version advanced via an ``invalidate`` (cookie cleared),
                # not a fresh ``publish`` — keep polling for the next
                # publish to come in.
                state.current_login_state_version = snapshot.version
                continue

            with self._lock:
                # Re-check after re-entering the lock: another worker may
                # have raced and published locally already.
                if (state.current_login_state_version or 0) >= snapshot.version:
                    continue
                state.current_login_state_version = snapshot.version
                state.refreshed_session_cookie = snapshot.cookie
                state.logged_in_proxy_name = snapshot.proxy_name

                injected_worker_id: Optional[int] = None
                for w in self._all_workers:
                    if w.proxy_name == snapshot.proxy_name:
                        w._handler.config.javdb_session_cookie = snapshot.cookie
                        injected_worker_id = w.worker_id
                        break

                if injected_worker_id is None:
                    logger.warning(
                        "Poller: published proxy '%s' is not in this runner's "
                        "pool — %d task(s) remain parked",
                        snapshot.proxy_name,
                        len(self._pending_login_tasks),
                    )
                    continue

                self.logged_in_worker_id = injected_worker_id
                tasks_to_dispatch = list(self._pending_login_tasks)
                self._pending_login_tasks.clear()

            for proxy_name, task, login_queue in tasks_to_dispatch:
                if hasattr(task, "failed_proxies"):
                    task.failed_proxies.discard(snapshot.proxy_name)
                _set_login_verified(task, True)
                login_queue.put(task)
            logger.info(
                "Poller: dispatched %d parked task(s) to login_queue after "
                "DO version advanced to %d (proxy=%s)",
                len(tasks_to_dispatch), snapshot.version, snapshot.proxy_name,
            )

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

        Note: this method assumes the caller already holds the DO
        re-login lease (when DO is configured); it does not arbitrate
        per-iteration.  Use :meth:`_find_and_login_next_worker_with_lease`
        from the public ``handle_login_required`` paths.
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

    # -- DO-lease-aware wrappers around the private login helpers ----------

    def _login_and_verify_with_lease(
        self,
        worker,
        task,
        login_queue: queue_module.Queue,
    ) -> Tuple[bool, Optional[str], bool]:
        """Run :meth:`_login_and_verify` under the cross-runtime DO lease.

        Returns ``(verified, new_cookie, parked)``:

        - ``parked=True``: the lease is held by another runner OR the
          P2-C cross-runner cooldown is active; ``task`` has been added
          to :attr:`_pending_login_tasks` and the poller will
          re-dispatch it (after the cooldown lifts or after another
          runner publishes a fresh cookie, whichever happens first).
          Caller MUST ``return`` from :meth:`handle_login_required`
          immediately and MUST NOT requeue the task or mark it as
          failed.
        - ``parked=False``: the local login attempt completed
          (``verified`` may be True or False); existing branch logic
          applies.  Each completed attempt is reported back to the DO
          via ``record_attempt`` so the next acquire across all runners
          sees the up-to-date failure ratio.
        """
        if not self._try_acquire_login_lease(worker.proxy_name):
            self._park_login_task(worker, task, login_queue)
            return False, None, True
        try:
            verified, new_cookie = self._login_and_verify(worker)
        finally:
            self._release_login_lease()
        # P2-C: bookkeeping fires AFTER the lease release so the next
        # runner doesn't have to wait on our network round-trip.
        self._record_login_attempt(
            worker.proxy_name, "success" if verified else "failure",
        )
        return verified, new_cookie, False

    def _find_and_login_next_worker_with_lease(
        self,
        exclude: set | None,
        task,
        login_queue: queue_module.Queue,
        hint_proxy_name: str,
    ) -> Tuple[Optional[int], bool]:
        """Run :meth:`_find_and_login_next_worker` under the DO lease.

        Returns ``(next_worker_id, parked)``.  ``parked=True`` semantics
        match :meth:`_login_and_verify_with_lease` — caller must
        ``return`` immediately.  ``hint_proxy_name`` is recorded as the
        lease's ``target_proxy_name`` for diagnostics; the lease is held
        for the entire iteration regardless of which proxy ultimately
        succeeds.
        """
        if not self._try_acquire_login_lease(hint_proxy_name):
            self._park_login_task_for_unknown_target(task, login_queue, hint_proxy_name)
            return None, True
        try:
            next_wid = self._find_and_login_next_worker(exclude=exclude)
        finally:
            self._release_login_lease()
        # P2-C: report the aggregate outcome of the multi-proxy sweep so
        # the cross-runner failure budget reflects this attempt.  We
        # attribute it to the proxy that ultimately succeeded
        # (``next_wid``) or to the hint proxy when nothing worked.
        if next_wid is not None:
            success_proxy = self._all_workers[next_wid].proxy_name
            self._record_login_attempt(success_proxy, "success")
        else:
            self._record_login_attempt(hint_proxy_name, "failure")
        return next_wid, False

    def _park_login_task_for_unknown_target(
        self,
        task,
        login_queue: queue_module.Queue,
        hint_proxy_name: str,
    ) -> None:
        """Park *task* even though we don't know which proxy will end up logging in.

        Reuses :meth:`_park_login_task` with the hint proxy name; the
        poller treats ``proxy_name`` as a free-form tag (only used to
        ``failed_proxies.discard`` on the eventual published proxy).
        """
        # Synthesize a worker-shaped object since we don't have one to
        # hand; the poller only reads ``proxy_name``.
        class _PsuedoWorker:  # noqa: N801 — internal class kept name-pyish
            def __init__(self, proxy_name: str):
                self.proxy_name = proxy_name
        self._park_login_task(_PsuedoWorker(hint_proxy_name), task, login_queue)

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

                # Inform the cross-runtime DO that the published cookie is
                # bad before we attempt to publish a replacement; the
                # optimistic version lock prevents us from clobbering a
                # newer cookie a peer may have just published while we
                # were spinning up.
                self._invalidate_do_state_if_owned()

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
                    verified, _, parked = self._login_and_verify_with_lease(
                        worker, task, login_queue,
                    )
                    if parked:
                        return
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

                next_wid, parked = self._find_and_login_next_worker_with_lease(
                    exclude={worker.proxy_name},
                    task=task,
                    login_queue=login_queue,
                    hint_proxy_name=worker.proxy_name,
                )
                if parked:
                    return
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
                    verified, _, parked = self._login_and_verify_with_lease(
                        worker, task, login_queue,
                    )
                    if parked:
                        return
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
                verified, _, parked = self._login_and_verify_with_lease(
                    worker, task, login_queue,
                )
                if parked:
                    return
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
            next_wid, parked = self._find_and_login_next_worker_with_lease(
                exclude={worker.proxy_name},
                task=task,
                login_queue=login_queue,
                hint_proxy_name=worker.proxy_name,
            )
            if parked:
                return
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
