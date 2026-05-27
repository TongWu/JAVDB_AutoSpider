from __future__ import annotations

import pytest

import javdb.spider.runtime.state as state
from javdb.proxy.coordinator.runner_registry_client import ConfigSnapshot, Signal
from javdb.proxy.coordinator.movie_claim_client import MOVIE_CLAIM_MODE_AUTO
from javdb.spider.runtime.context import SpiderRuntime


@pytest.fixture(autouse=True)
def _reset_active_runtime():
    active = state.get_active_runtime()
    if active is not None:
        state.clear_active_runtime(active)
    yield
    active = state.get_active_runtime()
    if active is not None:
        state.clear_active_runtime(active)


def test_bind_active_runtime_rebinds_mutable_detail_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    state.bind_active_runtime(first)
    state.parsed_links.add("/v/first")
    assert first.detail.parsed_links == {"/v/first"}

    state.bind_active_runtime(second)
    assert state.parsed_links is second.detail.parsed_links
    assert state.parsed_links == set()


def test_bind_active_runtime_rebinds_proxy_ban_html_files():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)
    state.proxy_ban_html_files.append("logs/proxy_ban.txt")

    assert runtime.proxy.proxy_ban_html_files == ["logs/proxy_ban.txt"]


def test_bind_active_runtime_exposes_runtime_holder_id():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)

    assert state.runtime_holder_id == runtime.runner_registry.holder_id


def test_clear_active_runtime_leaves_facade_importable():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)
    state.clear_active_runtime(runtime)

    assert state.get_active_runtime() is None
    assert isinstance(state.parsed_links, set)


def test_legacy_login_state_can_explicitly_clear_none_values():
    state.set_legacy_login_state(
        proxy_name="proxy-a",
        cookie="cookie-a",
        version=3,
    )

    assert state.get_legacy_login_state() == ("proxy-a", "cookie-a", 3)

    state.set_legacy_login_state(proxy_name=None, cookie=None, version=None)

    assert state.get_legacy_login_state() == (None, None, None)


def test_legacy_login_context_adapter_updates_module_state():
    login_ctx = state.get_legacy_login_context()

    login_ctx.login_attempted = True
    login_ctx.login_total_attempts = 2
    login_ctx.login_total_budget = 5
    login_ctx.login_attempts_per_proxy["proxy-a"] = 1
    login_ctx.login_failures_per_proxy["proxy-a"] = 0

    assert state.login_attempted is True
    assert state.login_total_attempts == 2
    assert state.login_total_budget == 5
    assert state.login_attempts_per_proxy == {"proxy-a": 1}
    assert state.login_failures_per_proxy == {"proxy-a": 0}


def test_bound_runtime_owns_runner_session_facade():
    runtime = SpiderRuntime()
    state.bind_active_runtime(runtime)

    state.set_active_runner_session(
        session_id="session-1",
        status="in_progress",
        write_mode="pending",
    )

    assert runtime.runner_registry.session is state._runner_session
    assert runtime.runner_registry.session.session_id == "session-1"
    assert runtime.runner_registry.session.status == "in_progress"
    assert runtime.runner_registry.session.write_mode == "pending"


def test_bound_runtime_owns_config_snapshot_facade():
    runtime = SpiderRuntime()
    state.bind_active_runtime(runtime)

    state._apply_config_snapshot(
        ConfigSnapshot(
            version=12,
            updated_at_ms=0,
            values={"heartbeat_interval_sec": "9"},
        )
    )

    assert runtime.runner_registry.last_applied_config_version == 12
    assert runtime.runner_registry.runner_heartbeat_interval_sec == 9.0
    assert runtime.runner_registry.heartbeat_interval_multi_runner_sec == 9.0
    assert state._last_applied_config_version == 12
    assert state._RUNNER_HEARTBEAT_INTERVAL_SEC == 9.0


def test_bound_runtime_owns_active_signal_facade():
    runtime = SpiderRuntime()
    fake_pool = type(
        "FakePool",
        (),
        {
            "ban_proxy": lambda self, proxy_id: self.banned.append(proxy_id),
            "unban_proxy": lambda self, proxy_id: self.unbanned.append(proxy_id),
        },
    )()
    fake_pool.banned = []
    fake_pool.unbanned = []
    runtime.services.proxy_pool = fake_pool
    state.bind_active_runtime(runtime)

    state._apply_active_signals([
        Signal(
            id="sig-1",
            kind="ban_proxy",
            expires_at_ms=1,
            created_at_ms=1,
            proxy_id="Proxy-A",
        )
    ])
    state._apply_active_signals([])

    assert runtime.runner_registry.signal_banned_proxies == set()
    assert state._signal_banned_proxies is runtime.runner_registry.signal_banned_proxies
    assert fake_pool.banned == ["Proxy-A"]
    assert fake_pool.unbanned == ["Proxy-A"]


def test_bound_runtime_reads_movie_claim_recommendation_for_heartbeat_interval():
    runtime = SpiderRuntime()
    runtime.movie_claim.mode = MOVIE_CLAIM_MODE_AUTO
    state.bind_active_runtime(runtime)

    assert runtime._next_heartbeat_interval() == (
        runtime.runner_registry.heartbeat_interval_single_runner_sec
    )

    state._apply_movie_claim_recommendation(True)

    assert runtime.movie_claim.last_recommended is True
    assert runtime._next_heartbeat_interval() == (
        runtime.runner_registry.runner_heartbeat_interval_sec
    )


def test_bound_runtime_owns_movie_claim_setup_and_interval_facade(monkeypatch):
    runtime = SpiderRuntime()
    runtime.movie_claim.mode = MOVIE_CLAIM_MODE_AUTO
    runtime.movie_claim.last_recommended = False
    state.bind_active_runtime(runtime)

    with monkeypatch.context() as m:
        m.setattr(runtime, "setup_movie_claim_client", lambda: "runtime-client")
        assert state.setup_movie_claim_client() == "runtime-client"

    assert state._next_heartbeat_interval() == (
        runtime.runner_registry.heartbeat_interval_single_runner_sec
    )
