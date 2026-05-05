"""Tests for the cross-runtime ``GlobalLoginState`` integration in
:class:`packages.python.javdb_spider.fetch.login_coordinator.LoginCoordinator`.

Focus areas (mirrors plan §B4):

1. **Parking**: when the DO ``acquire_lease`` returns ``acquired=False``,
   the calling worker thread must NOT block; the task is appended to
   ``_pending_login_tasks`` and a daemon poller is started exactly once.
2. **Polling**: when the poller observes a fresh ``version`` from the DO
   it must inject the new cookie into the matching worker, set that
   worker as ``logged_in_worker_id``, and re-dispatch every parked task
   to its ``login_queue``.
3. **Invalidation**: when self is the logged-in worker but the session
   went stale, ``_invalidate_do_state_if_owned`` must call
   ``client.invalidate(version)`` with the locally-cached version as the
   optimistic-lock token before any re-login attempt.
4. **Fail-open**: every DO error path must let the legacy local-only
   behaviour proceed without raising to the caller.

The DO endpoint behaviour itself is covered by the Worker repo's vitest
suite — here we only verify the Python wiring.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import packages.python.javdb_spider.runtime.state as state_mod  # noqa: E402
from packages.python.javdb_platform.login_state_client import (  # noqa: E402
    AcquireLeaseResult,
    InvalidateResult,
    LoginStateGetResult,
    LoginStateUnavailable,
    PublishResult,
    RecordAttemptResult,
    ReleaseLeaseResult,
)
from packages.python.javdb_spider.fetch import login_coordinator as lc_mod  # noqa: E402
from packages.python.javdb_spider.fetch.login_coordinator import (  # noqa: E402
    LoginCoordinator,
)


# ── shared fixtures / helpers ───────────────────────────────────────────────


def _make_worker(worker_id: int, proxy_name: str):
    """Build a duck-typed worker with the attributes LoginCoordinator reads."""
    handler = SimpleNamespace(config=SimpleNamespace(javdb_session_cookie=""))
    return SimpleNamespace(
        worker_id=worker_id,
        proxy_name=proxy_name,
        proxy_config={"http": f"http://{proxy_name}:1"},
        _handler=handler,
        is_alive=lambda: True,
    )


def _make_task(entry_index: str = "1/10"):
    """Engine-task look-alike with the duck-typed fields the coordinator uses."""
    return SimpleNamespace(
        entry_index=entry_index,
        failed_proxies=set(),
        login_verified_after_refresh=False,
        meta={"video_code": "ABC-001"},
    )


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Snapshot and restore the ``state`` module's globals around each test.

    The coordinator mutates ``state.global_login_state_client``,
    ``state.current_login_state_version`` etc.; without this fixture a
    failure in one test would leak into the next.
    """
    monkeypatch.setattr(state_mod, "global_login_state_client", None, raising=False)
    monkeypatch.setattr(state_mod, "current_login_state_version", None, raising=False)
    monkeypatch.setattr(state_mod, "logged_in_proxy_name", None, raising=False)
    monkeypatch.setattr(state_mod, "refreshed_session_cookie", None, raising=False)
    monkeypatch.setattr(state_mod, "runtime_holder_id", "runner-test", raising=False)
    monkeypatch.setattr(state_mod, "login_attempts_per_proxy", {}, raising=False)
    monkeypatch.setattr(state_mod, "login_failures_per_proxy", {}, raising=False)
    monkeypatch.setattr(state_mod, "login_total_attempts", 0, raising=False)
    monkeypatch.setattr(state_mod, "login_total_budget", 0, raising=False)
    yield


# ── _try_acquire_login_lease ────────────────────────────────────────────────


