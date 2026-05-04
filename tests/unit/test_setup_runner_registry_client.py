"""Tests for :func:`runtime.state.setup_runner_registry_client` (P2-E).

Locks the integration boundary the spider actually invokes:

- "未配置时行为完全等同今天" — every disable path returns ``None`` and
  leaves the global state pristine.
- Drift detection — a runner whose ``proxy_pool_hash`` differs from
  the cohort majority emits a single ``WARNING`` log line; agreeing
  runners stay quiet.
- Heartbeat daemon lifecycle — daemon spawns on success, stops on
  ``unregister``, never raises into the main thread.
- atexit hook — idempotent, never raises, calls both ``unregister``
  and ``close``.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import packages.python.javdb_spider.runtime.state as state  # noqa: E402
from packages.python.javdb_platform.runner_registry_client import (  # noqa: E402
    HeartbeatResult,
    PoolHashBucket,
    RegisterResult,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    UnregisterResult,
)


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    """Force a clean factory state for every test.

    The state module keeps several module-level singletons
    (``global_runner_registry_client``, ``_runner_heartbeat_thread``,
    ``_runner_unregistered``) that span tests; reset them all so each
    test starts from "no registry, no daemon, not unregistered".
    """
    monkeypatch.setattr(state, "global_runner_registry_client", None, raising=False)
    monkeypatch.setattr(state, "_runner_heartbeat_thread", None, raising=False)
    monkeypatch.setattr(state, "_runner_unregistered", False, raising=False)
    state._runner_heartbeat_stop.clear()
    yield
    # Stop any daemon the test left running, defensively.
    state._runner_heartbeat_stop.set()
    monkeypatch.setattr(state, "global_runner_registry_client", None, raising=False)
    monkeypatch.setattr(state, "_runner_heartbeat_thread", None, raising=False)
    monkeypatch.setattr(state, "_runner_unregistered", False, raising=False)


def _patch_cfg(monkeypatch, **values):
    """Patch ``config_helper.cfg`` to return values from *values* dict."""
    from packages.python.javdb_platform import config_helper

    def fake_cfg(name, default=""):
        return values.get(name, default)

    monkeypatch.setattr(config_helper, "cfg", fake_cfg)


def _enabled_cfg(monkeypatch, **extras):
    _patch_cfg(
        monkeypatch,
        RUNNER_REGISTRY_ENABLED="true",
        PROXY_COORDINATOR_URL="https://coord.test",
        PROXY_COORDINATOR_TOKEN="t",
        **extras,
    )


def _make_client_mock() -> MagicMock:
    """Build a MagicMock that quacks like a :class:`RunnerRegistryClient`."""
    m = MagicMock(spec=RunnerRegistryClient)
    m.register.return_value = RegisterResult(
        registered=True,
        active_runners=[],
        pool_hash_summary=[],
        server_time_ms=1,
    )
    m.heartbeat.return_value = HeartbeatResult(alive=True, server_time_ms=1)
    m.unregister.return_value = UnregisterResult(unregistered=True, server_time_ms=1)
    m.health_check.return_value = True
    return m


# ── disable paths ──────────────────────────────────────────────────────────


def test_returns_none_when_registry_disabled(monkeypatch):
    """Default OFF: no RUNNER_REGISTRY_ENABLED → no client."""
    _patch_cfg(
        monkeypatch,
        PROXY_COORDINATOR_URL="https://coord.test",
        PROXY_COORDINATOR_TOKEN="t",
    )
    assert state.setup_runner_registry_client() is None
    assert state.global_runner_registry_client is None


def test_returns_none_when_registry_explicitly_false(monkeypatch):
    _patch_cfg(
        monkeypatch,
        RUNNER_REGISTRY_ENABLED="false",
        PROXY_COORDINATOR_URL="https://coord.test",
        PROXY_COORDINATOR_TOKEN="t",
    )
    assert state.setup_runner_registry_client() is None


def test_returns_none_when_url_unset(monkeypatch):
    _patch_cfg(monkeypatch, RUNNER_REGISTRY_ENABLED="true",
               PROXY_COORDINATOR_TOKEN="t")
    assert state.setup_runner_registry_client() is None


def test_returns_none_when_token_unset(monkeypatch):
    _patch_cfg(monkeypatch, RUNNER_REGISTRY_ENABLED="true",
               PROXY_COORDINATOR_URL="https://coord.test")
    assert state.setup_runner_registry_client() is None


def test_returns_none_when_health_check_fails(monkeypatch):
    _enabled_cfg(monkeypatch)
    with patch.object(RunnerRegistryClient, "health_check", return_value=False), \
            patch.object(RunnerRegistryClient, "close") as close_mock:
        assert state.setup_runner_registry_client() is None
    close_mock.assert_called_once()


# ── happy path: register, daemon, atexit ───────────────────────────────────


def test_setup_calls_register_and_starts_heartbeat_daemon(monkeypatch):
    _enabled_cfg(monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_WORKFLOW", "DailyIngestion")

    fake_client = _make_client_mock()
    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ):
        client = state.setup_runner_registry_client()

    assert client is fake_client
    assert state.global_runner_registry_client is fake_client

    # Register was called once with GH metadata.
    fake_client.register.assert_called_once()
    kwargs = fake_client.register.call_args.kwargs
    assert kwargs["holder_id"] == state.runtime_holder_id
    assert kwargs["workflow_run_id"] == "12345"
    assert kwargs["workflow_name"] == "DailyIngestion"

    # Heartbeat daemon was spawned.
    assert state._runner_heartbeat_thread is not None
    assert state._runner_heartbeat_thread.is_alive()
    assert state._runner_heartbeat_thread.daemon is True

    # Stop the daemon cleanly so it doesn't leak past the test.
    state._runner_heartbeat_stop.set()


def test_setup_is_idempotent(monkeypatch):
    """Repeated calls return the same client without spawning duplicate daemons."""
    _enabled_cfg(monkeypatch)
    fake_client = _make_client_mock()
    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ):
        first = state.setup_runner_registry_client()
        second = state.setup_runner_registry_client()
    assert first is second
    fake_client.register.assert_called_once()
    state._runner_heartbeat_stop.set()


def test_setup_continues_on_register_unavailable(monkeypatch):
    """Register failure must NOT take down the spider — return None and log."""
    _enabled_cfg(monkeypatch)
    fake_client = _make_client_mock()
    fake_client.register.side_effect = RunnerRegistryUnavailable("upstream 5xx")
    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ):
        result = state.setup_runner_registry_client()
    # Returns None, not a half-initialised client.
    assert result is None
    assert state.global_runner_registry_client is None
    # Client.close called so the session leak is avoided.
    fake_client.close.assert_called_once()


def test_setup_continues_on_unexpected_register_exception(monkeypatch):
    _enabled_cfg(monkeypatch)
    fake_client = _make_client_mock()
    fake_client.register.side_effect = RuntimeError("unexpected")
    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ):
        result = state.setup_runner_registry_client()
    assert result is None
    fake_client.close.assert_called_once()


# ── drift detection (P3-B subsumed by P2-E) ────────────────────────────────


def test_drift_warning_emitted_when_self_is_minority(monkeypatch, caplog):
    """Hash mismatch with cohort majority → one WARNING line."""
    _enabled_cfg(monkeypatch, PROXY_POOL_JSON='{"key": "myhash"}')

    fake_client = _make_client_mock()
    fake_client.register.return_value = RegisterResult(
        registered=True,
        active_runners=[],
        pool_hash_summary=[
            PoolHashBucket(hash="majority", count=3),
            PoolHashBucket(hash="dummy_self", count=1),
        ],
        server_time_ms=1,
    )

    # Force the self-hash to match the minority bucket so the warning fires.
    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ), patch(
        "packages.python.javdb_spider.runtime.state.proxy_pool_hash",
        return_value="dummy_self",
    ), caplog.at_level(logging.WARNING, logger=state.logger.name):
        state.setup_runner_registry_client()

    drift_records = [
        r for r in caplog.records if "proxy_pool_hash drift" in r.getMessage()
    ]
    assert len(drift_records) == 1
    assert "dummy_self" in drift_records[0].getMessage()
    assert "majority=3" in drift_records[0].getMessage()
    state._runner_heartbeat_stop.set()


def test_drift_warning_quiet_when_in_majority(monkeypatch, caplog):
    """Self-hash matches majority → no drift WARNING."""
    _enabled_cfg(monkeypatch, PROXY_POOL_JSON='{"key": "myhash"}')

    fake_client = _make_client_mock()
    fake_client.register.return_value = RegisterResult(
        registered=True,
        active_runners=[],
        pool_hash_summary=[
            PoolHashBucket(hash="majority", count=3),
            PoolHashBucket(hash="other", count=1),
        ],
        server_time_ms=1,
    )

    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ), patch(
        "packages.python.javdb_spider.runtime.state.proxy_pool_hash",
        return_value="majority",
    ), caplog.at_level(logging.WARNING, logger=state.logger.name):
        state.setup_runner_registry_client()

    drift_records = [
        r for r in caplog.records if "proxy_pool_hash drift" in r.getMessage()
    ]
    assert drift_records == []
    state._runner_heartbeat_stop.set()


def test_drift_warning_quiet_when_only_self_in_summary(monkeypatch, caplog):
    """Single-runner deployment must NOT log drift against itself."""
    _enabled_cfg(monkeypatch)

    fake_client = _make_client_mock()
    fake_client.register.return_value = RegisterResult(
        registered=True,
        active_runners=[],
        pool_hash_summary=[
            PoolHashBucket(hash="solo", count=1),
        ],
        server_time_ms=1,
    )

    with patch(
        "packages.python.javdb_spider.runtime.state.create_runner_registry_client_from_env",
        return_value=fake_client,
    ), patch(
        "packages.python.javdb_spider.runtime.state.proxy_pool_hash",
        return_value="solo",
    ), caplog.at_level(logging.WARNING, logger=state.logger.name):
        state.setup_runner_registry_client()

    drift_records = [
        r for r in caplog.records if "proxy_pool_hash drift" in r.getMessage()
    ]
    assert drift_records == []
    state._runner_heartbeat_stop.set()


# ── atexit / unregister ────────────────────────────────────────────────────


def test_unregister_at_exit_calls_unregister_and_close(monkeypatch):
    fake_client = _make_client_mock()
    monkeypatch.setattr(state, "global_runner_registry_client", fake_client)
    monkeypatch.setattr(state, "_runner_unregistered", False)

    state._unregister_runner_at_exit()

    fake_client.unregister.assert_called_once_with(state.runtime_holder_id)
    fake_client.close.assert_called_once()
    assert state._runner_unregistered is True
    assert state.global_runner_registry_client is None


def test_unregister_at_exit_is_idempotent(monkeypatch):
    """Two calls (atexit + signal handler) → only one unregister."""
    fake_client = _make_client_mock()
    monkeypatch.setattr(state, "global_runner_registry_client", fake_client)
    monkeypatch.setattr(state, "_runner_unregistered", False)

    state._unregister_runner_at_exit()
    state._unregister_runner_at_exit()

    fake_client.unregister.assert_called_once()
    fake_client.close.assert_called_once()


def test_unregister_at_exit_swallows_unavailable(monkeypatch):
    fake_client = _make_client_mock()
    fake_client.unregister.side_effect = RunnerRegistryUnavailable("network")
    monkeypatch.setattr(state, "global_runner_registry_client", fake_client)
    monkeypatch.setattr(state, "_runner_unregistered", False)

    # Must not raise.
    state._unregister_runner_at_exit()
    fake_client.close.assert_called_once()


def test_unregister_at_exit_swallows_unexpected_exception(monkeypatch):
    fake_client = _make_client_mock()
    fake_client.unregister.side_effect = RuntimeError("boom")
    monkeypatch.setattr(state, "global_runner_registry_client", fake_client)
    monkeypatch.setattr(state, "_runner_unregistered", False)

    state._unregister_runner_at_exit()
    fake_client.close.assert_called_once()


def test_unregister_at_exit_skips_when_no_client(monkeypatch):
    monkeypatch.setattr(state, "global_runner_registry_client", None)
    monkeypatch.setattr(state, "_runner_unregistered", False)
    # Must not crash even with no client configured.
    state._unregister_runner_at_exit()
    # _runner_unregistered stays False because no work was done; the
    # function early-returns rather than flagging itself complete.
    assert state._runner_unregistered is False


# ── heartbeat daemon — lifecycle & error tolerance ─────────────────────────


def test_heartbeat_loop_exits_promptly_on_stop_event(monkeypatch):
    """The daemon must observe ``_runner_heartbeat_stop`` and exit quickly."""
    fake_client = _make_client_mock()
    state._runner_heartbeat_stop.clear()

    thread = threading.Thread(
        target=state._runner_heartbeat_loop,
        args=(fake_client, "h1"),
        daemon=True,
    )
    thread.start()
    state._runner_heartbeat_stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_heartbeat_loop_re_registers_on_alive_false(monkeypatch):
    """alive=False must trigger a re-register (registry GC'd us)."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.return_value = HeartbeatResult(
        alive=False, server_time_ms=1,
    )

    # Speed the loop up so we don't wait the real 60 s; use a 50 ms tick.
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    state._runner_heartbeat_stop.clear()

    thread = threading.Thread(
        target=state._runner_heartbeat_loop,
        args=(fake_client, "h1"),
        daemon=True,
    )
    thread.start()
    # Wait until at least one heartbeat + re-register fires.
    deadline = time.monotonic() + 2.0
    while (
        fake_client.register.call_count == 0
        and time.monotonic() < deadline
    ):
        time.sleep(0.02)
    state._runner_heartbeat_stop.set()
    thread.join(timeout=2)
    assert fake_client.heartbeat.called
    assert fake_client.register.called  # re-registered after eviction


def test_heartbeat_loop_swallows_unavailable_and_keeps_running(monkeypatch):
    """A transient outage must NOT kill the daemon."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.side_effect = RunnerRegistryUnavailable("net blip")

    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    state._runner_heartbeat_stop.clear()

    thread = threading.Thread(
        target=state._runner_heartbeat_loop,
        args=(fake_client, "h1"),
        daemon=True,
    )
    thread.start()
    # Let several ticks pass — the daemon must keep retrying despite the
    # exception on every call.
    deadline = time.monotonic() + 1.0
    while fake_client.heartbeat.call_count < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    state._runner_heartbeat_stop.set()
    thread.join(timeout=2)
    assert fake_client.heartbeat.call_count >= 2
    # ``register`` was NOT called — alive=False was never returned because
    # heartbeat raised before yielding a result.
    fake_client.register.assert_not_called()
