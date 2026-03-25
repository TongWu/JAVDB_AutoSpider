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

# Common decorator stack used by most tests that start the engine.
_engine_patches = lambda fn: (
    patch('scripts.spider.fetch.fetch_engine.RequestHandler', side_effect=_make_handler_stub)(
    patch('scripts.spider.fetch.fetch_engine.create_proxy_pool_from_config', return_value=MagicMock())(
    patch('scripts.spider.fetch.fetch_engine.PROXY_POOL', _PROXY_POOL)(
    patch('scripts.spider.fetch.fetch_engine.LOGIN_PROXY_NAME', None)(
    fn))))
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
            ban_log_file='', sleep_min=0.01, sleep_max=0.02,
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
            use_cookie=False, ban_log_file='',
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
            use_cookie=False, ban_log_file='',
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
            ban_log_file='', sleep_min=0.01, sleep_max=0.02,
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


def _mock_attempt_login(explicit_proxies=None, explicit_proxy_name=None):
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
            use_cookie=True, ban_log_file='',
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
            ban_log_file='', sleep_min=0.01, sleep_max=0.02,
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
            use_cookie=False, ban_log_file='',
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
            use_cookie=False, ban_log_file='',
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
            use_cookie=False, ban_log_file='',
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
    def test_proxy_banned_error_requeues_task(self, *_mocks):
        """ProxyBannedError should requeue task and mark proxy as failed."""
        from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask
        from packages.python.javdb_platform.request_handler import ProxyBannedError

        engine = FetchEngine.simple(
            parse_fn=lambda html, task: {'ok': True},
            use_cookie=False, ban_log_file='',
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
        assert results[0].error == 'all_proxies_failed'


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
            ban_log_file='',
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
            ban_log_file='',
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
