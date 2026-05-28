from __future__ import annotations

from unittest.mock import MagicMock

from javdb.spider.app import run_service
from javdb.spider.runtime.context import SpiderRuntime
from javdb.spider.runtime.sleep import ensure_sleep_runtime


def test_each_runtime_gets_its_own_sleep_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    ensure_sleep_runtime(first)
    ensure_sleep_runtime(second)

    assert first.sleep.movie_sleep_mgr is not second.sleep.movie_sleep_mgr
    assert first.sleep.penalty_tracker is not second.sleep.penalty_tracker
    assert first.sleep.triple_window_throttle is not second.sleep.triple_window_throttle

    assert first.sleep.movie_sleep_mgr._penalty_tracker is first.sleep.penalty_tracker
    assert first.sleep.movie_sleep_mgr._throttle is first.sleep.triple_window_throttle
    assert first.sleep.dual_window_throttle is first.sleep.triple_window_throttle

    assert second.sleep.movie_sleep_mgr._penalty_tracker is second.sleep.penalty_tracker
    assert second.sleep.movie_sleep_mgr._throttle is second.sleep.triple_window_throttle
    assert second.sleep.dual_window_throttle is second.sleep.triple_window_throttle


def test_runtime_request_handler_uses_runtime_sleep(monkeypatch):
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    original_handler = state.global_request_handler

    class CapturingRequestHandler:
        created = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            CapturingRequestHandler.created = self

    monkeypatch.setattr(state, "RequestHandler", CapturingRequestHandler)

    try:
        runtime.initialize_request_handler()

        handler = CapturingRequestHandler.created
        assert handler.kwargs["penalty_tracker"] is runtime.sleep.penalty_tracker
        assert (
            handler.kwargs["config"].between_attempt_sleep
            == runtime.sleep.movie_sleep_mgr.sleep
        )
    finally:
        state.global_request_handler = original_handler


def test_run_service_passes_runtime_to_index_fetch(monkeypatch):
    runtime = SpiderRuntime()
    observed = {}

    def fake_fetch_all_index_pages(*args, **kwargs):
        observed["runtime"] = kwargs["runtime"]
        return {
            "all_index_results_phase1": [],
            "all_index_results_phase2": [],
            "any_proxy_banned": False,
            "use_proxy": False,
            "use_cf_bypass": False,
            "csv_path": "reports/out.csv",
            "last_valid_page": 0,
        }

    monkeypatch.setattr(run_service, "fetch_all_index_pages", fake_fetch_all_index_pages)

    # Invoke the smallest helper introduced during implementation rather than
    # full _main; the helper must contain the index-fetch call.
    run_service._fetch_index_for_runtime(
        runtime=runtime,
        session=object(),
        start_page=1,
        end_page=1,
        parse_all=False,
        phase_mode="all",
        custom_url=None,
        ignore_release_date=False,
        use_proxy=False,
        use_cf_bypass=False,
        max_consecutive_empty=3,
        output_csv="out.csv",
        output_dated_dir="reports",
        csv_path="reports/out.csv",
        user_specified_output=False,
        parsed_movies_history_phase1={},
        parsed_movies_history_phase2={},
        use_parallel=False,
    )

    assert observed["runtime"] is runtime


def test_state_setup_proxy_pool_uses_active_runtime_services(monkeypatch):
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    proxy_pool = object()

    monkeypatch.setattr(
        state,
        "create_proxy_pool_from_config",
        lambda *_args, **_kwargs: proxy_pool,
    )
    monkeypatch.setattr(state, "setup_proxy_coordinator", lambda: None)
    monkeypatch.setattr(state, "setup_login_state_client", lambda: None)
    monkeypatch.setattr(state, "setup_movie_claim_client", lambda: None)
    monkeypatch.setattr(state, "enforce_movie_claim_for_d1", lambda: None)
    monkeypatch.setattr(state, "setup_runner_registry_client", lambda: None)
    monkeypatch.setattr(state, "setup_work_distributor_client", lambda: None)
    monkeypatch.setattr(state, "PROXY_MODE", "pool")
    monkeypatch.setattr(
        state,
        "PROXY_POOL",
        [{"name": "proxy-a", "http": "http://a:1", "https": "http://a:1"}],
    )
    monkeypatch.setattr(state, "PROXY_HTTP", "")
    monkeypatch.setattr(state, "PROXY_HTTPS", "")

    state.bind_active_runtime(runtime)
    try:
        state.setup_proxy_pool(True)
        assert state.global_proxy_pool is proxy_pool
    finally:
        state.clear_active_runtime(runtime)

    assert runtime.services.proxy_pool is proxy_pool


