"""Tests for scripts.spider.fetch.fetch_engine — FetchEngine, EngineWorker, WorkerContext."""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PROXY_POOL = [
    {'name': 'proxy-a', 'http': 'http://a:1', 'https': 'http://a:1'},
    {'name': 'proxy-b', 'http': 'http://b:1', 'https': 'http://b:1'},
]

def _make_ban_manager_stub():
    mgr = MagicMock()
    mgr.is_proxy_banned = MagicMock(return_value=False)
    return mgr


# Common decorator stack used by most tests that start the engine.
_engine_patches = lambda fn: (
    patch('scripts.spider.fetch.fetch_engine.RequestHandler', side_effect=_make_handler_stub)(
    patch('scripts.spider.fetch.fetch_engine.create_proxy_pool_from_config', return_value=MagicMock())(
    patch('scripts.spider.fetch.fetch_engine.get_ban_manager', return_value=_make_ban_manager_stub())(
    patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', _PROXY_POOL)(
    patch('scripts.spider.fetch.fetch_engine.LOGIN_PROXY_NAME', None)(
    fn)))))
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset mutable state between tests."""
    import scripts.spider.runtime.state as st
    st.login_attempted = False
    st.refreshed_session_cookie = None
    st.logged_in_proxy_name = None
    st.login_attempts_per_proxy.clear()
    st.login_failures_per_proxy.clear()
    st.login_total_attempts = 0
    st.always_bypass_time = None
    st.parsed_links.clear()
    if hasattr(st, '_login_budget_deducted_proxies'):
        st._login_budget_deducted_proxies.clear()
    # Keep unit tests deterministic: disable cross-runner login DO plumbing
    # so login-required paths do not park indefinitely waiting on peers.
    st.global_login_state_client = None
    st.current_login_state_version = None
    yield


def _make_handler_stub(*_args, **_kwargs):
    handler = MagicMock()
    handler.get_page = MagicMock(return_value=None)
    handler.config = MagicMock()
    handler.config.javdb_session_cookie = None
    return handler


def _patch_workers(engine, fetch_fn):
    """Replace _fetch_html and zero startup jitter on all workers."""
    for w in engine._workers:
        w._fetch_html = fetch_fn
        w._startup_jitter = 0.01


class TestStableProxyId:
    def test_url_fallback_matches_coordinator_host_port_hash(self):
        from packages.python.javdb_platform.proxy_coordinator_client import _normalize_proxy_id
        from packages.python.javdb_spider.fetch.fetch_engine import _stable_proxy_id

        expected = _normalize_proxy_id(None, fallback_seed="proxy.example.com:8080")

        assert _stable_proxy_id(
            {"http": "http://user:pass@Proxy.Example.com:8080/"},
            worker_id=1,
        ) == expected
        assert _stable_proxy_id(
            {"https": "https://other-creds@proxy.example.com:8080/path"},
            worker_id=2,
        ) == expected


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEngineSimpleMode:

    @_engine_patches
    def test_submit_and_get_results(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask

        html_pages = {'https://javdb.com/v/a': '<html>A</html>'}

        def fake_parse(html, task):
            return {'title': 'A'} if html == '<html>A</html>' else None

        engine = FetchEngine.simple(
            parse_fn=fake_parse, use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf, _h=html_pages: _h.get(url))

        engine.submit('https://javdb.com/v/a', meta={'code': 'A'})
        engine.mark_done()

        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].data == {'title': 'A'}
        assert results[0].task.meta == {'code': 'A'}

    @_engine_patches
    def test_parse_failure_retries_on_other_proxy(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: None,
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html></html>')

        engine.submit('https://javdb.com/v/x', entry_index='1/1')
        engine.mark_done()

        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == 'all_proxies_failed'

    @_engine_patches
    def test_multiple_tasks(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: {'code': task.meta.get('code')},
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: f'<html>{url}</html>')

        for i in range(5):
            engine.submit(f'https://javdb.com/v/{i}', meta={'code': f'C-{i}'})
        engine.mark_done()

        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 5
        assert all(r.success for r in results)
        assert {r.data['code'] for r in results} == {f'C-{i}' for i in range(5)}


class TestEngineAdvancedMode:

    @_engine_patches
    def test_ctx_fetch_returns_html(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine, WorkerContext, EngineTask

        def process(ctx, task):
            html = ctx.fetch(task.url)
            return {'len': len(html)} if html else None

        engine = FetchEngine(
            process_fn=process, use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html>ok</html>')

        engine.submit('https://javdb.com/v/1')
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].data == {'len': len('<html>ok</html>')}


def _mock_attempt_login(explicit_proxies=None, explicit_proxy_name=None,
                        *, spider_uses_proxy=True):
    """Mock login that always fails but correctly increments state counters."""
    import scripts.spider.runtime.state as st
    st.login_total_attempts += 1
    if explicit_proxy_name:
        st.login_attempts_per_proxy[explicit_proxy_name] = (
            st.login_attempts_per_proxy.get(explicit_proxy_name, 0) + 1
        )
    return False, None, None


class TestEngineLoginDetection:

    @patch('scripts.spider.fetch.login_coordinator.attempt_login_refresh', side_effect=_mock_attempt_login)
    @patch('scripts.spider.fetch.fetch_engine.is_login_page', return_value=True)
    @_engine_patches
    def test_login_page_triggers_coordinator(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine
        from scripts.spider.fetch.login_coordinator import requeue_front

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: {'ok': True},
            use_cookie=True,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html>login</html>')

        def _fast_handle_login_required(_coord, worker, task, *_args, task_queue, **_kwargs):
            # Keep this test focused on "login page routes into coordinator".
            # Full coordinator retry state-machine has dedicated tests.
            task.failed_proxies.add(worker.proxy_name)
            requeue_front(task_queue, task)

        with patch(
            'scripts.spider.fetch.fetch_engine.LoginCoordinator.handle_login_required',
            autospec=True,
            side_effect=_fast_handle_login_required,
        ) as mock_handle_login_required:
            engine.submit('https://javdb.com/v/login')
            engine.mark_done()
            results = list(engine.results())

        engine.shutdown()

        assert mock_handle_login_required.call_count >= 1
        assert len(results) == 1
        assert results[0].success is False


class TestEngineCFBypassFallback:

    @patch('scripts.spider.fetch.fetch_engine.is_login_page', return_value=False)
    @_engine_patches
    def test_cf_fallback_on_direct_failure(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine

        call_log = []

        def fake_parse(html, task):
            call_log.append(html)
            return {'ok': True}

        engine = FetchEngine.simple(
            parse_fn=fake_parse, use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()

        def fetch_direct_fail(url, use_cf):
            return '<html>cf</html>' if use_cf else None

        _patch_workers(engine, fetch_direct_fail)

        engine.submit('https://javdb.com/v/cf')
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is True
        assert '<html>cf</html>' in call_log


class TestEngineShutdown:

    @_engine_patches
    def test_shutdown_returns_orphans(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine

        engine = FetchEngine(
            process_fn=lambda ctx, task: time.sleep(10) or {'ok': True},
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()

        for i in range(10):
            engine.submit(f'https://javdb.com/v/{i}')

        time.sleep(0.5)
        orphaned = engine.shutdown(timeout=2)
        assert len(orphaned) > 0

    @_engine_patches
    def test_pending_counter(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine

        engine = FetchEngine(
            process_fn=lambda ctx, task: {'ok': True},
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html></html>')

        engine.submit('https://javdb.com/v/1')
        engine.submit('https://javdb.com/v/2')
        engine.mark_done()

        results = list(engine.results())
        engine.shutdown()

        assert engine.pending == 0
        assert len(results) == 2


class TestEngineSubmitTask:

    @_engine_patches
    def test_submit_task(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask

        engine = FetchEngine(
            process_fn=lambda ctx, task: task.meta,
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html></html>')

        task = EngineTask(url='https://javdb.com/v/1', entry_index='1/1', meta={'custom': 'data'})
        engine.submit_task(task)
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].data == {'custom': 'data'}
        assert results[0].task.entry_index == '1/1'


class TestEngineNoProxyPool:

    @patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', [])
    def test_start_raises_without_proxies(self):
        from scripts.spider.fetch.fetch_engine import FetchEngine
        engine = FetchEngine(process_fn=lambda ctx, t: None, use_cookie=False)
        with pytest.raises(RuntimeError, match="PROXY_POOL"):
            engine.start()

    @patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', [])
    def test_parallel_backend_start_raises_without_proxies(self):
        from scripts.spider.fetch.fetch_engine import ParallelFetchBackend

        backend = ParallelFetchBackend(
            process_fn=lambda ctx, t: None,
            use_cookie=False,
        )
        with pytest.raises(RuntimeError, match="PROXY_POOL"):
            backend.start()


class TestEngineMarkDoneGuard:

    @patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', _PROXY_POOL)
    def test_submit_after_done_raises(self):
        from scripts.spider.fetch.fetch_engine import FetchEngine
        engine = FetchEngine(process_fn=lambda ctx, t: None, use_cookie=False)
        engine.mark_done()
        with pytest.raises(RuntimeError, match="mark_done"):
            engine.submit('https://javdb.com/v/1')


class TestEngineProxyBanned:

    @_engine_patches
    def test_proxy_banned_stops_worker(self, *_mocks):
        """ProxyBannedError should stop the worker and produce a failure result."""
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask
        from packages.python.javdb_platform.request_handler import ProxyBannedError

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: {'ok': True},
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()

        def _raise_ban(url, _cf):
            raise ProxyBannedError('test-proxy', 'ban page detected')

        _patch_workers(engine, _raise_ban)

        engine.submit('https://javdb.com/v/ban', entry_index='1/1')
        engine.mark_done()

        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == 'all_proxies_banned'


class TestParallelBackendCompatibility:

    @_engine_patches
    def test_parallel_backend_matches_fetch_engine_results(self, *_mocks):
        from scripts.spider.fetch.fetch_engine import (
            EngineTask,
            FetchEngine,
            ParallelFetchBackend,
        )

        def fake_parse(html, task):
            return {'code': task.meta['code'], 'html': html}

        backend = ParallelFetchBackend.simple(
            parse_fn=fake_parse,
            use_cookie=False,
            sleep_min=0.01,
            sleep_max=0.02,
        )
        backend.start()
        _patch_workers(backend, lambda url, _cf: f'<html>{url}</html>')
        backend.submit_task(
            EngineTask(
                url='https://javdb.com/v/backend',
                entry_index='1/1',
                meta={'code': 'BACKEND'},
            )
        )
        backend.mark_done()
        backend_results = list(backend.results())
        backend.shutdown()

        engine = FetchEngine.simple(
            parse_fn=fake_parse,
            use_cookie=False,
            sleep_min=0.01,
            sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: f'<html>{url}</html>')
        engine.submit_task(
            EngineTask(
                url='https://javdb.com/v/backend',
                entry_index='1/1',
                meta={'code': 'BACKEND'},
            )
        )
        engine.mark_done()
        engine_results = list(engine.results())
        engine.shutdown()

        assert [(r.success, r.data, r.task.meta) for r in backend_results] == [
            (r.success, r.data, r.task.meta) for r in engine_results
        ]


# ---------------------------------------------------------------------------
# HOL blocking improvements – queue pressure
# ---------------------------------------------------------------------------


class TestQueuePressure:
    """Plan B: _simple_process skips CF fallback under low queue pressure."""

    @patch('scripts.spider.fetch.fetch_engine.is_login_page', return_value=False)
    @patch('scripts.spider.fetch.fetch_engine.RequestHandler', side_effect=_make_handler_stub)
    @patch('scripts.spider.fetch.fetch_engine.create_proxy_pool_from_config', return_value=MagicMock())
    @patch('scripts.spider.fetch.fetch_engine.get_ban_manager', return_value=_make_ban_manager_stub())
    @patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', [
        {'name': 'proxy-a', 'http': 'http://a:1', 'https': 'http://a:1'},
        {'name': 'proxy-b', 'http': 'http://b:1', 'https': 'http://b:1'},
        {'name': 'proxy-c', 'http': 'http://c:1', 'https': 'http://c:1'},
    ])
    @patch('scripts.spider.fetch.fetch_engine.LOGIN_PROXY_NAME', None)
    def test_low_pressure_skips_cf_fallback(self, *_mocks):
        """With 3 workers (active > 2) and a nearly-empty queue, pressure
        is 'low'.  The first proxies should skip CF fallback and re-queue;
        the task should still complete via CF on the tail attempt once
        enough proxies have failed via direct path."""
        from scripts.spider.fetch.fetch_engine import FetchEngine

        cf_calls = []

        def fetch_fn(url, use_cf):
            if use_cf:
                cf_calls.append(url)
                return '<html>cf</html>'
            return None  # direct always fails

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: {'ok': True},
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, fetch_fn)

        engine.submit('https://javdb.com/v/pressure', entry_index='p-1')
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is True
        # CF should eventually be tried (tail-task fallback), but fewer
        # times than the total number of workers (early workers skipped it).
        assert len(cf_calls) >= 1


class TestQueuePressureProperty:
    """Unit-level test for _queue_pressure without starting full engine."""

    @staticmethod
    def _make_worker_stub(qsize, active_workers):
        """Create a minimal namespace for _queue_pressure testing."""
        class _Stub:
            task_queue = MagicMock()
            _active_workers = active_workers
        _Stub.task_queue.qsize.return_value = qsize
        return _Stub()

    def test_low_when_queue_empty_and_many_workers(self):
        from packages.python.javdb_spider.fetch.fetch_engine import _EngineWorker
        w = self._make_worker_stub(qsize=0, active_workers=5)
        assert _EngineWorker._queue_pressure.fget(w) == 'low'

    def test_normal_when_queue_has_items(self):
        from packages.python.javdb_spider.fetch.fetch_engine import _EngineWorker
        w = self._make_worker_stub(qsize=5, active_workers=5)
        assert _EngineWorker._queue_pressure.fget(w) == 'normal'

    def test_normal_when_few_workers(self):
        from packages.python.javdb_spider.fetch.fetch_engine import _EngineWorker
        w = self._make_worker_stub(qsize=0, active_workers=2)
        assert _EngineWorker._queue_pressure.fget(w) == 'normal'


# ---------------------------------------------------------------------------
# HOL blocking improvements – task time budget
# ---------------------------------------------------------------------------


class TestTaskTimeBudget:
    """Plan A: task-level deadline prevents unbounded processing."""

    @_engine_patches
    def test_task_timeout_triggers_requeue(self, *_mocks):
        """A task that exceeds its time budget should be detected as
        expired and re-queued; a subsequent attempt should succeed."""
        from scripts.spider.fetch.fetch_engine import FetchEngine

        call_count = {'n': 0}
        observed_expired = {'v': False}

        def slow_process(ctx, task):
            call_count['n'] += 1
            if call_count['n'] == 1:
                time.sleep(0.3)  # exceed the 0.2s timeout
                observed_expired['v'] = ctx.is_expired
                return None  # expired — triggers re-queue
            return {'ok': True}

        engine = FetchEngine(
            process_fn=slow_process,
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
            task_timeout=0.2,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html></html>')

        engine.submit('https://javdb.com/v/slow', entry_index='slow-1')
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert observed_expired['v'], "first call should have observed an expired deadline"
        assert call_count['n'] >= 2, "task should have been retried after expiry"
        assert len(results) == 1
        assert results[0].success is True

    @_engine_patches
    def test_zero_timeout_means_no_limit(self, *_mocks):
        """task_timeout=0 should not set any deadline."""
        from scripts.spider.fetch.fetch_engine import FetchEngine

        engine = FetchEngine(
            process_fn=lambda ctx, task: {'ok': True},
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
            task_timeout=0,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html></html>')

        engine.submit('https://javdb.com/v/x')
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is True

    def test_engine_task_deadline_field(self):
        """EngineTask should have a _deadline field defaulting to None."""
        from packages.python.javdb_spider.fetch.fetch_engine import EngineTask
        t = EngineTask(url='https://example.com')
        assert t._deadline is None
        assert t._speculative is False


# ---------------------------------------------------------------------------
# HOL blocking improvements – CF fallback deadline in RequestHandler
# ---------------------------------------------------------------------------


class TestRequestHandlerDeadline:
    """Plan D: deadline-aware pausing in RequestHandler."""

    def test_pause_skipped_when_deadline_exceeded(self):
        from packages.python.javdb_platform.request_handler import (
            RequestHandler, RequestConfig,
        )
        config = RequestConfig(task_deadline=time.monotonic() - 1.0)
        handler = RequestHandler(config=config)
        start = time.monotonic()
        handler._pause_between_attempts(legacy_seconds=5.0)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0

    def test_pause_truncated_to_remaining_budget(self):
        from packages.python.javdb_platform.request_handler import (
            RequestHandler, RequestConfig,
        )
        config = RequestConfig(task_deadline=time.monotonic() + 0.1)
        handler = RequestHandler(config=config)
        start = time.monotonic()
        handler._pause_between_attempts(legacy_seconds=5.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.5

    def test_is_deadline_exceeded_false_when_no_deadline(self):
        from packages.python.javdb_platform.request_handler import (
            RequestHandler, RequestConfig,
        )
        handler = RequestHandler(config=RequestConfig())
        assert handler._is_deadline_exceeded() is False

    def test_is_deadline_exceeded_true_when_past(self):
        from packages.python.javdb_platform.request_handler import (
            RequestHandler, RequestConfig,
        )
        config = RequestConfig(task_deadline=time.monotonic() - 1.0)
        handler = RequestHandler(config=config)
        assert handler._is_deadline_exceeded() is True


# ---------------------------------------------------------------------------
# HOL blocking improvements – speculative execution
# ---------------------------------------------------------------------------


class TestSpeculativeExecution:
    """Plan C: idle workers speculatively race on in-flight tasks."""

    @_engine_patches
    def test_speculative_worker_produces_result(self, *_mocks):
        """When one worker is slow and another is idle, the idle worker
        should speculatively attempt the same task.

        The slow worker must hold the task longer than the
        ``task_queue.get(timeout=2.0)`` poll so the idle worker's
        ``_try_speculative_task()`` fires before the task is completed
        or re-queued.
        """
        from scripts.spider.fetch.fetch_engine import FetchEngine

        attempt_count = {'n': 0}

        def flaky_process(ctx, task):
            attempt_count['n'] += 1
            if attempt_count['n'] == 1:
                time.sleep(2.5)  # longer than queue poll timeout (2.0s)
                return None
            return {'ok': True}

        engine = FetchEngine(
            process_fn=flaky_process,
            use_cookie=False,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html></html>')

        engine.submit('https://javdb.com/v/spec', entry_index='spec-1')
        engine.mark_done()
        results = list(engine.results())
        engine.shutdown()

        assert len(results) == 1
        assert results[0].success is True

    def test_speculative_flag_on_task(self):
        from packages.python.javdb_spider.fetch.fetch_engine import EngineTask
        t = EngineTask(url='https://example.com', _speculative=True)
        assert t._speculative is True

    def test_mark_entry_completed_atomicity(self):
        """Only the first caller to mark_entry_completed should get True."""
        from packages.python.javdb_spider.fetch.fetch_engine import _EngineWorker

        worker = MagicMock(spec=_EngineWorker)
        completed = set()
        lock = threading.Lock()
        worker._completed_entries = completed
        worker._completed_lock = lock

        assert _EngineWorker._mark_entry_completed(worker, 'entry-1') is True
        assert _EngineWorker._mark_entry_completed(worker, 'entry-1') is False
        assert 'entry-1' in completed

    def test_mark_entry_completed_empty_index(self):
        """Empty entry_index should always return True (no dedup)."""
        from packages.python.javdb_spider.fetch.fetch_engine import _EngineWorker

        worker = MagicMock(spec=_EngineWorker)
        worker._completed_entries = set()
        worker._completed_lock = threading.Lock()

        assert _EngineWorker._mark_entry_completed(worker, '') is True
        assert _EngineWorker._mark_entry_completed(worker, '') is True


# ---------------------------------------------------------------------------
# Login verification + login budget reduction
# ---------------------------------------------------------------------------


class TestVerifyLoginViaFixedPages:
    """``verify_login_via_fixed_pages`` correctly gates a fresh login."""

    def test_returns_true_when_no_urls_configured(self):
        from packages.python.javdb_spider.fetch.session import (
            verify_login_via_fixed_pages,
        )
        handler = MagicMock()
        assert verify_login_via_fixed_pages(handler, 'p1', urls=[]) is True
        handler.get_page.assert_not_called()

    def test_returns_true_when_all_urls_pass(self):
        from packages.python.javdb_spider.fetch import session as session_mod

        handler = MagicMock()
        handler.get_page.return_value = '<html>logged in dashboard</html>'

        with patch.object(session_mod, 'is_login_page', return_value=False):
            ok = session_mod.verify_login_via_fixed_pages(
                handler, 'p1', urls=['/users/want_watch_videos', '/'],
            )
        assert ok is True
        assert handler.get_page.call_count == 2

    def test_returns_false_when_any_page_is_login_wall(self):
        from packages.python.javdb_spider.fetch import session as session_mod

        handler = MagicMock()
        handler.get_page.return_value = '<html>login form</html>'

        with patch.object(session_mod, 'is_login_page', return_value=True):
            ok = session_mod.verify_login_via_fixed_pages(
                handler, 'p1', urls=['/users/want_watch_videos'],
            )
        assert ok is False

    def test_returns_false_when_fetch_returns_empty(self):
        from packages.python.javdb_spider.fetch.session import (
            verify_login_via_fixed_pages,
        )
        handler = MagicMock()
        handler.get_page.return_value = None
        ok = verify_login_via_fixed_pages(handler, 'p1', urls=['/foo'])
        assert ok is False

    def test_relative_paths_are_prefixed_with_base_url(self):
        from packages.python.javdb_spider.fetch import session as session_mod

        handler = MagicMock()
        handler.get_page.return_value = '<html>ok</html>'

        with patch.object(session_mod, 'BASE_URL', 'https://javdb.com'), \
                patch.object(session_mod, 'is_login_page', return_value=False):
            session_mod.verify_login_via_fixed_pages(
                handler, 'p1', urls=['/users/want_watch_videos'],
            )

        called_url = handler.get_page.call_args.args[0]
        assert called_url == 'https://javdb.com/users/want_watch_videos'


class TestLoginBudgetReduction:
    """``state.deduct_proxy_login_budget`` shrinks budget for banned proxies."""

    def _reset(self, *, budget, attempts=0, per_proxy=None):
        import packages.python.javdb_spider.runtime.state as st
        st.login_total_budget = budget
        st.login_total_attempts = attempts
        st.login_attempts_per_proxy.clear()
        if per_proxy:
            st.login_attempts_per_proxy.update(per_proxy)
        st._login_budget_deducted_proxies.clear()

    def test_deducts_full_per_proxy_limit_when_unused(self):
        import packages.python.javdb_spider.runtime.state as st
        from packages.python.javdb_spider.runtime.config import (
            LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
        )
        self._reset(budget=4 * LOGIN_ATTEMPTS_PER_PROXY_LIMIT)
        original = st.login_total_budget

        deducted = st.deduct_proxy_login_budget('proxy-x')
        assert deducted == LOGIN_ATTEMPTS_PER_PROXY_LIMIT
        assert st.login_total_budget == original - LOGIN_ATTEMPTS_PER_PROXY_LIMIT

    def test_deducts_only_remaining_when_some_attempts_used(self):
        import packages.python.javdb_spider.runtime.state as st
        from packages.python.javdb_spider.runtime.config import (
            LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
        )
        self._reset(
            budget=4 * LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
            attempts=2,
            per_proxy={'proxy-x': 2},
        )
        original = st.login_total_budget

        deducted = st.deduct_proxy_login_budget('proxy-x')
        assert deducted == LOGIN_ATTEMPTS_PER_PROXY_LIMIT - 2
        assert st.login_total_budget == original - (LOGIN_ATTEMPTS_PER_PROXY_LIMIT - 2)

    def test_idempotent_for_same_proxy(self):
        import packages.python.javdb_spider.runtime.state as st
        from packages.python.javdb_spider.runtime.config import (
            LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
        )
        self._reset(budget=4 * LOGIN_ATTEMPTS_PER_PROXY_LIMIT)

        first = st.deduct_proxy_login_budget('proxy-x')
        before = st.login_total_budget
        second = st.deduct_proxy_login_budget('proxy-x')
        assert first == LOGIN_ATTEMPTS_PER_PROXY_LIMIT
        assert second == 0
        assert st.login_total_budget == before

    def test_never_drops_below_attempts_already_spent(self):
        import packages.python.javdb_spider.runtime.state as st
        from packages.python.javdb_spider.runtime.config import (
            LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
        )
        # 1 proxy already spent everything, only 1 attempt total left.
        self._reset(
            budget=LOGIN_ATTEMPTS_PER_PROXY_LIMIT + 1,
            attempts=LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
            per_proxy={'proxy-y': LOGIN_ATTEMPTS_PER_PROXY_LIMIT},
        )
        # Banning a fresh proxy with 0 used attempts would normally subtract
        # the full limit; clamp must keep budget >= login_total_attempts.
        st.deduct_proxy_login_budget('proxy-x')
        assert st.login_total_budget >= st.login_total_attempts

    def test_banned_proxy_runtime_path_calls_deduct(self):
        """``_handle_proxy_banned`` invokes ``deduct_proxy_login_budget``."""
        import packages.python.javdb_spider.runtime.state as st
        from packages.python.javdb_platform.request_handler import ProxyBannedError
        from scripts.spider.fetch.fetch_engine import FetchEngine
        from packages.python.javdb_spider.runtime.config import (
            LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
        )

        # Reset state so we have a known starting budget.
        self._reset(budget=2 * LOGIN_ATTEMPTS_PER_PROXY_LIMIT)
        original_budget = st.login_total_budget

        with patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', _PROXY_POOL), \
                patch('scripts.spider.fetch.fetch_engine.LOGIN_PROXY_NAME', None), \
                patch('scripts.spider.fetch.fetch_engine.RequestHandler', side_effect=_make_handler_stub), \
                patch('scripts.spider.fetch.fetch_engine.create_proxy_pool_from_config', return_value=MagicMock()), \
                patch(
                    'scripts.spider.fetch.fetch_engine.get_ban_manager',
                    return_value=_make_ban_manager_stub(),
                ):
            engine = FetchEngine.simple(
                parse_fn=lambda html, task: {'ok': True},
                use_cookie=False,
                sleep_min=0.01, sleep_max=0.02,
            )
            engine.start()

            def _raise_ban(url, _cf):
                raise ProxyBannedError('proxy-a', 'banned')

            _patch_workers(engine, _raise_ban)
            engine.submit('https://javdb.com/v/x', entry_index='1/1')
            engine.mark_done()
            list(engine.results())
            engine.shutdown()

        # At least one proxy got banned, so budget must have been reduced.
        assert st.login_total_budget < original_budget


class TestEngineTaskLoginVerifiedFlag:
    """``EngineTask.login_verified_after_refresh`` propagates through the engine."""

    def test_default_is_false(self):
        from packages.python.javdb_spider.fetch.fetch_engine import EngineTask
        assert EngineTask(url='https://x').login_verified_after_refresh is False


class TestLoginCoordinatorVerifiedShortCircuit:
    """When a verified-login task hits the wall again, no extra login fires."""

    def test_verified_task_routed_back_without_relogin(self):
        import packages.python.javdb_spider.runtime.state as st
        from packages.python.javdb_spider.fetch.login_coordinator import (
            LoginCoordinator,
        )
        from packages.python.javdb_spider.fetch.fetch_engine import EngineTask
        import queue as queue_module

        st.login_total_budget = 10
        st.login_total_attempts = 1
        st.login_attempts_per_proxy.clear()
        st.login_failures_per_proxy.clear()
        st._login_budget_deducted_proxies.clear()

        worker = MagicMock()
        worker.worker_id = 0
        worker.proxy_name = 'proxy-a'
        worker.proxy_config = {'name': 'proxy-a'}
        worker._handler = MagicMock()
        worker._handler.config = MagicMock()

        coord = LoginCoordinator(all_workers=[worker], login_proxy_name=None)
        coord.logged_in_worker_id = 0  # this worker IS the logged-in worker

        task = EngineTask(
            url='https://x', entry_index='1/1',
            login_verified_after_refresh=True,
        )
        task_q: queue_module.Queue = queue_module.Queue()
        login_q: queue_module.Queue = queue_module.Queue()

        with patch(
            'packages.python.javdb_spider.fetch.login_coordinator.attempt_login_refresh',
        ) as mock_login:
            coord.handle_login_required(
                worker=worker, task=task, video_code='V-1',
                login_queue=login_q, task_queue=task_q,
            )
        # No login attempt should fire.
        mock_login.assert_not_called()
        # Task should be re-queued to the regular task queue with proxy marked failed.
        assert 'proxy-a' in task.failed_proxies
        assert task_q.qsize() == 1
        assert login_q.qsize() == 0