class TestTryAcquireLoginLease:
    def test_returns_true_when_do_not_configured(self):
        """No DO → fail-open → caller proceeds with legacy local login."""
        coord = LoginCoordinator(all_workers=[_make_worker(0, "P1")])
        assert coord._try_acquire_login_lease("P1") is True

    def test_returns_true_when_lease_acquired(self):
        coord = LoginCoordinator(all_workers=[_make_worker(0, "P1")])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True,
            holder_id="runner-test",
            target_proxy_name="P1",
            lease_expires_at=99_999,
            server_time_ms=0,
        )
        state_mod.global_login_state_client = client

        assert coord._try_acquire_login_lease("P1") is True
        client.acquire_lease.assert_called_once_with(
            "runner-test", "P1", lc_mod._DO_LEASE_TTL_MS,
        )

    def test_returns_false_when_lease_held_by_other(self):
        coord = LoginCoordinator(all_workers=[_make_worker(0, "P1")])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=False,
            holder_id="other-runner",
            target_proxy_name="P1",
            lease_expires_at=10_000,
            server_time_ms=5_000,
        )
        state_mod.global_login_state_client = client
        assert coord._try_acquire_login_lease("P1") is False

    def test_fail_open_on_network_error(self):
        """Network errors degrade to legacy local login (return True)."""
        coord = LoginCoordinator(all_workers=[_make_worker(0, "P1")])
        client = MagicMock()
        client.acquire_lease.side_effect = LoginStateUnavailable("network error")
        state_mod.global_login_state_client = client
        assert coord._try_acquire_login_lease("P1") is True


# ── _release_login_lease + _invalidate_do_state_if_owned ────────────────────


class TestReleaseAndInvalidate:
    def test_release_no_op_when_do_not_configured(self):
        coord = LoginCoordinator(all_workers=[])
        coord._release_login_lease()  # must not raise

    def test_release_swallows_errors(self):
        client = MagicMock()
        client.release_lease.side_effect = LoginStateUnavailable("boom")
        state_mod.global_login_state_client = client
        coord = LoginCoordinator(all_workers=[])
        coord._release_login_lease()
        client.release_lease.assert_called_once_with("runner-test")

    def test_invalidate_uses_optimistic_lock_with_local_version(self):
        client = MagicMock()
        client.invalidate.return_value = InvalidateResult(
            invalidated=True, current_version=8, server_time_ms=0,
        )
        state_mod.global_login_state_client = client
        state_mod.current_login_state_version = 7
        coord = LoginCoordinator(all_workers=[])
        coord._invalidate_do_state_if_owned()
        client.invalidate.assert_called_once_with(7)
        assert state_mod.current_login_state_version == 8

    def test_invalidate_resyncs_version_when_stale(self):
        client = MagicMock()
        client.invalidate.return_value = InvalidateResult(
            invalidated=False, current_version=12, server_time_ms=0,
        )
        state_mod.global_login_state_client = client
        state_mod.current_login_state_version = 7
        coord = LoginCoordinator(all_workers=[])
        coord._invalidate_do_state_if_owned()
        # Even when the lock fails, we re-sync to the server's view so the
        # next invalidate call uses the right token.
        assert state_mod.current_login_state_version == 12

    def test_invalidate_no_op_without_version(self):
        client = MagicMock()
        state_mod.global_login_state_client = client
        state_mod.current_login_state_version = None
        coord = LoginCoordinator(all_workers=[])
        coord._invalidate_do_state_if_owned()
        client.invalidate.assert_not_called()


# ── _login_and_verify_with_lease ────────────────────────────────────────────