def test_runtime_request_handler_callbacks_use_runtime_coordinator(monkeypatch):
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    coordinator = MagicMock()
    runtime.services.proxy_coordinator = coordinator
    original_handler = state.global_request_handler

    class CapturingRequestHandler:
        created = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            CapturingRequestHandler.created = self

    monkeypatch.setattr(state, "RequestHandler", CapturingRequestHandler)

    try:
        runtime.initialize_request_handler()
        handler = CapturingRequestHandler.created

        handler.kwargs["on_cf_event"]("proxy-a")
        handler.kwargs["on_request_complete"]("proxy-a", "success", 123)

        coordinator.report_async.assert_any_call("proxy-a", "cf")
        coordinator.report_async.assert_any_call("proxy-a", "success", latency_ms=123)
        assert runtime.services.request_handler is handler
        assert state.global_request_handler is handler
    finally:
        state.global_request_handler = original_handler


def test_runtime_registry_signals_update_runtime_sleep_not_legacy():
    from javdb.proxy.coordinator.runner_registry_client import Signal
    from javdb.spider.runtime import sleep as sleep_module

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    legacy_mgr = sleep_module.movie_sleep_mgr
    legacy_factor = legacy_mgr._global_factor
    legacy_pause = legacy_mgr._pause_until_ms
    legacy_long_max = legacy_mgr._throttle.long_max
    legacy_extra_max = legacy_mgr._throttle.extra_max

    runtime._apply_sleep_runner_count(3)
    runtime._apply_active_signals([
        Signal(
            id="sig-throttle",
            kind="throttle_global",
            expires_at_ms=99_999,
            created_at_ms=1,
            factor=2.5,
        ),
        Signal(
            id="sig-pause",
            kind="pause_all",
            expires_at_ms=88_888,
            created_at_ms=1,
        ),
    ])

    assert runtime.sleep.movie_sleep_mgr._global_factor == 2.5
    assert runtime.sleep.movie_sleep_mgr._pause_until_ms == 88_888
    assert runtime.sleep.triple_window_throttle.long_max == 10
    assert runtime.sleep.triple_window_throttle.extra_max == 66
    assert legacy_mgr._global_factor == legacy_factor
    assert legacy_mgr._pause_until_ms == legacy_pause
    assert legacy_mgr._throttle.long_max == legacy_long_max
    assert legacy_mgr._throttle.extra_max == legacy_extra_max


def test_runtime_config_snapshot_updates_runtime_throttle_not_legacy():
    from javdb.proxy.coordinator.runner_registry_client import ConfigSnapshot
    from javdb.spider.runtime import sleep as sleep_module

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    legacy_throttle = sleep_module.triple_window_throttle
    legacy_original = (
        legacy_throttle.short_max,
        legacy_throttle.long_max,
        legacy_throttle.extra_max,
        legacy_throttle.short_window,
        legacy_throttle.long_window,
        legacy_throttle.extra_window,
        legacy_throttle._base_short_max,
        legacy_throttle._base_long_max,
        legacy_throttle._base_extra_max,
    )

    try:
        runtime._apply_config_snapshot(
            ConfigSnapshot(
                version=1,
                updated_at_ms=0,
                values={
                    "short_max": "3",
                    "long_max": "12",
                    "extra_max": "90",
                    "long_window_sec": "180",
                },
            )
        )

        assert runtime.sleep.triple_window_throttle.short_max == 3
        assert runtime.sleep.triple_window_throttle.long_max == 12
        assert runtime.sleep.triple_window_throttle.extra_max == 90
        assert runtime.sleep.triple_window_throttle.long_window == 180.0
        assert (
            legacy_throttle.short_max,
            legacy_throttle.long_max,
            legacy_throttle.extra_max,
            legacy_throttle.short_window,
            legacy_throttle.long_window,
            legacy_throttle.extra_window,
            legacy_throttle._base_short_max,
            legacy_throttle._base_long_max,
            legacy_throttle._base_extra_max,
        ) == legacy_original
    finally:
        (
            legacy_throttle.short_max,
            legacy_throttle.long_max,
            legacy_throttle.extra_max,
            legacy_throttle.short_window,
            legacy_throttle.long_window,
            legacy_throttle.extra_window,
            legacy_throttle._base_short_max,
            legacy_throttle._base_long_max,
            legacy_throttle._base_extra_max,
        ) = legacy_original


