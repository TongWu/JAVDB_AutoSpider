"""Tests for :class:`RunnerRegistryClient` (P2-E).

Mirror the structure of ``test_movie_claim_client.py``: keep the network
mocked at the ``requests.Session`` boundary so the suite is deterministic
and runs offline, and assert that every failure path collapses into
:class:`RunnerRegistryUnavailable` (the fail-open contract documented in
``runner_registry_client.py``), while local argument errors raise
:class:`ValueError` before any HTTP call.

The "three-piece" fail-open harness (``fail-open-test-harness`` todo)
is the union of:

1. **DO normal** — happy paths in this file.
2. **DO unreachable** — ``test_*_collapses_*_into_unavailable`` cases.
3. **DO not configured** — ``test_factory_*`` cases against
   :func:`create_runner_registry_client_from_env`.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.runner_registry_client import (  # noqa: E402
    ActiveRunnersResult,
    HeartbeatResult,
    PoolHashBucket,
    RegisterResult,
    RunnerInfo,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    UnregisterResult,
    create_runner_registry_client_from_env,
    proxy_pool_hash,
    _extract_server_time_ms,
    _parse_runner_info,
    _parse_hash_summary,
)


def _make_client() -> RunnerRegistryClient:
    """Build a client without any network I/O during construction."""
    return RunnerRegistryClient(
        base_url="https://coord.example.test",
        token="dummy",
    )


def _mock_response(status: int = 200, json_body=None, text: str = "") -> MagicMock:
    """Build a minimal ``requests.Response``-shaped MagicMock."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = text
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no JSON")
    return resp


# ── proxy_pool_hash ─────────────────────────────────────────────────────────


def test_proxy_pool_hash_returns_16_hex_chars_for_valid_json():
    h = proxy_pool_hash('[{"name":"p1","http":"http://a"}]')
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_proxy_pool_hash_is_canonical_across_key_order_and_whitespace():
    """Cosmetic JSON differences must NOT register as drift.

    Two semantically identical pool configs (different key order +
    pretty-printed whitespace) must hash to the same value, otherwise
    every workflow re-deploy would falsely warn about peers it agrees
    with.
    """
    a = '[{"name":"p1","http":"x"}]'
    b = '[\n  {\n    "http": "x",\n    "name": "p1"\n  }\n]'
    assert proxy_pool_hash(a) == proxy_pool_hash(b)


def test_proxy_pool_hash_returns_empty_for_empty_input():
    assert proxy_pool_hash("") == ""
    assert proxy_pool_hash("   ") == ""


def test_proxy_pool_hash_handles_invalid_json_without_raising():
    """Invalid JSON must still return a non-empty hash so drift IS visible.

    The contract says "still hash the raw bytes so a mismatch is at
    least detectable" — silently returning ``""`` would mask drift.
    """
    h = proxy_pool_hash("not valid json {{{")
    assert isinstance(h, str)
    assert len(h) == 16  # falls back to raw-bytes hash


# ── construction ────────────────────────────────────────────────────────────


def test_init_rejects_empty_base_url():
    with pytest.raises(ValueError, match="base_url"):
        RunnerRegistryClient(base_url="", token="t")


def test_init_rejects_empty_token():
    with pytest.raises(ValueError, match="token"):
        RunnerRegistryClient(base_url="https://coord.test", token="")


def test_init_strips_trailing_slash_from_base_url():
    c = RunnerRegistryClient(base_url="https://coord.test/", token="t")
    assert c.base_url == "https://coord.test"


def test_init_sets_bearer_header():
    c = _make_client()
    assert c._session.headers["Authorization"] == "Bearer dummy"


# ── register — happy paths & body shape ─────────────────────────────────────