class TestLoginAndVerifyWithLease:
    def test_proceeds_when_lease_acquired(self):
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=99_999, server_time_ms=0,
        )
        client.release_lease.return_value = ReleaseLeaseResult(released=True, server_time_ms=0)
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie-NEW")):
            verified, cookie, parked = coord._login_and_verify_with_lease(
                worker, task, login_queue,
            )

        assert parked is False
        assert verified is True
        assert cookie == "cookie-NEW"
        client.release_lease.assert_called_once_with("runner-test")

    def test_releases_lease_even_when_login_raises(self):
        """The ``finally`` in the wrapper must release even if the inner
        login throws."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=99_999, server_time_ms=0,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()

        with patch.object(coord, "_login_and_verify", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                coord._login_and_verify_with_lease(worker, _make_task(), login_queue)
        client.release_lease.assert_called_once_with("runner-test")

    def test_parks_task_when_lease_held_by_other(self):
        """parked=True → task in pending queue, poller started, no login attempt."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=False, holder_id="winner", target_proxy_name="P1",
            lease_expires_at=10_000, server_time_ms=5_000,
        )
        # get_state is invoked by the daemon poller; return "no progress yet"
        # so the poller idles instead of dispatching.
        client.get_state.return_value = LoginStateGetResult(
            proxy_name=None, cookie=None, version=0,
            last_verified_at=0, has_active_lease=True, server_time_ms=0,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(coord, "_login_and_verify") as mock_login:
            verified, cookie, parked = coord._login_and_verify_with_lease(
                worker, task, login_queue,
            )

        assert parked is True
        assert verified is False
        assert cookie is None
        # No real login attempt was made.
        mock_login.assert_not_called()
        # Task is queued in the pending list.
        assert len(coord._pending_login_tasks) == 1
        assert coord._pending_login_tasks[0][0] == "P1"
        assert coord._pending_login_tasks[0][1] is task
        # Daemon poller is up.
        assert coord._poll_thread is not None
        assert coord._poll_thread.is_alive()


# ── _poll_login_state_loop ──────────────────────────────────────────────────


class TestPollerDispatch:
    def _wait_until(self, predicate, timeout=5.0):
        """Spin-wait until ``predicate()`` becomes truthy or the timeout elapses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_poller_injects_cookie_and_dispatches_parked_tasks(self):
        """A version bump + matching proxy → cookie injected, tasks dispatched.

        Drives the full park-then-dispatch flow end-to-end with a real
        daemon thread + a tiny poll interval so the test stays under
        a second.
        """
        worker_p1 = _make_worker(0, "P1")
        worker_p2 = _make_worker(1, "P2")
        coord = LoginCoordinator(all_workers=[worker_p1, worker_p2])

        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=False, holder_id="winner", target_proxy_name="P2",
            lease_expires_at=10_000, server_time_ms=5_000,
        )
        # Two consecutive get_state calls: first "still no cookie", then
        # the published one.  The poller picks up the second.
        client.get_state.side_effect = [
            LoginStateGetResult(
                proxy_name=None, cookie=None, version=0,
                last_verified_at=0, has_active_lease=True, server_time_ms=0,
            ),
            LoginStateGetResult(
                proxy_name="P2", cookie="cookie-NEW", version=5,
                last_verified_at=99_999, has_active_lease=False, server_time_ms=0,
            ),
        ]
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        # Speed up the poll for the test.
        with patch.object(lc_mod, "_POLL_INTERVAL_SEC", 0.05):
            with patch.object(coord, "_login_and_verify"):
                _, _, parked = coord._login_and_verify_with_lease(
                    worker_p1, task, login_queue,
                )
            assert parked is True
            # Wait for the poller to dispatch.
            ok = self._wait_until(lambda: not login_queue.empty(), timeout=3.0)
            assert ok, "poller did not dispatch the parked task in time"

        dispatched = login_queue.get_nowait()
        assert dispatched is task
        # Cookie was injected into the WORKER MATCHING THE PUBLISHED PROXY,
        # not the parking worker (P2 not P1).
        assert worker_p2._handler.config.javdb_session_cookie == "cookie-NEW"
        assert worker_p1._handler.config.javdb_session_cookie == ""
        assert coord.logged_in_worker_id == worker_p2.worker_id
        # Local view caught up.
        assert state_mod.current_login_state_version == 5
        assert state_mod.refreshed_session_cookie == "cookie-NEW"
        assert state_mod.logged_in_proxy_name == "P2"
        # Task's failed_proxies cleared so the inheritor can serve it.
        assert "P2" not in task.failed_proxies
        # And it's marked verified so the next login wall on this URL is
        # treated as a page failure, not a re-login trigger.
        assert task.login_verified_after_refresh is True

    def test_poller_skips_when_published_proxy_not_in_pool(self):
        """If the DO publishes for a proxy this runner doesn't have, leave
        the task parked (warning logged, no crash)."""
        worker_p1 = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker_p1])

        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=False, holder_id="winner", target_proxy_name="ZZ",
            lease_expires_at=10_000, server_time_ms=5_000,
        )
        client.get_state.return_value = LoginStateGetResult(
            proxy_name="ZZ-not-in-our-pool", cookie="cookie-NEW", version=5,
            last_verified_at=99_999, has_active_lease=False, server_time_ms=0,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()

        with patch.object(lc_mod, "_POLL_INTERVAL_SEC", 0.05):
            with patch.object(coord, "_login_and_verify"):
                _, _, parked = coord._login_and_verify_with_lease(
                    worker_p1, _make_task(), login_queue,
                )
            assert parked is True
            # Give the poller a couple of ticks; it should NOT dispatch.
            time.sleep(0.3)

        assert login_queue.empty()
        assert worker_p1._handler.config.javdb_session_cookie == ""

    def test_poller_handles_do_disappearance(self):
        """If the DO client is unset mid-flight, parked tasks are returned
        to the local task queue as proxy failures (no leak)."""
        worker_p1 = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker_p1])

        # Acquire returns "held by other" so we park.
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=False, holder_id="winner", target_proxy_name="P1",
            lease_expires_at=10_000, server_time_ms=5_000,
        )
        # The poller will see ``state.global_login_state_client = None``
        # *before* its first get_state, so this stub should never be hit.
        client.get_state.side_effect = AssertionError("get_state must not run")
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(lc_mod, "_POLL_INTERVAL_SEC", 0.05):
            with patch.object(coord, "_login_and_verify"):
                _, _, parked = coord._login_and_verify_with_lease(
                    worker_p1, task, login_queue,
                )
            assert parked is True
            # Yank the DO mid-flight.
            state_mod.global_login_state_client = None
            ok = self._wait_until(lambda: not login_queue.empty(), timeout=2.0)
            assert ok, "poller did not re-queue parked tasks after DO disappeared"

        dispatched = login_queue.get_nowait()
        assert dispatched is task
        assert "P1" in task.failed_proxies


# ── End-to-end fail-open ─────────────────────────────────────────────────────


class TestHandleLoginRequiredFailOpenWithoutDO:
    """End-to-end proof that ``handle_login_required`` behaves identically to
    the pre-DO path when ``state.global_login_state_client is None``.

    Concretely: the public entry point must (a) never touch the DO, (b) never
    park tasks, (c) drive the legacy ``_login_and_verify`` flow, and (d)
    leave login_queue / logged_in_worker_id in the exact state the original
    coordinator did.  This is the contract operators rely on to disable the
    cross-runtime DO simply by clearing the env vars.
    """

    def _patch_login_budget_helpers(self, monkeypatch):
        """Stub config constants so the budget arithmetic is deterministic."""
        monkeypatch.setattr(
            lc_mod, "LOGIN_ATTEMPTS_PER_PROXY_LIMIT", 5, raising=False,
        )
        monkeypatch.setattr(
            lc_mod, "LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH", 3, raising=False,
        )

    def test_branch4_login_succeeds_without_touching_do(self, monkeypatch):
        """Branch 4 (no logged-in worker yet, try own proxy) — must call
        ``_login_and_verify`` directly, dispatch task to login_queue, and
        set ``logged_in_worker_id`` without ever calling any DO helper."""
        self._patch_login_budget_helpers(monkeypatch)
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])

        # Sentinel: any DO method invocation explodes the test.  The
        # production wiring should short-circuit on ``client is None``
        # *before* reaching any of these.
        do_sentinel = MagicMock()
        do_sentinel.acquire_lease.side_effect = AssertionError("DO must not be called")
        do_sentinel.release_lease.side_effect = AssertionError("DO must not be called")
        do_sentinel.invalidate.side_effect = AssertionError("DO must not be called")
        do_sentinel.publish.side_effect = AssertionError("DO must not be called")

        # The supported "disabled" path: client is None.
        state_mod.global_login_state_client = None

        login_queue: queue.Queue = queue.Queue()
        task_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie-OK")) as mock_lv:
            coord.handle_login_required(
                worker=worker,
                task=task,
                video_code="ABC-001",
                login_queue=login_queue,
                task_queue=task_queue,
            )

        # No park: pending queue empty, no poller daemon launched.
        assert len(coord._pending_login_tasks) == 0
        assert coord._poll_thread is None
        # The legacy login path was driven exactly once.
        mock_lv.assert_called_once_with(worker)
        # Task is on its way to be re-fetched with the new cookie.
        assert login_queue.get_nowait() is task
        assert task.login_verified_after_refresh is True
        # This worker is now the logged-in worker.
        assert coord.logged_in_worker_id == worker.worker_id
        # Side-channel: task_queue is left untouched.
        assert task_queue.empty()

    def test_branch3_self_stale_relogin_without_invalidate_call(self, monkeypatch):
        """Branch 3 (self is logged-in, session went stale) — must reach
        ``_login_and_verify`` for re-login without invoking
        ``client.invalidate`` (since the client is None)."""
        self._patch_login_budget_helpers(monkeypatch)
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        coord.logged_in_worker_id = worker.worker_id  # self is the logged-in one

        state_mod.global_login_state_client = None
        # Even if a stale version sits in state, the invalidate helper
        # must early-return on ``client is None`` and not consult it.
        state_mod.current_login_state_version = 7

        login_queue: queue.Queue = queue.Queue()
        task_queue: queue.Queue = queue.Queue()
        task = _make_task()  # login_verified_after_refresh defaults to False

        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie-NEW")) as mock_lv:
            coord.handle_login_required(
                worker=worker, task=task, video_code="ABC-001",
                login_queue=login_queue, task_queue=task_queue,
            )

        mock_lv.assert_called_once_with(worker)
        assert coord.logged_in_worker_id == worker.worker_id
        assert login_queue.get_nowait() is task
        # version untouched: invalidate helper bailed before calling the (None) client.
        assert state_mod.current_login_state_version == 7

    def test_publish_helper_short_circuits_when_client_none(self, monkeypatch):
        """Companion check: ``_publish_login_state_to_do`` (called from
        ``attempt_login_refresh``) must early-return without exception."""
        from packages.python.javdb_spider.fetch import session as session_mod
        state_mod.global_login_state_client = None
        state_mod.current_login_state_version = None
        # No exception, no version bump.
        session_mod._publish_login_state_to_do("P1", "cookie-X")
        assert state_mod.current_login_state_version is None

    def test_inherit_login_state_skips_do_lookup_when_client_none(self):
        """``ParallelFetchBackend._inherit_login_state`` must not crash when
        the DO client is unset — it falls through to the existing
        index-phase cookie inheritance path.  We verify the early-return by
        asserting that ``state.refreshed_session_cookie`` stays at its
        sentinel."""
        from packages.python.javdb_spider.fetch import fetch_engine as fe_mod

        state_mod.global_login_state_client = None
        state_mod.refreshed_session_cookie = None
        state_mod.logged_in_proxy_name = None
        state_mod.current_login_state_version = None

        # Fabricate the minimum ParallelFetchBackend surface
        # ``_inherit_login_state`` needs: ``self._workers`` (empty list
        # short-circuits the matching loop) and ``self._coordinator``
        # (unused on the no-state path).
        fake_backend = SimpleNamespace(_workers=[], _coordinator=MagicMock())
        # Bind the actual method to our SimpleNamespace stand-in.
        fe_mod.ParallelFetchBackend._inherit_login_state(fake_backend)

        assert state_mod.refreshed_session_cookie is None
        assert state_mod.logged_in_proxy_name is None
        assert state_mod.current_login_state_version is None

    def test_inherit_login_state_swallows_unexpected_do_error(self):
        from packages.python.javdb_spider.fetch import fetch_engine as fe_mod

        client = MagicMock()
        client.get_state.side_effect = RuntimeError("boom")
        state_mod.global_login_state_client = client
        state_mod.refreshed_session_cookie = "index-cookie"
        state_mod.logged_in_proxy_name = "P1"
        state_mod.current_login_state_version = None

        fake_backend = SimpleNamespace(_workers=[], _coordinator=MagicMock())
        fe_mod.ParallelFetchBackend._inherit_login_state(fake_backend)

        assert state_mod.refreshed_session_cookie == "index-cookie"
        assert state_mod.logged_in_proxy_name == "P1"


# ── P2-C: cross-runner login cooldown ───────────────────────────────────


class TestP2CLoginCooldown:
    """End-to-end behaviour of the P2-C ``cooldown_until_ms`` field.

    Verifies the three contracts called out in the plan:

    1. When ``acquire_lease`` returns a future ``cooldown_until_ms``
       the lease is released and the task is parked — no local login
       attempt fires.
    2. ``_record_login_attempt`` posts to ``record_attempt`` after every
       login attempt regardless of outcome.
    3. The poller drains parked tasks back to their ``login_queue``
       once the cooldown clock has expired.
    """

    def test_cooldown_response_parks_task_and_releases_lease(self):
        """Cooldown active → caller parks; lease that the DO granted is
        released so peer runners aren't blocked needlessly."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True,
            holder_id="runner-test",
            target_proxy_name="P1",
            lease_expires_at=99_999,
            server_time_ms=1_000,
            cooldown_until_ms=999_999_999,
            recent_attempt_count=6,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=1_000,
        )
        # The poller would call get_state on tick; return "no version progress".
        client.get_state.return_value = LoginStateGetResult(
            proxy_name=None, cookie=None, version=0,
            last_verified_at=0, has_active_lease=False, server_time_ms=1_000,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(coord, "_login_and_verify") as mock_lv:
            verified, cookie, parked = coord._login_and_verify_with_lease(
                worker, task, login_queue,
            )

        assert parked is True
        assert verified is False
        assert cookie is None
        # Release MUST have fired so the lease isn't blocking peer runners.
        client.release_lease.assert_called_once_with("runner-test")
        # ``_login_and_verify`` was NEVER reached — the cooldown short-circuited.
        mock_lv.assert_not_called()
        # Cooldown clock was recorded.
        assert coord._cooldown_until_ms == 999_999_999
        # Task is in the pending queue, not the login queue.
        assert len(coord._pending_login_tasks) == 1
        assert login_queue.empty()

    def test_no_cooldown_proceeds_normally(self):
        """``cooldown_until_ms == 0`` → caller follows the regular path."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=99_999, server_time_ms=1_000,
            cooldown_until_ms=0, recent_attempt_count=2,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=1_000,
        )
        client.record_attempt.return_value = RecordAttemptResult(
            recent_attempt_count=3, recent_failure_count=0,
            cooldown_until_ms=0, server_time_ms=1_000,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie-NEW")):
            verified, cookie, parked = coord._login_and_verify_with_lease(
                worker, task, login_queue,
            )

        assert parked is False
        assert verified is True
        assert cookie == "cookie-NEW"
        assert coord._cooldown_until_ms == 0

    def test_record_attempt_called_after_every_login(self):
        """Both success and failure outcomes are recorded."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=99_999, server_time_ms=1_000,
            cooldown_until_ms=0, recent_attempt_count=0,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=1_000,
        )
        client.record_attempt.return_value = RecordAttemptResult(
            recent_attempt_count=1, recent_failure_count=1,
            cooldown_until_ms=0, server_time_ms=1_000,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()

        with patch.object(coord, "_login_and_verify", return_value=(False, None)):
            coord._login_and_verify_with_lease(worker, _make_task(), login_queue)
        client.record_attempt.assert_called_once_with(
            "runner-test", "P1", "failure",
        )

        client.record_attempt.reset_mock()
        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie")):
            coord._login_and_verify_with_lease(worker, _make_task(), login_queue)
        client.record_attempt.assert_called_once_with(
            "runner-test", "P1", "success",
        )

    def test_record_attempt_failures_are_tolerated(self):
        """``record_attempt`` raising must not break the login path."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=99_999, server_time_ms=1_000,
            cooldown_until_ms=0, recent_attempt_count=0,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=1_000,
        )
        client.record_attempt.side_effect = LoginStateUnavailable("boom")
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()

        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie")):
            verified, _, parked = coord._login_and_verify_with_lease(
                worker, _make_task(), login_queue,
            )
        assert verified is True
        assert parked is False

    def test_record_attempt_updates_local_cooldown_when_returned(self):
        """When ``record_attempt`` itself reports a cooldown (i.e. this
        very failure crossed the threshold), the coordinator must
        record it locally so the *next* attempt parks straight away
        without an extra acquire round-trip."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=99_999, server_time_ms=1_000,
            cooldown_until_ms=0, recent_attempt_count=0,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=1_000,
        )
        client.record_attempt.return_value = RecordAttemptResult(
            recent_attempt_count=5, recent_failure_count=5,
            cooldown_until_ms=999_999_999, server_time_ms=1_000,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()

        with patch.object(coord, "_login_and_verify", return_value=(False, None)):
            coord._login_and_verify_with_lease(worker, _make_task(), login_queue)
        assert coord._cooldown_until_ms == 999_999_999

    def test_record_attempt_skipped_when_client_none(self):
        """Fail-open: no DO client means no record_attempt call."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        state_mod.global_login_state_client = None
        login_queue: queue.Queue = queue.Queue()

        with patch.object(coord, "_login_and_verify", return_value=(True, "cookie")):
            coord._login_and_verify_with_lease(worker, _make_task(), login_queue)
        # No exception, no AttributeError on a None client.

    def _wait_until(self, predicate, timeout: float = 2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_poller_dispatches_parked_tasks_when_cooldown_lifts(self):
        """Poller drains parked tasks once the cooldown clock has passed.

        Synthesise the cooldown state directly so the test doesn't need
        to wait several minutes; the real cooldown is anchored on
        ``Date.now()`` server-side and we only care here about the
        poller's transition logic.
        """
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        cooldown_until_ms = int(time.time() * 1000) + 60_000
        # Park one task with cooldown active.
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=cooldown_until_ms + 99_000,
            server_time_ms=cooldown_until_ms - 1_000,
            cooldown_until_ms=cooldown_until_ms,
            recent_attempt_count=6,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=cooldown_until_ms - 1_000,
        )
        # The poller will call get_state, but because the cooldown has
        # expired (we'll rewind it manually below), it should drain the
        # parked tasks BEFORE doing any get_state work.
        client.get_state.side_effect = AssertionError(
            "get_state must not run before cooldown drain",
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        login_queue.put("already-waiting")
        task = _make_task()

        with patch.object(lc_mod, "_POLL_INTERVAL_SEC", 0.05):
            with patch.object(coord, "_login_and_verify"):
                _, _, parked = coord._login_and_verify_with_lease(
                    worker, task, login_queue,
                )
            assert parked is True
            assert coord._cooldown_until_ms > 0
            # Rewind the cooldown clock so the poller's next tick will
            # observe "cooldown lifted" and drain the parked tasks.
            with coord._lock:
                coord._cooldown_until_ms = 1
            ok = self._wait_until(lambda: login_queue.qsize() >= 2, timeout=2.0)
            assert ok, "poller did not re-dispatch parked tasks after cooldown"

        dispatched = login_queue.get_nowait()
        assert dispatched is task
        assert login_queue.get_nowait() == "already-waiting"
        with coord._lock:
            assert coord._cooldown_until_ms == 0
            assert len(coord._pending_login_tasks) == 0

    def test_poller_checks_state_during_active_cooldown(self):
        """A peer-published cookie should release parked tasks immediately."""
        worker = _make_worker(0, "P1")
        coord = LoginCoordinator(all_workers=[worker])
        client = MagicMock()
        cooldown_until_ms = int(time.time() * 1000) + 60_000
        client.acquire_lease.return_value = AcquireLeaseResult(
            acquired=True, holder_id="runner-test", target_proxy_name="P1",
            lease_expires_at=cooldown_until_ms + 99_000,
            server_time_ms=cooldown_until_ms - 1_000,
            cooldown_until_ms=cooldown_until_ms,
            recent_attempt_count=6,
        )
        client.release_lease.return_value = ReleaseLeaseResult(
            released=True, server_time_ms=cooldown_until_ms - 1_000,
        )
        client.get_state.return_value = LoginStateGetResult(
            proxy_name="P1", cookie="cookie-NEW", version=5,
            last_verified_at=99_999, has_active_lease=False, server_time_ms=0,
        )
        state_mod.global_login_state_client = client
        login_queue: queue.Queue = queue.Queue()
        task = _make_task()

        with patch.object(lc_mod, "_POLL_INTERVAL_SEC", 0.05):
            with patch.object(coord, "_login_and_verify"):
                _, _, parked = coord._login_and_verify_with_lease(
                    worker, task, login_queue,
                )
            assert parked is True
            ok = self._wait_until(lambda: not login_queue.empty(), timeout=2.0)
            assert ok, "poller did not dispatch after peer publish"

        assert login_queue.get_nowait() is task
        assert worker._handler.config.javdb_session_cookie == "cookie-NEW"
        assert state_mod.refreshed_session_cookie == "cookie-NEW"
        with coord._lock:
            assert coord._cooldown_until_ms == 0
