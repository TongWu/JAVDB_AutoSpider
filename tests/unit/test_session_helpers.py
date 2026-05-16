"""Pin down the shared scaffolding extracted from ``apps.cli.rollback``
and ``apps.cli.commit_session`` into ``apps.cli._session_helpers``.

The headline regression these tests guard against is the
``normalize_run_started_at`` divergence — see the module's history
note. Both CLIs previously kept their own copy; the rollback version
used ``datetime.fromisoformat`` (correct UTC conversion for non-UTC
offsets), and the commit version did ad-hoc string slicing that left
non-UTC inputs at their local wall-clock value. Production was safe
because GitHub Actions only emits ``Z`` suffixes, but the latent bug
shipped for months.

If a future refactor reintroduces the slicing form, the
``test_normalize_converts_*_offset_to_utc`` cases below fail.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from apps.cli import _session_helpers as helpers


# ── normalize_run_started_at ───────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", None),
        (None, None),
        ("   ", None),
        ("not-a-time", None),
        ("2026-05-04T19:30:00Z", "2026-05-04 19:30:00"),
        ("2026-05-04T19:30:00+00:00", "2026-05-04 19:30:00"),
        # Fractional seconds: dropped after normalisation (strftime
        # template is to-the-second).
        ("2026-05-04T19:30:00.123456Z", "2026-05-04 19:30:00"),
    ],
)
def test_normalize_run_started_at_basic_cases(raw, expected):
    assert helpers.normalize_run_started_at(raw) == expected


def test_normalize_converts_positive_offset_to_utc():
    """``+08:00`` (SGT) input must shift backwards to naive UTC.

    This is the bug the old ``commit_session`` slicing version had —
    it stripped the ``+08:00`` without adjusting the time and so a
    19:30 SGT timestamp came out as ``2026-05-04 19:30:00`` (wrong)
    instead of ``2026-05-04 11:30:00`` (correct UTC).
    """
    assert (
        helpers.normalize_run_started_at("2026-05-04T19:30:00+08:00")
        == "2026-05-04 11:30:00"
    )


def test_normalize_converts_negative_offset_to_utc():
    """``-04:00`` (EDT) input must shift forwards to naive UTC."""
    assert (
        helpers.normalize_run_started_at("2026-05-04T19:30:00-04:00")
        == "2026-05-04 23:30:00"
    )


# ── append_jsonl_record ────────────────────────────────────────────────


def test_append_jsonl_record_creates_directory_and_writes_one_line(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    helpers.append_jsonl_record({"a": 1, "b": "x"})
    helpers.append_jsonl_record({"a": 2})

    path = tmp_path / "D1" / "d1_drift.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines == [{"a": 1, "b": "x"}, {"a": 2}]


def test_append_jsonl_record_honours_explicit_reports_dir(tmp_path):
    helpers.append_jsonl_record(
        {"k": "v"}, reports_dir=str(tmp_path), filename="custom.jsonl",
    )
    path = tmp_path / "D1" / "custom.jsonl"
    assert path.exists()
    assert json.loads(path.read_text().strip()) == {"k": "v"}


def test_append_jsonl_record_swallows_exceptions(monkeypatch):
    """Best-effort: a write failure must NOT raise (callers rely on
    metric emission never blocking the primary operation)."""
    def boom(*_a, **_kw):
        raise OSError("simulated FS failure")

    monkeypatch.setattr(helpers.os, "makedirs", boom)
    # Should NOT raise.
    helpers.append_jsonl_record({"k": "v"})


# ── write_github_output ────────────────────────────────────────────────


def test_write_github_output_writes_kv_lines(tmp_path, monkeypatch):
    target = tmp_path / "gh_out"
    target.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))

    helpers.write_github_output(foo=1, bar="hello")

    lines = target.read_text().splitlines()
    assert lines == ["foo=1", "bar=hello"]


def test_write_github_output_silent_when_env_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    # Should be a silent no-op — no exception, no side effect we can
    # observe here (the test passes if it doesn't raise).
    helpers.write_github_output(foo=1)


# ── attach_run_identity ────────────────────────────────────────────────


def test_attach_run_identity_populates_when_lookup_succeeds(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "db_get_session_run_identity",
        lambda sid: ("run-123", 4),
    )
    record: Dict[str, Any] = {"kind": "x"}
    helpers.attach_run_identity(record, "sess-1")
    assert record == {"kind": "x", "run_id": "run-123", "run_attempt": 4}


def test_attach_run_identity_noop_when_lookup_returns_none(monkeypatch):
    monkeypatch.setattr(
        helpers, "db_get_session_run_identity", lambda sid: None,
    )
    record: Dict[str, Any] = {"kind": "x"}
    helpers.attach_run_identity(record, "sess-1")
    assert record == {"kind": "x"}


def test_attach_run_identity_swallows_lookup_failure(monkeypatch):
    def boom(_sid):
        raise RuntimeError("DB hiccup")

    monkeypatch.setattr(
        helpers, "db_get_session_run_identity", boom,
    )
    record: Dict[str, Any] = {"kind": "x"}
    # Must NOT raise — the docstring says best-effort.
    helpers.attach_run_identity(record, "sess-1")
    assert record == {"kind": "x"}


# ── read_session_pre_state ─────────────────────────────────────────────


def test_read_session_pre_state_returns_dataclass(monkeypatch):
    monkeypatch.setattr(
        helpers, "db_get_session_status", lambda sid: ("pending", "in_progress"),
    )
    state = helpers.read_session_pre_state("sess-1")
    assert state.write_mode == "pending"
    assert state.status == "in_progress"


def test_read_session_pre_state_falls_back_to_audit_on_missing(monkeypatch):
    monkeypatch.setattr(
        helpers, "db_get_session_status", lambda sid: None,
    )
    state = helpers.read_session_pre_state("sess-x")
    assert state.write_mode == "audit"
    assert state.status is None


def test_read_session_pre_state_falls_back_to_audit_on_error(monkeypatch):
    def boom(_sid):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(helpers, "db_get_session_status", boom)
    state = helpers.read_session_pre_state("sess-x")
    assert state.write_mode == "audit"
    assert state.status is None


# ── find_run_sessions / find_window_sessions ───────────────────────────


def test_find_run_sessions_returns_empty_on_lookup_error(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("oops")

    monkeypatch.setattr(helpers, "db_find_sessions_by_run", boom)
    assert helpers.find_run_sessions("r-1", 2) == []


def test_find_run_sessions_passes_through(monkeypatch):
    captured = {}

    def fake(run_id, attempt):
        captured["run_id"] = run_id
        captured["attempt"] = attempt
        return ["s1", "s2"]

    monkeypatch.setattr(helpers, "db_find_sessions_by_run", fake)
    assert helpers.find_run_sessions("r-1", 2) == ["s1", "s2"]
    assert captured == {"run_id": "r-1", "attempt": 2}


def test_find_window_sessions_returns_empty_for_falsy_since():
    assert helpers.find_window_sessions(None) == []
    assert helpers.find_window_sessions("") == []


def test_find_window_sessions_passes_since_through(monkeypatch):
    captured = {}

    def fake(*, since, **_kw):
        captured["since"] = since
        return ["a", "b"]

    monkeypatch.setattr(helpers, "db_find_in_progress_sessions", fake)
    assert helpers.find_window_sessions("2026-05-04 00:00:00") == ["a", "b"]
    assert captured == {"since": "2026-05-04 00:00:00"}


def test_find_window_sessions_returns_empty_on_lookup_error(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("DB down")

    monkeypatch.setattr(helpers, "db_find_in_progress_sessions", boom)
    assert helpers.find_window_sessions("2026-05-04 00:00:00") == []


# ── fanout_movie_claim ─────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeClient:
    def __init__(self, *, behaviour=None):
        # behaviour: dict mapping op_name → function(session_id, date) → result-or-raises
        self.behaviour = behaviour or {}
        self.closed = False

    def rollback_staged_movies(self, session_id, date):
        return self._dispatch("rollback_staged_movies", session_id, date)

    def commit_completed_movies(self, session_id, date):
        return self._dispatch("commit_completed_movies", session_id, date)

    def _dispatch(self, op, session_id, date):
        action = self.behaviour.get(op)
        if action is None:
            raise AssertionError(
                f"unexpected call to {op}({session_id}, {date})"
            )
        return action(session_id, date)

    def close(self):
        self.closed = True


@pytest.fixture
def patch_claim_client(monkeypatch):
    """Return a helper that installs a configured _FakeClient."""

    def _install(client_or_factory):
        if callable(client_or_factory):
            monkeypatch.setattr(
                helpers, "create_movie_claim_client_from_env",
                client_or_factory,
            )
        else:
            monkeypatch.setattr(
                helpers, "create_movie_claim_client_from_env",
                lambda: client_or_factory,
            )
        monkeypatch.setattr(
            helpers, "current_shard_date", lambda: "2026-05-04",
        )

    return _install


def test_fanout_movie_claim_skips_when_client_unconfigured(
    patch_claim_client,
):
    patch_claim_client(lambda: None)
    assert helpers.fanout_movie_claim(
        ["s1"], operation="rollback",
    ) == []


def test_fanout_movie_claim_rejects_unknown_operation():
    with pytest.raises(ValueError, match="unknown operation"):
        helpers.fanout_movie_claim(
            ["s1"], operation="banana",
        )


def test_fanout_movie_claim_rollback_returns_removed_count(patch_claim_client):
    client = _FakeClient(behaviour={
        "rollback_staged_movies": lambda sid, date: _FakeResult(removed=42),
    })
    patch_claim_client(client)
    out = helpers.fanout_movie_claim(
        ["s1"], operation="rollback", shard_date="2026-05-04",
    )
    assert out == [{
        "session_id": "s1",
        "shard_date": "2026-05-04",
        "removed": 42,
        "ok": True,
    }]
    assert client.closed is True


def test_fanout_movie_claim_commit_returns_promoted_count(patch_claim_client):
    client = _FakeClient(behaviour={
        "commit_completed_movies": lambda sid, date: _FakeResult(promoted=7),
    })
    patch_claim_client(client)
    out = helpers.fanout_movie_claim(
        ["s1"], operation="commit",
    )
    assert out == [{
        "session_id": "s1",
        "shard_date": "2026-05-04",
        "promoted": 7,
        "ok": True,
    }]


def test_fanout_movie_claim_records_failure_after_exhausting_retries(
    patch_claim_client,
):
    calls = []

    def always_fail(sid, date):
        calls.append((sid, date))
        from packages.python.javdb_platform.movie_claim_client import (
            MovieClaimUnavailable,
        )
        raise MovieClaimUnavailable("transient")

    client = _FakeClient(behaviour={
        "rollback_staged_movies": always_fail,
    })
    patch_claim_client(client)
    out = helpers.fanout_movie_claim(
        ["sX"], operation="rollback", max_attempts=2,
    )
    assert len(calls) == 2  # tried twice
    assert out[0]["ok"] is False
    assert out[0]["removed"] == 0
    assert out[0]["error"] == "transient"
    assert out[0]["attempts"] == 2


def test_fanout_movie_claim_commit_does_not_retry(patch_claim_client):
    calls = []

    def fail_once(sid, date):
        calls.append((sid, date))
        from packages.python.javdb_platform.movie_claim_client import (
            MovieClaimUnavailable,
        )
        raise MovieClaimUnavailable("nope")

    client = _FakeClient(behaviour={
        "commit_completed_movies": fail_once,
    })
    patch_claim_client(client)
    # commit defaults to max_attempts=1.
    out = helpers.fanout_movie_claim(
        ["sY"], operation="commit",
    )
    assert len(calls) == 1
    assert out[0]["ok"] is False
    # Single-attempt failures omit the redundant attempts key.
    assert "attempts" not in out[0]


def test_fanout_movie_claim_empty_session_list_skips_client_creation(
    monkeypatch,
):
    monkeypatch.setattr(
        helpers, "create_movie_claim_client_from_env",
        lambda: pytest.fail("client must not be created for empty list"),
    )
    assert helpers.fanout_movie_claim([], operation="rollback") == []
