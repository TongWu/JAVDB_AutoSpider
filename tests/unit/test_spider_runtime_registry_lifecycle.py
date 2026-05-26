from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from javdb.proxy.coordinator.runner_registry_client import UnregisterResult
from javdb.spider.runtime.context import SpiderRuntime


@pytest.fixture(autouse=True)
def _reset_active_runtime():
    import javdb.spider.runtime.state as state

    active = state.get_active_runtime()
    if active is not None:
        state.clear_active_runtime(active)
    yield
    active = state.get_active_runtime()
    if active is not None:
        state.clear_active_runtime(active)


def test_runtime_close_stops_heartbeat_and_unregisters_once():
    runtime = SpiderRuntime()
    client = MagicMock()
    client.unregister.return_value = UnregisterResult(
        unregistered=True,
        server_time_ms=1,
    )
    runtime.services.runner_registry_client = client
    runtime.runner_registry.heartbeat_stop.clear()

    runtime.close()
    runtime.close()

    assert runtime.runner_registry.heartbeat_stop.is_set()
    assert runtime.services.runner_registry_client is None
    client.unregister.assert_called_once_with(
        runtime.runner_registry.holder_id,
        session=runtime.runner_registry.session,
    )


def test_runtime_close_joins_live_heartbeat_thread():
    runtime = SpiderRuntime()
    started = threading.Event()
    stop_seen = {}

    def worker():
        started.set()
        runtime.runner_registry.heartbeat_stop.wait(timeout=2)
        stop_seen["stopped"] = runtime.runner_registry.heartbeat_stop.is_set()

    thread = threading.Thread(target=worker, daemon=True)
    runtime.runner_registry.heartbeat_thread = thread
    thread.start()
    assert started.wait(timeout=1)

    runtime.close()

    assert stop_seen["stopped"] is True
    assert not thread.is_alive()


def test_runtime_close_refreshes_legacy_runner_registry_facade():
    runtime = SpiderRuntime()
    runtime.services.runner_registry_client = MagicMock()

    runtime.close()

    import javdb.spider.runtime.state as state

    assert state.global_runner_registry_client is None
    assert runtime.services.runner_registry_client is None
    assert runtime.runner_registry.heartbeat_thread is None