def test_runtime_runner_registry_startup_updates_runtime_sleep(monkeypatch):
    from javdb.proxy.coordinator.runner_registry_client import (
        RegisterResult,
        RunnerInfo,
    )
    from javdb.spider.runtime import sleep as sleep_module
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    client = MagicMock()
    client.register.return_value = RegisterResult(
        registered=True,
        active_runners=[
            RunnerInfo(
                holder_id="runner-a",
                workflow_run_id="1",
                workflow_name="daily",
                started_at=1,
                last_heartbeat=1,
                proxy_pool_hash="h",
                page_range=None,
            ),
            RunnerInfo(
                holder_id="runner-b",
                workflow_run_id="2",
                workflow_name="daily",
                started_at=1,
                last_heartbeat=1,
                proxy_pool_hash="h",
                page_range=None,
            ),
            RunnerInfo(
                holder_id="runner-c",
                workflow_run_id="3",
                workflow_name="daily",
                started_at=1,
                last_heartbeat=1,
                proxy_pool_hash="h",
                page_range=None,
            ),
        ],
        pool_hash_summary=[],
        server_time_ms=1,
    )
    legacy_mgr = sleep_module.movie_sleep_mgr
    legacy_long_max = legacy_mgr._throttle.long_max
    legacy_extra_max = legacy_mgr._throttle.extra_max

    class LiveThread:
        def __init__(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.alive = False
            return None

    monkeypatch.setattr(
        "javdb.infra.config.cfg",
        lambda name, default="": {
            "RUNNER_REGISTRY_ENABLED": "true",
            "PROXY_COORDINATOR_URL": "https://coord.test",
            "PROXY_COORDINATOR_TOKEN": "t",
        }.get(name, default),
    )
    monkeypatch.setattr(
        state,
        "create_runner_registry_client_from_env",
        lambda: client,
    )
    monkeypatch.setattr(state, "_warn_on_proxy_pool_drift", lambda *_args: None)
    monkeypatch.setattr(state, "_resolve_proxy_pool_json", lambda: "[]")
    monkeypatch.setattr(state, "PROXY_POOL", [])
    runtime.runner_registry.heartbeat_thread = LiveThread()

    assert runtime.setup_runner_registry_client() is client

    assert runtime.sleep.triple_window_throttle.long_max == 10
    assert runtime.sleep.triple_window_throttle.extra_max == 66
    assert legacy_mgr._throttle.long_max == legacy_long_max
    assert legacy_mgr._throttle.extra_max == legacy_extra_max


def test_runtime_proxy_coordinator_injects_runtime_sleep(monkeypatch):
    from javdb.spider.runtime import sleep as sleep_module
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    client = MagicMock()
    client.health_check.return_value = True
    legacy_mgr = sleep_module.movie_sleep_mgr
    legacy_coordinator = legacy_mgr._coordinator

    monkeypatch.setattr(
        "javdb.infra.config.cfg",
        lambda name, default="": {
            "PROXY_COORDINATOR_URL": "https://coord.test",
            "PROXY_COORDINATOR_TOKEN": "t",
        }.get(name, default),
    )
    monkeypatch.setattr(
        state,
        "ProxyCoordinatorClient",
        lambda base_url, token: client,
    )

    assert runtime.setup_proxy_coordinator() is client

    assert runtime.sleep.movie_sleep_mgr._coordinator is client
    assert legacy_mgr._coordinator is legacy_coordinator


def test_sleep_runtime_copies_existing_coordinator_binding():
    from javdb.spider.runtime import sleep as sleep_module

    runtime = SpiderRuntime()
    coordinator = MagicMock()
    legacy_mgr = sleep_module.movie_sleep_mgr
    legacy_coordinator = legacy_mgr._coordinator
    legacy_proxy_id = legacy_mgr._proxy_id
    legacy_coord_failures = legacy_mgr._coord_failures
    legacy_degraded = legacy_mgr._degraded

    try:
        legacy_mgr.set_coordinator(coordinator, proxy_id="proxy-runtime")

        sleep_ctx = ensure_sleep_runtime(runtime)

        assert sleep_ctx.movie_sleep_mgr.has_coordinator() is True
        assert sleep_ctx.movie_sleep_mgr._coordinator is coordinator
        assert sleep_ctx.movie_sleep_mgr._proxy_id == "proxy-runtime"
    finally:
        legacy_mgr._coordinator = legacy_coordinator
        legacy_mgr._proxy_id = legacy_proxy_id
        legacy_mgr._coord_failures = legacy_coord_failures
        legacy_mgr._degraded = legacy_degraded


def test_sleep_runtime_does_not_copy_legacy_proxy_id_for_runtime_coordinator():
    from javdb.spider.runtime import sleep as sleep_module

    runtime = SpiderRuntime()
    runtime_coordinator = MagicMock()
    legacy_coordinator = MagicMock()
    runtime.services.proxy_coordinator = runtime_coordinator
    legacy_mgr = sleep_module.movie_sleep_mgr
    legacy_original_coordinator = legacy_mgr._coordinator
    legacy_proxy_id = legacy_mgr._proxy_id
    legacy_coord_failures = legacy_mgr._coord_failures
    legacy_degraded = legacy_mgr._degraded

    try:
        legacy_mgr.set_coordinator(legacy_coordinator, proxy_id="stale-proxy")

        sleep_ctx = ensure_sleep_runtime(runtime)

        assert sleep_ctx.movie_sleep_mgr.has_coordinator() is True
        assert sleep_ctx.movie_sleep_mgr._coordinator is runtime_coordinator
        assert sleep_ctx.movie_sleep_mgr._proxy_id is None
    finally:
        legacy_mgr._coordinator = legacy_original_coordinator
        legacy_mgr._proxy_id = legacy_proxy_id
        legacy_mgr._coord_failures = legacy_coord_failures
        legacy_mgr._degraded = legacy_degraded


def test_explicit_runtime_fallback_fetch_uses_runtime_request_handler(monkeypatch):
    import javdb.spider.fetch.fallback as fallback
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    handler = MagicMock()
    handler.get_page.return_value = (
        '<html><body><div class="movie-list"><div class="item">ok</div></div></body></html>'
    )
    runtime.services.request_handler = handler

    state.clear_active_runtime()
    state.global_request_handler = None
    monkeypatch.setattr(fallback, "is_cf_bypass_reachable", lambda **_kwargs: False)

    result = fallback.fetch_index_page_with_fallback(
        "https://javdb.com/page/1",
        session=object(),
        use_cookie=True,
        use_proxy=False,
        use_cf_bypass=False,
        page_num=1,
        runtime=runtime,
    )

    assert result == (
        handler.get_page.return_value,
        True,
        False,
        False,
        False,
        False,
    )
    handler.get_page.assert_called_once_with(
        url="https://javdb.com/page/1",
        session=handler.get_page.call_args.kwargs["session"],
        use_cookie=True,
        use_proxy=False,
        module_name="spider",
        max_retries=1,
        use_cf_bypass=False,
    )


def test_explicit_runtime_fallback_fetch_does_not_use_legacy_handler(monkeypatch):
    import javdb.spider.fetch.fallback as fallback
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    legacy_handler = MagicMock()
    legacy_handler.get_page.side_effect = AssertionError(
        "explicit runtime must not fall back to legacy request handler"
    )

    state.clear_active_runtime()
    state.global_request_handler = legacy_handler
    monkeypatch.setattr(fallback, "is_cf_bypass_reachable", lambda **_kwargs: False)

    result = fallback.fetch_index_page_with_fallback(
        "https://javdb.com/page/1",
        session=object(),
        use_cookie=True,
        use_proxy=False,
        use_cf_bypass=False,
        page_num=1,
        runtime=runtime,
    )

    assert result == (None, False, False, False, False, False)
    legacy_handler.get_page.assert_not_called()


def test_fetch_all_index_pages_parallel_receives_explicit_runtime(monkeypatch):
    import javdb.spider.fetch.index as index
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    observed = {}

    def fake_parallel_fetch(*args, **kwargs):
        observed["runtime"] = kwargs["runtime"]
        return {
            "all_index_results_phase1": [],
            "all_index_results_phase2": [],
            "any_proxy_banned": False,
            "use_proxy": True,
            "use_cf_bypass": False,
            "csv_path": "reports/out.csv",
            "last_valid_page": 0,
        }

    state.clear_active_runtime()
    monkeypatch.setattr(index, "PROXY_POOL", [{"name": "proxy-a"}], raising=False)
    monkeypatch.setattr(
        "javdb.spider.fetch.index_parallel.fetch_all_index_pages_parallel",
        fake_parallel_fetch,
    )

    index.fetch_all_index_pages(
        runtime=runtime,
        session=object(),
        start_page=1,
        end_page=1,
        parse_all=False,
        phase_mode="all",
        custom_url=None,
        ignore_release_date=False,
        use_proxy=True,
        use_cf_bypass=False,
        max_consecutive_empty=3,
        output_csv="out.csv",
        output_dated_dir="reports",
        csv_path="reports/out.csv",
        user_specified_output=False,
        parsed_movies_history_phase1={},
        parsed_movies_history_phase2={},
        use_parallel=True,
    )

    assert observed["runtime"] is runtime


def test_index_post_process_applies_runtime_sleep_volume():
    import javdb.spider.fetch.index as index
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    state.clear_active_runtime()

    index._post_process_index_results(
        {
            "all_index_results_phase1": [{"href": "/v/a"}],
            "all_index_results_phase2": [{"href": "/v/b"}],
        },
        custom_url="https://javdb.com/actors/EvkJ",
        parsed_movies_history_phase1={},
        parsed_movies_history_phase2={},
        num_workers=1,
        runtime=runtime,
    )

    assert runtime.sleep.movie_sleep_mgr.last_volume_total == 2


def test_attempt_login_refresh_uses_explicit_runtime_login_state(monkeypatch):
    import sys
    import types

    import javdb.spider.fetch.session as session
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    handler = MagicMock()
    runtime.services.request_handler = handler

    state.clear_active_runtime()
    state.global_request_handler = None
    state.login_total_attempts = 10
    state.login_total_budget = 10
    state.login_attempts_per_proxy = {"proxy-a": 99}

    monkeypatch.setattr(session, "LOGIN_FEATURE_AVAILABLE", True)
    monkeypatch.setattr(session, "JAVDB_USERNAME", "user")
    monkeypatch.setattr(session, "JAVDB_PASSWORD", "pass")

    fake_login_mod = types.ModuleType("javdb.spider.auth.login")
    fake_login_mod.login_with_retry = lambda *a, **kw: (True, "cookie-runtime", "ok")
    fake_login_mod.update_config_file = lambda cookie: False
    monkeypatch.setitem(sys.modules, "javdb.spider.auth.login", fake_login_mod)

    success, cookie, proxy_name = session.attempt_login_refresh(
        explicit_proxies={"http": "http://proxy-a:1"},
        explicit_proxy_name="proxy-a",
        publish_to_do=False,
        runtime=runtime,
    )

    assert success is True
    assert cookie == "cookie-runtime"
    assert proxy_name == "proxy-a"
    assert runtime.login.login_attempted is True
    assert runtime.login.login_total_attempts == 1
    assert runtime.login.login_attempts_per_proxy == {"proxy-a": 1}
    assert runtime.login.logged_in_proxy_name == "proxy-a"
    assert runtime.login.refreshed_session_cookie == "cookie-runtime"
    assert handler.config.javdb_session_cookie == "cookie-runtime"
    assert state.login_attempts_per_proxy == {"proxy-a": 99}


def test_login_coordinator_uses_explicit_runtime_do_client_and_holder(monkeypatch):
    import queue

    from javdb.proxy.coordinator.login_state_client import AcquireLeaseResult
    from javdb.spider.fetch.login_coordinator import LoginCoordinator
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    runtime.runner_registry.holder_id = "runtime-holder"
    client = MagicMock()
    client.acquire_lease.return_value = AcquireLeaseResult(
        acquired=True,
        holder_id="runtime-holder",
        target_proxy_name="proxy-a",
        lease_expires_at=99_999,
        server_time_ms=0,
    )
    runtime.services.login_state_client = client

    state.clear_active_runtime()
    state.global_login_state_client = None
    state.runtime_holder_id = "legacy-holder"

    worker = MagicMock()
    worker.proxy_name = "proxy-a"
    coordinator = LoginCoordinator(all_workers=[worker], runtime=runtime)

    assert coordinator._try_acquire_login_lease("proxy-a") is True
    client.acquire_lease.assert_called_once()
    assert client.acquire_lease.call_args.args[0] == "runtime-holder"

    coordinator._release_login_lease()
    client.release_lease.assert_called_once_with("runtime-holder")

    runtime.login.login_attempts_per_proxy = {"proxy-a": 5}
    next_worker, cookie = coordinator._find_and_login_next_worker(exclude=set())
    assert (next_worker, cookie) == (None, None)

    # Smoke the unknown-target park path without relying on a global client.
    task_queue = queue.Queue()
    coordinator._park_login_task_for_unknown_target(object(), task_queue, "proxy-a")
    assert coordinator._pending_login_tasks[0][0] == "proxy-a"


def test_process_detail_entries_uses_explicit_runtime_detail_and_services(monkeypatch):
    from javdb.proxy.coordinator.work_distributor_client import (
        EnqueueResult,
        PullResult,
        WorkItem,
    )
    from javdb.spider.detail import runner as detail_runner
    from javdb.spider.detail.runner import DetailPersistOutcome, process_detail_entries
    from javdb.spider.fetch.backend import FetchRuntimeState
    import javdb.spider.runtime.state as state

    class Backend:
        def __init__(self):
            self.submitted = []

        @property
        def worker_count(self):
            return 1

        def start(self):
            pass

        def submit_task(self, task):
            self.submitted.append(task)

        def mark_done(self):
            pass

        def results(self):
            return iter([])

        def shutdown(self, *, timeout=10):
            return []

        def runtime_state(self):
            return FetchRuntimeState(use_proxy=False, use_cf_bypass=False)

    runtime = SpiderRuntime()
    runtime.runner_registry.holder_id = "runtime-holder"
    runtime.detail.parsed_links.add("/v/skip-global")
    work_client = MagicMock()
    work_client.enqueue.return_value = EnqueueResult(
        enqueued=["/v/runtime"], duplicates=[], queue_size=1, server_time_ms=1,
    )
    work_client.pull.side_effect = [
        PullResult(
            items=[
                WorkItem(key="/v/runtime", payload=None, enqueued_at_ms=1, attempt_count=1),
                WorkItem(key="/v/peer", payload=None, enqueued_at_ms=1, attempt_count=1),
            ],
            queue_size=2,
            server_time_ms=1,
        ),
        PullResult(items=[], queue_size=0, server_time_ms=1),
    ]
    runtime.services.work_distributor_client = work_client

    state.clear_active_runtime()
    state.parsed_links.clear()
    state.global_work_distributor_client = None
    state.runtime_holder_id = "legacy-holder"

    monkeypatch.setattr(detail_runner, "has_complete_subtitles", lambda *a, **kw: False)
    monkeypatch.setattr(detail_runner, "should_skip_from_rclone", lambda *a, **kw: False)
    monkeypatch.setattr(detail_runner, "should_skip_recent_today_release", lambda *a, **kw: False)
    monkeypatch.setattr(detail_runner, "should_skip_recent_yesterday_release", lambda *a, **kw: False)
    monkeypatch.setattr(
        detail_runner,
        "persist_parsed_detail_result",
        lambda **kw: DetailPersistOutcome(status="reported"),
    )
    monkeypatch.setattr(detail_runner, "finalize_detail_phase", lambda **kw: None)

    backend = Backend()
    entries = [
        {"video_code": "SKIP", "href": "/v/skip-global", "page": 1},
        {"video_code": "RUN", "href": "/v/runtime", "page": 1},
    ]

    out = process_detail_entries(
        runtime=runtime,
        backend=backend,
        entries=entries,
        phase=1,
        history_data={},
        history_file="",
        csv_path="",
        fieldnames=[],
        dry_run=True,
        use_history_for_saving=False,
        is_adhoc_mode=False,
    )

    assert out["failed"] == 0
    assert [task.meta["entry"]["href"] for task in backend.submitted] == ["/v/runtime"]
    work_client.enqueue.assert_called_once_with(["/v/runtime"])
    assert work_client.pull.call_args_list[0].args[0] == "runtime-holder"
    release_calls = [call.args for call in work_client.release.call_args_list]
    assert ("runtime-holder", ["/v/peer"]) in release_calls
    assert "/v/runtime" in runtime.detail.parsed_links
    assert state.parsed_links == set()


def test_parallel_detail_wrapper_forwards_explicit_runtime(monkeypatch):
    from javdb.spider.detail import parallel_mode

    runtime = SpiderRuntime()
    backend = object()
    observed = {}

    def fake_build_parallel_detail_backend(**kwargs):
        observed["build"] = kwargs
        return backend

    def fake_process_detail_entries(**kwargs):
        observed["process"] = kwargs
        return {"rows": 0}

    monkeypatch.setattr(
        parallel_mode,
        "build_parallel_detail_backend",
        fake_build_parallel_detail_backend,
    )
    monkeypatch.setattr(
        parallel_mode,
        "process_detail_entries",
        fake_process_detail_entries,
    )

    result = parallel_mode.process_detail_entries_parallel(
        entries=[],
        phase=1,
        history_data={},
        history_file="history.csv",
        csv_path="out.csv",
        fieldnames=[],
        dry_run=True,
        use_history_for_saving=False,
        use_cookie=True,
        is_adhoc_mode=False,
        runtime=runtime,
    )

    assert result == {"rows": 0}
    assert observed["build"]["runtime"] is runtime
    assert observed["build"]["use_cookie"] is True
    assert observed["process"]["runtime"] is runtime
    assert observed["process"]["backend"] is backend
    assert observed["process"]["include_recent_release_filters"] is True


def test_sequential_detail_wrapper_forwards_explicit_runtime(monkeypatch):
    from javdb.spider.detail import sequential_mode

    runtime = SpiderRuntime()
    session = object()
    backend = object()
    observed = {}

    def fake_build_sequential_detail_backend(*args, **kwargs):
        observed["build_args"] = args
        observed["build"] = kwargs
        return backend

    def fake_process_detail_entries(**kwargs):
        observed["process"] = kwargs
        return {"rows": 0}

    monkeypatch.setattr(
        sequential_mode,
        "build_sequential_detail_backend",
        fake_build_sequential_detail_backend,
    )
    monkeypatch.setattr(
        sequential_mode,
        "process_detail_entries",
        fake_process_detail_entries,
    )

    result = sequential_mode.process_phase_entries_sequential(
        entries=[],
        phase=2,
        history_data={},
        history_file="history.csv",
        csv_path="out.csv",
        fieldnames=[],
        dry_run=True,
        use_history_for_saving=False,
        use_cookie=True,
        is_adhoc_mode=True,
        session=session,
        use_proxy=True,
        use_cf_bypass=False,
        runtime=runtime,
    )

    assert result == {"rows": 0}
    assert observed["build_args"] == (session,)
    assert observed["build"]["runtime"] is runtime
    assert observed["build"]["use_cookie"] is True
    assert observed["build"]["is_adhoc_mode"] is True
    assert observed["build"]["use_proxy"] is True
    assert observed["process"]["runtime"] is runtime
    assert observed["process"]["backend"] is backend
    assert observed["process"]["include_recent_release_filters"] is False
    assert observed["process"]["log_duplicate_skips"] is True


def test_parallel_fetch_backend_start_adjusts_runtime_login_budget(monkeypatch):
    import javdb.spider.fetch.fetch_engine as fetch_engine
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    runtime.login.login_total_attempts = 0
    runtime.login.login_total_budget = 10
    state.login_total_attempts = 7
    state.login_total_budget = 70
    state._login_budget_deducted_proxies.clear()

    class BanManager:
        def is_proxy_banned(self, name):
            return name == "proxy-a"

    monkeypatch.setattr(fetch_engine, "PROXY_POOL", [
        {"name": "proxy-a", "http": "http://a:1", "https": "http://a:1"},
        {"name": "proxy-b", "http": "http://b:1", "https": "http://b:1"},
    ])
    monkeypatch.setattr(fetch_engine, "LOGIN_ATTEMPTS_PER_PROXY_LIMIT", 5)
    monkeypatch.setattr(fetch_engine, "get_ban_manager", lambda: BanManager())
    monkeypatch.setattr(fetch_engine, "LOGIN_PROXY_NAME", None)
    monkeypatch.setattr(fetch_engine, "_EngineWorker", MagicMock())

    backend = fetch_engine.ParallelFetchBackend(
        lambda _ctx, _task: None,
        runtime=runtime,
    )
    backend._inherit_login_state = lambda: None
    backend._inherit_global_volume = lambda _num_workers: None

    backend.start()

    assert runtime.login.login_total_budget == 5
    assert runtime.login.login_budget_deducted_proxies == {"proxy-a"}
    assert state.login_total_budget == 70
    assert "proxy-a" not in state._login_budget_deducted_proxies


def test_parallel_fetch_backend_start_deducts_runtime_budget_after_attempts(monkeypatch):
    import javdb.spider.fetch.fetch_engine as fetch_engine
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    runtime.login.login_total_attempts = 3
    runtime.login.login_total_budget = 10
    runtime.login.login_attempts_per_proxy = {"proxy-a": 2}
    state.login_total_attempts = 7
    state.login_total_budget = 70
    state._login_budget_deducted_proxies.clear()

    class BanManager:
        def is_proxy_banned(self, name):
            return name == "proxy-a"

    monkeypatch.setattr(fetch_engine, "PROXY_POOL", [
        {"name": "proxy-a", "http": "http://a:1", "https": "http://a:1"},
        {"name": "proxy-b", "http": "http://b:1", "https": "http://b:1"},
    ])
    monkeypatch.setattr(fetch_engine, "LOGIN_ATTEMPTS_PER_PROXY_LIMIT", 5)
    monkeypatch.setattr(fetch_engine, "get_ban_manager", lambda: BanManager())
    monkeypatch.setattr(fetch_engine, "LOGIN_PROXY_NAME", None)
    monkeypatch.setattr(fetch_engine, "_EngineWorker", MagicMock())
    monkeypatch.setattr(
        state,
        "deduct_proxy_login_budget",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("legacy budget deduction must not run with runtime")
        ),
    )

    backend = fetch_engine.ParallelFetchBackend(
        lambda _ctx, _task: None,
        runtime=runtime,
    )
    backend._inherit_login_state = lambda: None
    backend._inherit_global_volume = lambda _num_workers: None

    backend.start()

    assert runtime.login.login_total_budget == 7
    assert runtime.login.login_budget_deducted_proxies == {"proxy-a"}
    assert state.login_total_budget == 70