def test_register_sends_full_body_with_optional_fields():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update({"url": url, "body": json})
        return _mock_response(200, {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 1234,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            r = c.register(
                holder_id="runner-A",
                workflow_run_id="123",
                workflow_name="DailyIngestion",
                started_at=1000,
                proxy_pool_hash="abc123",
                page_range="1-50",
            )
        assert isinstance(r, RegisterResult)
        assert r.registered is True
        assert r.active_runners == []
        assert r.pool_hash_summary == []
        assert r.server_time_ms == 1234
        assert captured["url"] == "https://coord.example.test/register"
        assert captured["body"]["holder_id"] == "runner-A"
        assert captured["body"]["workflow_run_id"] == "123"
        assert captured["body"]["workflow_name"] == "DailyIngestion"
        assert captured["body"]["started_at"] == 1000
        assert captured["body"]["proxy_pool_hash"] == "abc123"
        assert captured["body"]["page_range"] == "1-50"
    finally:
        c.close()


def test_register_omits_started_at_when_none():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update(json)
        return _mock_response(200, {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.register(holder_id="r1")
        assert "started_at" not in captured
    finally:
        c.close()


@pytest.mark.parametrize("holder_id", ["", None])
def test_register_validates_empty_holder_id(holder_id):
    c = _make_client()
    try:
        with pytest.raises(ValueError, match="holder_id"):
            c.register(holder_id=holder_id)
    finally:
        c.close()


def test_register_parses_active_runners_list():
    c = _make_client()
    body = {
        "registered": True,
        "active_runners": [
            {
                "holder_id": "runner-A",
                "workflow_run_id": "1",
                "workflow_name": "Daily",
                "started_at": 100,
                "last_heartbeat": 200,
                "proxy_pool_hash": "h1",
                "page_range": "1-10",
            },
            {
                "holder_id": "runner-B",
                "workflow_run_id": "",
                "workflow_name": "",
                "started_at": 110,
                "last_heartbeat": 210,
                "proxy_pool_hash": "h1",
                "page_range": None,
            },
        ],
        "pool_hash_summary": [{"hash": "h1", "count": 2}],
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.register(holder_id="caller", proxy_pool_hash="h1")
        assert len(r.active_runners) == 2
        assert r.active_runners[0].holder_id == "runner-A"
        assert r.active_runners[0].page_range == "1-10"
        assert r.active_runners[1].page_range is None
        assert r.pool_hash_summary == [PoolHashBucket(hash="h1", count=2)]
    finally:
        c.close()


def test_register_recognises_re_register_as_idempotent():
    """``registered=False`` from the Worker must be passed through verbatim."""
    c = _make_client()
    body = {
        "registered": False,
        "active_runners": [],
        "pool_hash_summary": [],
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.register(holder_id="dup")
        assert r.registered is False
    finally:
        c.close()


def test_register_treats_string_booleans_as_false():
    """Worker contract booleans must be real JSON booleans, not truthy strings."""
    c = _make_client()
    body = {
        "registered": "false",
        "movie_claim_recommended": "true",
        "active_runners": [],
        "pool_hash_summary": [],
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.register(holder_id="runner")
        assert r.registered is False
        assert r.movie_claim_recommended is False
    finally:
        c.close()


# ── heartbeat / unregister — body + parsing ─────────────────────────────────


def test_heartbeat_sends_correct_body():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update(json)
        return _mock_response(200, {"alive": True, "server_time": 1})

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            r = c.heartbeat("runner-X")
        assert isinstance(r, HeartbeatResult)
        assert r.alive is True
        assert captured == {"holder_id": "runner-X"}
    finally:
        c.close()


def test_heartbeat_alive_false_for_evicted_holder():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {"alive": False, "server_time": 1}),
        ):
            r = c.heartbeat("evicted")
        assert r.alive is False
    finally:
        c.close()


def test_heartbeat_treats_string_booleans_as_false():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {
                "alive": "true",
                "movie_claim_recommended": "true",
                "server_time": 1,
            }),
        ):
            r = c.heartbeat("runner")
        assert r.alive is False
        assert r.movie_claim_recommended is False
    finally:
        c.close()


@pytest.mark.parametrize("holder_id", ["", None])
def test_heartbeat_validates_empty_holder_id(holder_id):
    c = _make_client()
    try:
        with pytest.raises(ValueError, match="holder_id"):
            c.heartbeat(holder_id)
    finally:
        c.close()


def test_unregister_sends_correct_body():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update(json)
        return _mock_response(200, {"unregistered": True, "server_time": 1})

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            r = c.unregister("runner-Y")
        assert isinstance(r, UnregisterResult)
        assert r.unregistered is True
        assert captured == {"holder_id": "runner-Y"}
    finally:
        c.close()


def test_unregister_unknown_holder_returns_false():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {"unregistered": False, "server_time": 1}),
        ):
            r = c.unregister("unknown")
        assert r.unregistered is False
    finally:
        c.close()


def test_unregister_treats_string_boolean_as_false():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {
                "unregistered": "true",
                "server_time": 1,
            }),
        ):
            r = c.unregister("runner")
        assert r.unregistered is False
    finally:
        c.close()


