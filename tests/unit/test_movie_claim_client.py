"""Tests for :class:`MovieClaimClient` (P1-B).

Mirror the structure of ``test_login_state_client.py``: keep the network
mocked at the ``requests.Session`` boundary so the suite is deterministic
and runs offline, and assert that every failure path collapses into
:class:`MovieClaimUnavailable` (the fail-open contract documented in
``movie_claim_client.py``).

The "three-piece" fail-open harness referenced in the plan
(``fail-open-test-harness`` todo) is the union of:

1. **DO normal** — happy paths in this file.
2. **DO unreachable** — ``test_*_collapses_*_into_unavailable`` cases.
3. **DO not configured** — ``test_factory_*`` cases against
   :func:`create_movie_claim_client_from_env`.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.movie_claim_client import (  # noqa: E402
    CLAIM_TTL_MAX_MS,
    CLAIM_TTL_MIN_MS,
    DEFAULT_CLAIM_TTL_MS,
    MOVIE_CLAIM_MODE_AUTO,
    MOVIE_CLAIM_MODE_FORCE_ON,
    MOVIE_CLAIM_MODE_OFF,
    ClaimResult,
    CompleteResult,
    MovieClaimClient,
    MovieClaimUnavailable,
    ReleaseResult,
    ReportFailureResult,
    StatusResult,
    create_movie_claim_client_from_env,
    create_movie_claim_client_with_mode_from_env,
    current_shard_date,
    parse_movie_claim_mode,
    _extract_server_time_ms,
)


def _make_client() -> MovieClaimClient:
    """Build a client without any network I/O during construction."""
    return MovieClaimClient(
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


# ── constants ───────────────────────────────────────────────────────────────


def test_default_claim_ttl_is_thirty_minutes():
    assert DEFAULT_CLAIM_TTL_MS == 30 * 60 * 1000


def test_claim_ttl_bounds_match_worker():
    assert CLAIM_TTL_MIN_MS == 60_000
    assert CLAIM_TTL_MAX_MS == 2 * 60 * 60 * 1000


def test_current_shard_date_is_yyyy_mm_dd():
    today = current_shard_date()
    assert len(today) == 10
    assert today[4] == "-" and today[7] == "-"
    int(today.replace("-", ""))  # int-roundtrips → all digits


# ── construction ────────────────────────────────────────────────────────────


def test_init_rejects_empty_base_url():
    with pytest.raises(ValueError, match="base_url"):
        MovieClaimClient(base_url="", token="t")


def test_init_rejects_empty_token():
    with pytest.raises(ValueError, match="token"):
        MovieClaimClient(base_url="https://coord.test", token="")


def test_init_strips_trailing_slash_from_base_url():
    c = MovieClaimClient(base_url="https://coord.test/", token="t")
    assert c.base_url == "https://coord.test"


def test_init_sets_bearer_header():
    c = _make_client()
    assert c._session.headers["Authorization"] == "Bearer dummy"


# ── claim — happy paths & body shape ────────────────────────────────────────


def test_claim_sends_post_with_full_body_and_default_date():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002 — match requests' kwarg name
        captured.update({"url": url, "body": json, "timeout": timeout})
        return _mock_response(200, {
            "acquired": True,
            "current_holder_id": "holder-1",
            "expires_at": 1_000,
            "already_completed": False,
            "server_time": 500,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            r = c.claim("/v/abc", "holder-1")
        assert isinstance(r, ClaimResult)
        assert r.acquired is True
        assert r.current_holder_id == "holder-1"
        assert r.expires_at == 1_000
        assert r.already_completed is False
        assert r.server_time_ms == 500
        assert captured["url"] == "https://coord.example.test/claim_movie"
        assert captured["body"]["href"] == "/v/abc"
        assert captured["body"]["holder_id"] == "holder-1"
        assert captured["body"]["ttl_ms"] == DEFAULT_CLAIM_TTL_MS
        assert captured["body"]["date"] == current_shard_date()
        assert captured["timeout"] == 5.0
    finally:
        c.close()


def test_claim_honours_explicit_ttl_and_date():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update(json)
        return _mock_response(200, {
            "acquired": True, "current_holder_id": "h", "expires_at": 1,
            "already_completed": False, "server_time": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.claim(
                "/v/abc", "holder-1",
                ttl_ms=120_000,
                date="2026-01-15",
            )
        assert captured["ttl_ms"] == 120_000
        assert captured["date"] == "2026-01-15"
    finally:
        c.close()


def test_claim_already_completed_branch_parses_correctly():
    c = _make_client()
    body = {
        "acquired": False,
        "current_holder_id": "",
        "expires_at": 0,
        "already_completed": True,
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.claim("/v/done", "holder-1")
        assert r.acquired is False
        assert r.already_completed is True
        assert r.current_holder_id == ""
        assert r.expires_at == 0
    finally:
        c.close()


def test_claim_contention_branch_returns_other_holder():
    c = _make_client()
    body = {
        "acquired": False,
        "current_holder_id": "holder-2",
        "expires_at": 9999,
        "already_completed": False,
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.claim("/v/contended", "holder-1")
        assert r.acquired is False
        assert r.current_holder_id == "holder-2"
        assert r.already_completed is False
    finally:
        c.close()


def test_claim_validates_empty_inputs():
    c = _make_client()
    try:
        with pytest.raises(MovieClaimUnavailable, match="href"):
            c.claim("", "holder-1")
        with pytest.raises(MovieClaimUnavailable, match="holder_id"):
            c.claim("/v/abc", "")
    finally:
        c.close()


# ── release / complete / status — body + parsing ────────────────────────────


def test_release_sends_correct_body():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update(json)
        return _mock_response(200, {"released": True, "server_time": 1})

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            r = c.release("/v/abc", "holder-1", date="2026-01-15")
        assert isinstance(r, ReleaseResult)
        assert r.released is True
        assert captured == {
            "href": "/v/abc",
            "holder_id": "holder-1",
            "date": "2026-01-15",
        }
    finally:
        c.close()


def test_release_non_owner_returns_released_false():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {"released": False, "server_time": 1}),
        ):
            r = c.release("/v/abc", "holder-2")
        assert r.released is False
    finally:
        c.close()


def test_complete_echoes_href_and_marks_completed():
    c = _make_client()
    body = {"completed": True, "href": "/v/abc", "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.complete("/v/abc", "holder-1")
        assert isinstance(r, CompleteResult)
        assert r.completed is True
        assert r.href == "/v/abc"
    finally:
        c.close()


def test_complete_stale_holder_returns_completed_false():
    c = _make_client()
    body = {"completed": False, "href": "/v/abc", "server_time": 1}
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.complete("/v/abc", "holder-2")
        assert r.completed is False
    finally:
        c.close()


def test_get_status_url_encodes_href_and_appends_date():
    c = _make_client()
    captured: dict = {}

    def fake_get(url, timeout):
        captured["url"] = url
        return _mock_response(200, {
            "current_holder_id": "holder-1",
            "expires_at": 9999,
            "already_completed": False,
            "server_time": 1,
        })

    try:
        with patch.object(c._session, "get", side_effect=fake_get):
            r = c.get_status("/v/with spaces?q=1", date="2026-02-01")
        assert isinstance(r, StatusResult)
        assert r.current_holder_id == "holder-1"
        assert r.expires_at == 9999
        assert r.already_completed is False
        # URL-encoded href must round-trip safely.
        assert "/movie_status?href=" in captured["url"]
        assert "%2Fv%2Fwith%20spaces%3Fq%3D1" in captured["url"]
        assert "&date=2026-02-01" in captured["url"]
    finally:
        c.close()


def test_get_status_null_holder_decodes_to_none():
    c = _make_client()
    body = {
        "current_holder_id": None,
        "expires_at": 0,
        "already_completed": False,
        "server_time": 1,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.get_status("/v/never")
        assert r.current_holder_id is None
        assert r.expires_at == 0
        assert r.already_completed is False
    finally:
        c.close()


# ── P2-A: claim cooldown fields + report_failure ──────────────────────────


def test_claim_parses_p2a_cooldown_metadata():
    """Worker may now return ``cooldown_until`` / ``last_error_kind`` /
    ``fail_count`` alongside the P1-B fields when an href is in the
    failure-cooldown window.  Caller surfaces them via ClaimResult."""
    c = _make_client()
    body = {
        "acquired": False,
        "current_holder_id": "",
        "expires_at": 0,
        "already_completed": False,
        "server_time_ms": 1_000_000,
        "cooldown_until": 2_000_000,
        "last_error_kind": "timeout",
        "fail_count": 3,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.claim("/v/cooling", "holder-1")
        assert r.acquired is False
        assert r.cooldown_until == 2_000_000
        assert r.last_error_kind == "timeout"
        assert r.fail_count == 3
    finally:
        c.close()


def test_claim_p2a_fields_default_to_zero_for_legacy_worker():
    """Pre-P2-A Workers omit the cooldown fields; the client MUST treat
    a missing field as ``0`` / ``""`` so the legacy ``acquired=True``
    happy path keeps working unchanged."""
    c = _make_client()
    body = {
        "acquired": True,
        "current_holder_id": "holder-1",
        "expires_at": 1_000,
        "already_completed": False,
        "server_time": 500,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.claim("/v/abc", "holder-1")
        assert r.cooldown_until == 0
        assert r.last_error_kind == ""
        assert r.fail_count == 0
    finally:
        c.close()


def test_status_parses_p2a_cooldown_metadata():
    c = _make_client()
    body = {
        "current_holder_id": None,
        "expires_at": 0,
        "already_completed": False,
        "server_time_ms": 1_000_000,
        "cooldown_until": 2_500_000,
        "last_error_kind": "cf_bypass",
        "fail_count": 5,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.get_status("/v/under-cooldown")
        assert r.cooldown_until == 2_500_000
        assert r.last_error_kind == "cf_bypass"
        assert r.fail_count == 5
    finally:
        c.close()


def test_report_failure_sends_full_body_with_default_date():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update({"url": url, "body": json, "timeout": timeout})
        return _mock_response(200, {
            "fail_count": 1,
            "cooldown_until": 1_500_000,
            "dead_lettered": False,
            "server_time_ms": 1_000_000,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            r = c.report_failure(
                "/v/abc", "holder-1",
                error_kind="timeout",
            )
        assert isinstance(r, ReportFailureResult)
        assert r.fail_count == 1
        assert r.cooldown_until == 1_500_000
        assert r.dead_lettered is False
        assert captured["url"] == "https://coord.example.test/report_failure"
        assert captured["body"]["href"] == "/v/abc"
        assert captured["body"]["holder_id"] == "holder-1"
        assert captured["body"]["error_kind"] == "timeout"
        assert captured["body"]["date"] == current_shard_date()
        # ``cooldown_ms`` is omitted unless explicitly passed — the
        # Worker then falls back to its built-in cooldown ladder.
        assert "cooldown_ms" not in captured["body"]
    finally:
        c.close()


def test_report_failure_passes_cooldown_ms_override():
    c = _make_client()
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured.update(json)
        return _mock_response(200, {
            "fail_count": 2, "cooldown_until": 0,
            "dead_lettered": False, "server_time_ms": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.report_failure(
                "/v/abc", "holder-1",
                error_kind="proxy_error",
                cooldown_ms=15_000,
                date="2026-02-01",
            )
        assert captured["cooldown_ms"] == 15_000
        assert captured["date"] == "2026-02-01"
    finally:
        c.close()


def test_report_failure_dead_lettered_surfaces_through():
    c = _make_client()
    body = {
        "fail_count": 8,
        "cooldown_until": 99_999_999,
        "dead_lettered": True,
        "server_time_ms": 1,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.report_failure("/v/burnt", "holder-1")
        assert r.dead_lettered is True
        assert r.fail_count == 8
    finally:
        c.close()


def test_report_failure_validates_empty_href():
    c = _make_client()
    try:
        with pytest.raises(MovieClaimUnavailable, match="href"):
            c.report_failure("", "holder-1")
    finally:
        c.close()


def test_report_failure_collapses_503_into_unavailable():
    """``/report_failure`` is a P2-A endpoint; pre-P2-A Workers may 404
    or 503.  Either way the client surfaces Unavailable so the caller
    can fall back to plain ``release``."""
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(503, json_body={"error": "x"}, text="x"),
        ):
            with pytest.raises(MovieClaimUnavailable, match="HTTP 503"):
                c.report_failure("/v/abc", "holder-1")
    finally:
        c.close()


def test_report_failure_collapses_malformed_json_into_unavailable():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {"server_time_ms": 1}),  # missing fail_count
        ):
            with pytest.raises(MovieClaimUnavailable, match="malformed report_failure"):
                c.report_failure("/v/abc", "holder-1")
    finally:
        c.close()


# ── failure modes — DO unreachable / 5xx / malformed JSON ──────────────────


def test_claim_collapses_timeout_into_unavailable():
    c = _make_client()
    try:
        with patch.object(c._session, "post", side_effect=requests.Timeout("slow")):
            with pytest.raises(MovieClaimUnavailable, match="network error"):
                c.claim("/v/abc", "holder-1")
    finally:
        c.close()


def test_claim_collapses_connection_error_into_unavailable():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            side_effect=requests.ConnectionError("refused"),
        ):
            with pytest.raises(MovieClaimUnavailable, match="network error"):
                c.claim("/v/abc", "holder-1")
    finally:
        c.close()


def test_claim_collapses_503_into_unavailable():
    """503 is what the Worker returns when MOVIE_CLAIM_DO binding is missing
    (i.e. v3 migration not yet applied) — the client must surface this as
    Unavailable so the caller falls open to per-process dedup."""
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(503, json_body={"error": "binding missing"},
                                        text='{"error":"binding missing"}'),
        ):
            with pytest.raises(MovieClaimUnavailable, match="HTTP 503"):
                c.claim("/v/abc", "holder-1")
    finally:
        c.close()


def test_claim_collapses_malformed_json_into_unavailable():
    c = _make_client()
    bad = MagicMock(spec=requests.Response)
    bad.status_code = 200
    bad.text = "not json"
    bad.json.side_effect = ValueError("boom")
    try:
        with patch.object(c._session, "post", return_value=bad):
            with pytest.raises(MovieClaimUnavailable, match="invalid JSON"):
                c.claim("/v/abc", "holder-1")
    finally:
        c.close()


def test_claim_collapses_missing_acquired_field_into_unavailable():
    c = _make_client()
    try:
        with patch.object(
            c._session, "post",
            return_value=_mock_response(200, {"server_time": 1}),
        ):
            with pytest.raises(MovieClaimUnavailable, match="malformed claim"):
                c.claim("/v/abc", "holder-1")
    finally:
        c.close()


# ── server_time wire-key fallback (forward-compat) ──────────────────────────


def test_extract_server_time_ms_prefers_explicit_ms_key():
    assert _extract_server_time_ms({"server_time_ms": 7, "server_time": 9}) == 7


def test_extract_server_time_ms_falls_back_to_server_time():
    assert _extract_server_time_ms({"server_time": 9}) == 9


def test_claim_parses_server_time_ms_wire_key():
    c = _make_client()
    body = {
        "acquired": True, "current_holder_id": "h", "expires_at": 1,
        "already_completed": False, "server_time_ms": 99,
    }
    try:
        with patch.object(c._session, "post", return_value=_mock_response(200, body)):
            r = c.claim("/v/abc", "holder-1")
        assert r.server_time_ms == 99
    finally:
        c.close()


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
        with patch.object(
            c._session, "get",
            return_value=_mock_response(503),
        ):
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


# ── factory: env-var disable / unconfigured / health failure ────────────────


def test_factory_returns_none_when_unconfigured(monkeypatch):
    """Auto-default (MOVIE_CLAIM_ENABLED unset) collapses to None when
    URL/TOKEN are missing — same fail-open behaviour the rest of the
    coordinator family follows.  See ``test_with_mode_factory_default_unset_is_auto``
    for the matching healthy path that DOES yield a client."""
    monkeypatch.delenv("MOVIE_CLAIM_ENABLED", raising=False)
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    monkeypatch.delenv("PROXY_COORDINATOR_TOKEN", raising=False)
    assert create_movie_claim_client_from_env() is None


def test_factory_returns_none_when_movie_claim_explicitly_false(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "false")
    assert create_movie_claim_client_from_env() is None


@pytest.mark.parametrize("enabled", ["1", "true", "yes", "TRUE", "Yes"])
def test_factory_accepts_truthy_enable_values(monkeypatch, enabled):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", enabled)
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = create_movie_claim_client_from_env()
    assert client is not None
    assert isinstance(client, MovieClaimClient)
    client.close()


def test_factory_returns_none_when_url_unset(monkeypatch):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "true")
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    assert create_movie_claim_client_from_env() is None


def test_factory_returns_none_when_token_unset(monkeypatch):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "true")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.delenv("PROXY_COORDINATOR_TOKEN", raising=False)
    assert create_movie_claim_client_from_env() is None


def test_factory_returns_none_and_closes_when_health_fails(monkeypatch):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "true")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    with patch.object(MovieClaimClient, "health_check", return_value=False), \
            patch.object(MovieClaimClient, "close") as close_mock:
        assert create_movie_claim_client_from_env() is None
    close_mock.assert_called_once()


# ── three-state mode parser ────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["1", "true", "yes", "TRUE", "Yes", "  true  "])
def test_parse_mode_force_on_values(raw):
    assert parse_movie_claim_mode(raw) == MOVIE_CLAIM_MODE_FORCE_ON


@pytest.mark.parametrize("raw", ["0", "false", "no", "FALSE", "  no  ", ""])
def test_parse_mode_off_values(raw):
    assert parse_movie_claim_mode(raw) == MOVIE_CLAIM_MODE_OFF


@pytest.mark.parametrize("raw", ["auto", "AUTO", "  Auto  "])
def test_parse_mode_auto_values(raw):
    assert parse_movie_claim_mode(raw) == MOVIE_CLAIM_MODE_AUTO


def test_parse_mode_none_input_is_off():
    """``None`` (i.e. function-level "no value") is treated as "explicit off"
    so callers that need the "var unset → auto" semantic must apply it
    themselves; the parser stays a pure value→mode mapping."""
    assert parse_movie_claim_mode(None) == MOVIE_CLAIM_MODE_OFF


@pytest.mark.parametrize("raw", ["maybe", "trure", "garbage", "ato"])
def test_parse_mode_unknown_falls_back_to_auto(raw):
    """Typos must not silently disable the mutex on a multi-runner deploy."""
    assert parse_movie_claim_mode(raw) == MOVIE_CLAIM_MODE_AUTO


# ── with-mode factory ──────────────────────────────────────────────────────


def test_with_mode_factory_default_unset_is_auto(monkeypatch):
    """``MOVIE_CLAIM_ENABLED`` not set in env → ``auto`` (new default)."""
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.delenv("MOVIE_CLAIM_ENABLED", raising=False)
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client, mode = create_movie_claim_client_with_mode_from_env()
    assert client is not None
    assert mode == MOVIE_CLAIM_MODE_AUTO
    client.close()


def test_with_mode_factory_explicit_auto_returns_auto(monkeypatch):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "auto")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client, mode = create_movie_claim_client_with_mode_from_env()
    assert client is not None
    assert mode == MOVIE_CLAIM_MODE_AUTO
    client.close()


def test_with_mode_factory_force_on_returns_force_on(monkeypatch):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "true")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client, mode = create_movie_claim_client_with_mode_from_env()
    assert client is not None
    assert mode == MOVIE_CLAIM_MODE_FORCE_ON
    client.close()


@pytest.mark.parametrize("raw", ["false", "0", "no", ""])
def test_with_mode_factory_off_returns_none_and_off(monkeypatch, raw):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", raw)
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    client, mode = create_movie_claim_client_with_mode_from_env()
    assert client is None
    assert mode == MOVIE_CLAIM_MODE_OFF


def test_with_mode_factory_unconfigured_collapses_to_off(monkeypatch):
    """Auto mode + missing URL/TOKEN → (None, off) so the runtime layer
    short-circuits the auto-toggle path entirely."""
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "auto")
    monkeypatch.delenv("PROXY_COORDINATOR_URL", raising=False)
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    client, mode = create_movie_claim_client_with_mode_from_env()
    assert client is None
    assert mode == MOVIE_CLAIM_MODE_OFF


def test_with_mode_factory_health_failure_collapses_to_off(monkeypatch):
    monkeypatch.setenv("MOVIE_CLAIM_ENABLED", "auto")
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    with patch.object(MovieClaimClient, "health_check", return_value=False):
        client, mode = create_movie_claim_client_with_mode_from_env()
    assert client is None
    assert mode == MOVIE_CLAIM_MODE_OFF


def test_legacy_factory_default_unset_now_returns_client_in_auto_mode(monkeypatch):
    """Backward-compat shim: callers using the legacy single-return
    factory observe ``auto`` as the new default → they get a client
    even without an explicit MOVIE_CLAIM_ENABLED=true.  Pre-auto callers
    that want the old "default off" must now set MOVIE_CLAIM_ENABLED=
    explicitly (covered by the factory-disabled case above)."""
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "t")
    monkeypatch.delenv("MOVIE_CLAIM_ENABLED", raising=False)
    with patch.object(MovieClaimClient, "health_check", return_value=True):
        client = create_movie_claim_client_from_env()
    assert client is not None
    client.close()