def test_engine_worker_proxy_ban_deducts_runtime_login_budget(monkeypatch):
    import queue
    import threading

    import javdb.spider.fetch.fetch_engine as fetch_engine
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    runtime.login.login_total_attempts = 3
    runtime.login.login_total_budget = 10
    runtime.login.login_attempts_per_proxy = {"proxy-a": 2}
    state.login_total_attempts = 7
    state.login_total_budget = 70
    state._login_budget_deducted_proxies.clear()
    monkeypatch.setattr(fetch_engine, "LOGIN_ATTEMPTS_PER_PROXY_LIMIT", 5)
    monkeypatch.setattr(
        state,
        "deduct_proxy_login_budget",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("legacy budget deduction must not run with runtime")
        ),
    )

    worker = object.__new__(fetch_engine._EngineWorker)
    worker._runtime = runtime
    worker.proxy_name = "proxy-a"
    worker.total_workers = 1
    worker._banned_proxies = set()
    worker._capped_proxies = set()
    worker._drain_lock = threading.Lock()
    worker.task_queue = queue.Queue()
    worker.login_queue = queue.Queue()
    worker.result_queue = queue.Queue()
    worker._per_worker_task_limit = 0
    worker.all_workers = [worker]
    worker._drain_done = [False]

    task = fetch_engine.EngineTask(url="https://javdb.com/v/x")

    worker._handle_proxy_banned(task)

    assert runtime.login.login_total_budget == 7
    assert runtime.login.login_budget_deducted_proxies == {"proxy-a"}
    assert state.login_total_budget == 70