@pytest.mark.parametrize("holder_id", ["", None])
def test_unregister_validates_empty_holder_id(holder_id):
    c = _make_client()
    try:
        with pytest.raises(ValueError, match="holder_id"):
            c.unregister(holder_id)
    finally:
        c.close()


# ── get_active_runners — read-only snapshot ────────────────────────────────


def test_get_active_runners_uses_get_request_no_body():
    c = _make_client()
    captured: dict = {}

    def fake_get(url, timeout):
        captured["url"] = url
        return _mock_response(200, {
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 9,
        })

    try:
        with patch.object(c._session, "get", side_effect=fake_get):
            r = c.get_active_runners()
        assert isinstance(r, ActiveRunnersResult)
        assert captured["url"] == "https://coord.example.test/active_runners"
        assert r.server_time_ms == 9
    finally:
        c.close()


def test_get_active_runners_orders_buckets_by_count_desc():
    """The Worker already orders by count desc; client just preserves order."""
    c = _make_client()
    body = {
        "active_runners": [],
        "pool_hash_summary": [
            {"hash": "majority", "count": 3},
            {"hash": "drift", "count": 1},
        ],
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.get_active_runners()
        assert [b.hash for b in r.pool_hash_summary] == ["majority", "drift"]
        assert [b.count for b in r.pool_hash_summary] == [3, 1]
    finally:
        c.close()


# ── failure modes — DO unreachable / 5xx / malformed JSON ─────────────────


def test_register_collapses_timeout_into_unavailable():
    c = _make_client()
    try:
        with patch.object(c._session, "post", side_effect=requests.Timeout("slow")):
            with pytest.raises(RunnerRegistryUnavailable, match="network error"):
                c.register(holder_id="x")
    finally:
        c.close()


def test_register_collapses_connection_error_into_unavailable():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            side_effect=requests.ConnectionError("refused"),
        ):
            with pytest.raises(RunnerRegistryUnavailable, match="network error"):
                c.register(holder_id="x")
    finally:
        c.close()


def test_register_collapses_503_binding_missing_into_unavailable():
    """503 = ``RUNNER_REGISTRY_DO`` binding missing → v3 migration not applied."""
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(
                503,
                json_body={"error": "runner_registry binding not configured"},
                text='{"error":"runner_registry binding not configured"}',
            ),
        ):
            with pytest.raises(RunnerRegistryUnavailable, match="HTTP 503"):
                c.register(holder_id="x")
    finally:
        c.close()


def test_register_collapses_malformed_json_into_unavailable():
    c = _make_client()
    bad = MagicMock(spec=requests.Response)
    bad.status_code = 200
    bad.text = "not json"
    bad.json.side_effect = ValueError("boom")
    try:
        with patch.object(c._session, "post", return_value=bad):
            with pytest.raises(RunnerRegistryUnavailable, match="invalid JSON"):
                c.register(holder_id="x")
    finally:
        c.close()


def test_register_collapses_non_object_json_into_unavailable():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, ["not", "an", "object"]),
        ):
            with pytest.raises(
                RunnerRegistryUnavailable,
                match="invalid JSON: expected object, got list",
            ):
                c.register(holder_id="x")
    finally:
        c.close()


