"""Phase-1 ADR-008 — tests for SessionPayload + extended register/heartbeat/
unregister method signatures.

Asserts:
  * SessionPayload.to_payload() produces the right wire shape.
  * register() includes `session` in the POST body when provided.
  * heartbeat() includes `session` when provided.
  * unregister() includes `session` when provided.
  * RegisterResult / HeartbeatResult parse `pipeline_paused_until` +
    `pipeline_pause_reason` from the Worker response.
  * Backward-compat: omitting `session` produces a body without the key.
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
    RunnerRegistryClient,
    SessionPayload,
)


def _mock_response(status=200, json_body=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = ""
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no JSON")
    return resp


def _make_client() -> RunnerRegistryClient:
    return RunnerRegistryClient(base_url="https://coord.example.test", token="dummy")  # noqa: S106


# ── SessionPayload.to_payload ───────────────────────────────────────────────


def test_session_payload_minimum_required_fields():
    sp = SessionPayload(session_id="s1", status="in_progress")
    assert sp.to_payload() == {"session_id": "s1", "status": "in_progress"}


def test_session_payload_includes_optional_fields_when_set():
    sp = SessionPayload(
        session_id="s1",
        status="failed",
        write_mode="audit",
        report_type="daily",
        failure_reason="boom",
    )
    p = sp.to_payload()
    assert p["session_id"] == "s1"
    assert p["status"] == "failed"
    assert p["write_mode"] == "audit"
    assert p["report_type"] == "daily"
    assert p["failure_reason"] == "boom"


def test_session_payload_omits_none_optional_fields():
    sp = SessionPayload(session_id="s1", status="in_progress")
    p = sp.to_payload()
    # None-valued fields must not show up — Worker dashboard treats absent
    # write_mode/failure_reason differently from explicit empty strings.
    assert "write_mode" not in p
    assert "report_type" not in p
    assert "failure_reason" not in p


# ── register / heartbeat / unregister body contracts ────────────────────────


def test_register_sends_session_field_when_provided():
    client = _make_client()
    fake_resp = _mock_response(
        json_body={
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
        }
    )
    with patch.object(client._session, "post", return_value=fake_resp) as m:
        client.register(
            holder_id="h1",
            session=SessionPayload(
                session_id="sess-1",
                status="in_progress",
                write_mode="pending",
                report_type="daily",
            ),
        )
    body = m.call_args.kwargs["json"]
    assert body["holder_id"] == "h1"
    assert body["session"]["session_id"] == "sess-1"
    assert body["session"]["status"] == "in_progress"
    assert body["session"]["write_mode"] == "pending"
    assert body["session"]["report_type"] == "daily"


def test_register_omits_session_field_when_absent():
    """Backwards-compatibility: register() with no session must NOT add the key."""
    client = _make_client()
    fake_resp = _mock_response(
        json_body={
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
        }
    )
    with patch.object(client._session, "post", return_value=fake_resp) as m:
        client.register(holder_id="h1")
    body = m.call_args.kwargs["json"]
    assert "session" not in body


def test_heartbeat_sends_session_field_when_provided():
    client = _make_client()
    fake_resp = _mock_response(json_body={"alive": True, "server_time": 0})
    with patch.object(client._session, "post", return_value=fake_resp) as m:
        client.heartbeat(
            "h1",
            session=SessionPayload(
                session_id="sess-1",
                status="finalizing",
            ),
        )
    body = m.call_args.kwargs["json"]
    assert body["holder_id"] == "h1"
    assert body["session"]["session_id"] == "sess-1"
    assert body["session"]["status"] == "finalizing"


def test_heartbeat_omits_session_field_when_absent():
    client = _make_client()
    fake_resp = _mock_response(json_body={"alive": True, "server_time": 0})
    with patch.object(client._session, "post", return_value=fake_resp) as m:
        client.heartbeat("h1")
    body = m.call_args.kwargs["json"]
    assert "session" not in body


def test_unregister_sends_session_field_with_terminal_status():
    client = _make_client()
    fake_resp = _mock_response(json_body={"unregistered": True, "server_time": 0})
    with patch.object(client._session, "post", return_value=fake_resp) as m:
        client.unregister(
            "h1",
            session=SessionPayload(
                session_id="sess-1",
                status="committed",
            ),
        )
    body = m.call_args.kwargs["json"]
    assert body["holder_id"] == "h1"
    assert body["session"]["status"] == "committed"


# ── RegisterResult / HeartbeatResult pipeline_paused parsing ────────────────


def test_register_result_parses_pipeline_paused_fields():
    client = _make_client()
    fake_resp = _mock_response(
        json_body={
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 1000,
            "pipeline_paused_until": 5000,
            "pipeline_pause_reason": "release freeze",
        }
    )
    with patch.object(client._session, "post", return_value=fake_resp):
        result = client.register(holder_id="h1")
    assert result.pipeline_paused_until == 5000
    assert result.pipeline_pause_reason == "release freeze"


def test_register_result_pipeline_paused_defaults_to_zero_when_absent():
    """Worker omits pipeline_paused_until when no pause is active."""
    client = _make_client()
    fake_resp = _mock_response(
        json_body={
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 1000,
        }
    )
    with patch.object(client._session, "post", return_value=fake_resp):
        result = client.register(holder_id="h1")
    assert result.pipeline_paused_until == 0
    assert result.pipeline_pause_reason is None


def test_heartbeat_result_parses_pipeline_paused_fields():
    client = _make_client()
    fake_resp = _mock_response(
        json_body={
            "alive": True,
            "server_time": 1000,
            "pipeline_paused_until": 9000,
            "pipeline_pause_reason": "incident IN-42",
        }
    )
    with patch.object(client._session, "post", return_value=fake_resp):
        result = client.heartbeat("h1")
    assert result.pipeline_paused_until == 9000
    assert result.pipeline_pause_reason == "incident IN-42"