def test_parallel_fetch_backend_export_login_state_updates_runtime(monkeypatch):
    import javdb.spider.fetch.fetch_engine as fetch_engine
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    state.logged_in_proxy_name = "legacy-proxy"
    state.refreshed_session_cookie = "legacy-cookie"

    backend = fetch_engine.ParallelFetchBackend(
        lambda _ctx, _task: None,
        runtime=runtime,
    )
    backend._coordinator = MagicMock(logged_in_worker_id=42)
    worker = MagicMock()
    worker.worker_id = 42
    worker.proxy_name = "runtime-proxy"
    worker._handler.config.javdb_session_cookie = "runtime-cookie"
    backend._workers = [worker]

    backend.export_login_state()

    assert runtime.login.logged_in_proxy_name == "runtime-proxy"
    assert runtime.login.refreshed_session_cookie == "runtime-cookie"
    assert state.logged_in_proxy_name == "legacy-proxy"
    assert state.refreshed_session_cookie == "legacy-cookie"


def test_can_attempt_login_uses_explicit_runtime_login_attempted(monkeypatch):
    import javdb.spider.fetch.session as session
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    runtime.login.login_attempted = True
    state.login_attempted = False
    monkeypatch.setattr(session, "LOGIN_FEATURE_AVAILABLE", True)

    assert session.can_attempt_login(
        is_adhoc_mode=True,
        is_index_page=False,
        runtime=runtime,
    ) is False
    assert session.can_attempt_login(
        is_adhoc_mode=True,
        is_index_page=False,
    ) is True


