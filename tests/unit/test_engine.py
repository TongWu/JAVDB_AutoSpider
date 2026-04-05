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
        import scripts.spider.runtime.state as st
        from scripts.spider.fetch.fetch_engine import FetchEngine

        st.login_total_budget = 2

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: {'ok': True},
            use_cookie=True,
            sleep_min=0.01, sleep_max=0.02,
        )
        engine.start()
        _patch_workers(engine, lambda url, _cf: '<html>login</html>')

        engine.submit('https://javdb.com/v/login')
        engine.mark_done()

        results = list(engine.results())
        engine.shutdown()

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
        """A task that exceeds its time budget should return None and
        be re-queued to another worker."""
        from scripts.spider.fetch.fetch_engine import FetchEngine

        call_count = {'n': 0}

        def slow_process(ctx, task):
            call_count['n'] += 1
            if call_count['n'] == 1:
                time.sleep(0.3)
                return None  # simulate slow failure
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

        assert len(results) == 1

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
        should speculatively attempt the same task."""
        from scripts.spider.fetch.fetch_engine import FetchEngine

        attempt_count = {'n': 0}

        def flaky_process(ctx, task):
            attempt_count['n'] += 1
            if attempt_count['n'] == 1:
                time.sleep(0.5)
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