def test_register_missing_registered_field_defaults_false():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {"server_time": 1}),
        ):
            r = c.register(holder_id="x")
        assert r.registered is False
    finally:
        c.close()


def test_register_collapses_malformed_active_runner_into_unavailable():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(
                200,
                {
                    "registered": True,
                    "active_runners": ["not a dict"],
                    "pool_hash_summary": [],
                    "server_time": 1,
                },
            ),
        ):
            with pytest.raises(RunnerRegistryUnavailable, match="malformed register"):
                c.register(holder_id="x")
    finally:
        c.close()


# ── server_time wire-key fallback ──────────────────────────────────────────


def test_extract_server_time_ms_prefers_explicit_ms_key():
    assert _extract_server_time_ms({"server_time_ms": 7, "server_time": 9}) == 7


def test_extract_server_time_ms_falls_back_to_server_time():
    assert _extract_server_time_ms({"server_time": 9}) == 9


def test_extract_server_time_ms_returns_zero_when_neither_present():
    assert _extract_server_time_ms({}) == 0


# ── parsing helpers — defensive against malformed Worker responses ─────────


def test_parse_runner_info_coerces_missing_fields_to_safe_defaults():
    info = _parse_runner_info({})
    assert info.holder_id == ""
    assert info.workflow_run_id == ""
    assert info.workflow_name == ""
    assert info.started_at == 0
    assert info.last_heartbeat == 0
    assert info.proxy_pool_hash == ""
    assert info.page_range is None


def test_parse_runner_info_rejects_malformed_entry():
    with pytest.raises(ValueError, match="runner entry"):
        _parse_runner_info("not a dict")


def test_parse_runner_info_rejects_non_string_page_range():
    with pytest.raises(ValueError, match="page_range"):
        _parse_runner_info({"page_range": ["1-10"]})


# ── movie_claim_recommended forward-compat parsing ─────────────────────────