def test_login_refresh_for_spider_forwards_explicit_runtime(monkeypatch):
    import javdb.spider.fetch.fallback as fallback

    runtime = SpiderRuntime()
    observed = {}

    def fake_attempt_login_refresh(*args, **kwargs):
        observed["runtime"] = kwargs.get("runtime")
        return True, "cookie", None

    monkeypatch.setattr(
        fallback,
        "attempt_login_refresh",
        fake_attempt_login_refresh,
    )

    assert fallback._login_refresh_for_spider(False, runtime=runtime)[0] is True
    assert observed["runtime"] is runtime


def test_fallback_cf_bypass_marker_uses_explicit_runtime(monkeypatch):
    import javdb.spider.fetch.fallback as fallback
    import javdb.spider.runtime.state as state

    runtime = SpiderRuntime()
    runtime.proxy.always_bypass_time = 0
    state.always_bypass_time = None
    state.proxies_requiring_cf_bypass.clear()

    fallback._mark_proxy_cf_bypass("proxy-a", runtime=runtime)

    assert runtime.proxy.proxies_requiring_cf_bypass.keys() == {"proxy-a"}
    assert state.proxies_requiring_cf_bypass == {}
    assert fallback._proxy_needs_cf_bypass("proxy-a", runtime=runtime) is True
    assert fallback._proxy_needs_cf_bypass("proxy-a") is False


