from __future__ import annotations

import threading
import time

import pytest

import javdb.storage.db._db_connection as db_conn


def test_startup_replay_runs_once_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setenv("D1_STARTUP_REPLAY_ENABLED", "1")
    monkeypatch.setattr(db_conn, "_startup_recovery_drain", lambda: calls.append("drain"))
    monkeypatch.setattr(db_conn, "_startup_recovery_drained", False)

    db_conn._maybe_startup_recovery_drain()
    db_conn._maybe_startup_recovery_drain()

    assert calls == ["drain"]


def test_startup_replay_skips_when_disabled(monkeypatch):
    calls = []
    monkeypatch.delenv("D1_STARTUP_REPLAY_ENABLED", raising=False)
    monkeypatch.setattr(db_conn, "_startup_recovery_drain", lambda: calls.append("drain"))
    monkeypatch.setattr(db_conn, "_startup_recovery_drained", False)

    db_conn._maybe_startup_recovery_drain()

    assert calls == []


def test_startup_replay_failure_counts_as_process_attempt(monkeypatch):
    calls = []
    monkeypatch.setenv("D1_STARTUP_REPLAY_ENABLED", "1")

    def fail():
        calls.append("drain")
        raise RuntimeError("temporary")

    monkeypatch.setattr(db_conn, "_startup_recovery_drain", fail)
    monkeypatch.setattr(db_conn, "_startup_recovery_drained", False)

    with pytest.raises(RuntimeError, match="temporary"):
        db_conn._maybe_startup_recovery_drain()

    db_conn._maybe_startup_recovery_drain()

    assert calls == ["drain"]


def test_startup_replay_concurrent_call_runs_once(monkeypatch):
    calls = []
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setenv("D1_STARTUP_REPLAY_ENABLED", "1")

    def drain():
        calls.append("drain")
        started.set()
        release.wait(timeout=1)

    monkeypatch.setattr(db_conn, "_startup_recovery_drain", drain)
    monkeypatch.setattr(db_conn, "_startup_recovery_drained", False)

    first = threading.Thread(target=db_conn._maybe_startup_recovery_drain)
    second = threading.Thread(target=db_conn._maybe_startup_recovery_drain)
    first.start()
    assert started.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert calls == ["drain"]


def test_startup_recovery_drain_uses_configured_bounds(monkeypatch):
    import javdb.storage.d1_client as d1_client
    import javdb.storage.d1_recovery as d1_recovery

    calls = []
    monkeypatch.setenv("D1_STARTUP_REPLAY_MAX_ORDERING_KEYS", "2")
    monkeypatch.setenv("D1_STARTUP_REPLAY_MAX_EVENTS_PER_KEY", "5")

    monkeypatch.setattr(
        d1_recovery,
        "startup_drain",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(
        d1_client,
        "make_d1_connection",
        lambda logical_db: object(),
    )

    db_conn._startup_recovery_drain()

    assert calls == [
        {
            "connection_factory": d1_client.make_d1_connection,
            "max_ordering_keys": 2,
            "max_events_per_key": 5,
        }
    ]