def test_register_parses_movie_claim_recommended_when_present():
    """New Worker → fields populated → parser surfaces them verbatim."""
    c = _make_client()
    body = {
        "registered": True,
        "active_runners": [],
        "pool_hash_summary": [],
        "server_time_ms": 1,
        "movie_claim_recommended": True,
        "movie_claim_min_runners": 2,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.register(holder_id="x")
        assert r.movie_claim_recommended is True
        assert r.movie_claim_min_runners == 2
    finally:
        c.close()


def test_register_defaults_movie_claim_fields_when_missing():
    """Old Worker (pre-auto-toggle) omits the fields → parser defaults to
    False / 0 → ``state._apply_movie_claim_recommendation`` interprets
    that as "single runner, do not mount" (the safe default)."""
    c = _make_client()
    body = {
        "registered": True,
        "active_runners": [],
        "pool_hash_summary": [],
        "server_time_ms": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.register(holder_id="x")
        assert r.movie_claim_recommended is False
        assert r.movie_claim_min_runners == 0
    finally:
        c.close()


def test_register_movie_claim_min_runners_handles_string_and_zero():
    """Coercion mirrors the rest of the parser: ``None`` / 0 → 0,
    a numeric string parses cleanly."""
    c = _make_client()
    body_zero = {
        "registered": True,
        "active_runners": [],
        "pool_hash_summary": [],
        "server_time_ms": 1,
        "movie_claim_recommended": False,
        "movie_claim_min_runners": 0,
    }
    body_none = {
        "registered": True,
        "active_runners": [],
        "pool_hash_summary": [],
        "server_time_ms": 1,
        "movie_claim_recommended": False,
        "movie_claim_min_runners": None,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body_zero)):
            r = c.register(holder_id="x")
        assert r.movie_claim_min_runners == 0
        with patch.object(c._session, "post", return_value=_mock_response(200, body_none)):
            r = c.register(holder_id="x")
        assert r.movie_claim_min_runners == 0
    finally:
        c.close()


def test_heartbeat_parses_movie_claim_recommended_when_present():
    c = _make_client()
    body = {
        "alive": True,
        "server_time_ms": 1,
        "movie_claim_recommended": True,
        "movie_claim_min_runners": 2,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.heartbeat("x")
        assert r.alive is True
        assert r.movie_claim_recommended is True
        assert r.movie_claim_min_runners == 2
    finally:
        c.close()


def test_heartbeat_defaults_movie_claim_fields_when_missing():
    """Old Worker → missing fields → safe defaults preserve fail-open."""
    c = _make_client()
    body = {"alive": True, "server_time_ms": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.heartbeat("x")
        assert r.alive is True
        assert r.movie_claim_recommended is False
        assert r.movie_claim_min_runners == 0
    finally:
        c.close()


def test_heartbeat_alive_false_still_parses_recommendation():
    """An evicted holder still surfaces the cohort recommendation
    because the Worker computes it from the pruned cohort."""
    c = _make_client()
    body = {
        "alive": False,
        "server_time_ms": 1,
        "movie_claim_recommended": True,
        "movie_claim_min_runners": 2,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.heartbeat("evicted")
        assert r.alive is False
        assert r.movie_claim_recommended is True
        assert r.movie_claim_min_runners == 2
    finally:
        c.close()


def test_parse_hash_summary_rejects_malformed_buckets():
    with pytest.raises(ValueError, match="entries"):
        _parse_hash_summary([
            {"hash": "h1", "count": 3},
            "not a dict",
        ])
    with pytest.raises(ValueError):
        _parse_hash_summary([
            {"hash": "h2", "count": "not a number"},
        ])


# ── health_check ────────────────────────────────────────────────────────────


def test_health_check_returns_true_on_200():
    c = _make_client()
    try:
        with patch.object(
            c._session, "get",
            return_value=_mock_response(200, {"ok": True}),
        ):
            assert c.health_check() is True
    finally:
        c.close()


def test_health_check_returns_false_on_non_200():
    c = _make_client()
    try:
        with patch.object(c._session, "get", return_value=_mock_response(503)):
            assert c.health_check() is False
    finally:
        c.close()


def test_health_check_swallows_exceptions():
    c = _make_client()
    try:
        with patch.object(
            c._session, "get",
            side_effect=requests.ConnectionError("boom"),
        ):
            assert c.health_check() is False
    finally:
        c.close()


# ── factory: env-var disable / unconfigured / health failure ───────────────


def test_factory_returns_none_when_disabled(monkeypatch):
    """Default OFF: returns None unless RUNNER_REGISTRY_ENABLED is truthy."""
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.delenv("RUNNER_REGISTRY_ENABLED", raising=False)
    assert create_runner_registry_client_from_env() is None


def test_factory_returns_none_when_explicitly_false(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.setenv("RUNNER_REGISTRY_ENABLED", "false")
    assert create_runner_registry_client_from_env() is None


@pytest.mark.parametrize("enabled", ["1", "true", "yes", "TRUE", "Yes"])
def test_factory_accepts_truthy_enable_values(monkeypatch, enabled):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.setenv("RUNNER_REGISTRY_ENABLED", enabled)
    with patch.object(RunnerRegistryClient, "health_check", return_value=True):
        client = create_runner_registry_client_from_env()
    assert client is not None
    assert isinstance(client, RunnerRegistryClient)
    client.close()


def test_factory_returns_none_when_url_unset(monkeypatch):
    monkeypatch.setenv("RUNNER_REGISTRY_ENABLED", "true")
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    assert create_runner_registry_client_from_env() is None


def test_factory_returns_none_when_token_unset(monkeypatch):
    monkeypatch.setenv("RUNNER_REGISTRY_ENABLED", "true")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.delenv("PROXY_COORDINATOR_TOKEN", raising=False)
    assert create_runner_registry_client_from_env() is None


def test_factory_returns_none_and_closes_when_health_fails(monkeypatch):
    monkeypatch.setenv("RUNNER_REGISTRY_ENABLED", "true")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    with patch.object(RunnerRegistryClient, "health_check", return_value=False), \
            patch.object(RunnerRegistryClient, "close") as close_mock:
        assert create_runner_registry_client_from_env() is None
    close_mock.assert_called_once()