def test_sequential_backend_sleeps_with_explicit_runtime(monkeypatch):
    from javdb.spider.fetch.sequential_backend import SequentialFetchBackend
    import javdb.spider.fetch.sequential_backend as sequential_backend

    runtime = SpiderRuntime()
    ensure_sleep_runtime(runtime)
    calls = []
    runtime.sleep.movie_sleep_mgr.sleep = lambda: calls.append("runtime") or 0.0
    monkeypatch.setattr(
        sequential_backend.movie_sleep_mgr,
        "sleep",
        lambda: (_ for _ in ()).throw(
            AssertionError("legacy sleep manager must not run with runtime")
        ),
    )

    backend = SequentialFetchBackend(
        object(),
        runtime=runtime,
        use_proxy=False,
        use_cf_bypass=False,
        use_cookie=False,
        is_adhoc_mode=False,
    )

    backend._acknowledge_result("reported", runtime_state_changed=True)

    assert calls == ["runtime"]


def test_clear_active_runtime_clears_runtime_service_globals_only():
    import javdb.spider.runtime.state as state

    original_handler = state.global_request_handler
    runtime = SpiderRuntime()
    runtime.services.proxy_pool = object()
    runtime.services.request_handler = object()
    runtime.services.proxy_coordinator = object()
    runtime.services.login_state_client = object()
    runtime.services.runner_registry_client = object()
    runtime.services.recommend_proxy_policy = object()
    runtime.services.work_distributor_client = object()
    runtime.movie_claim.client_public = object()

    try:
        state.bind_active_runtime(runtime)
        assert state.global_proxy_pool is runtime.services.proxy_pool
        assert state.global_request_handler is runtime.services.request_handler

        replacement_handler = object()
        state.global_request_handler = replacement_handler

        state.clear_active_runtime(runtime)

        assert state.get_active_runtime() is None
        assert state.global_proxy_pool is None
        assert state.global_proxy_coordinator is None
        assert state.global_login_state_client is None
        assert state.global_runner_registry_client is None
        assert state.global_recommend_proxy_policy is None
        assert state.global_work_distributor_client is None
        assert state.global_movie_claim_client is None
        assert state.global_request_handler is replacement_handler
    finally:
        state.global_request_handler = original_handler
