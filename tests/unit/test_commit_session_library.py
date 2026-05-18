"""Library-shape tests for ``javdb.storage.sessions.commit``.

Verifies the public contract: dataclass fields, defaults, and error
handling.  Behavioural tests for the underlying DB mutations are in
``test_commit_session_bulk.py`` and ``test_rollback_commit_cli.py``.
"""

from __future__ import annotations

import pytest

from javdb.storage.sessions import (
    CommitRequest,
    CommitResult,
    commit_session,
)


def test_request_dataclass_has_expected_fields():
    req = CommitRequest(
        session_id="20260518T000000.000000Z-0001-0001",
        force=True,
        drop_pending=False,
        emit_metrics=True,
        fanout_claims=True,
        shard_date="2026-05-18",
    )
    assert req.session_id == "20260518T000000.000000Z-0001-0001"
    assert req.force is True
    assert req.emit_metrics is True
    assert req.fanout_claims is True
    assert req.shard_date == "2026-05-18"


def test_request_defaults_match_safe_posture():
    req = CommitRequest(session_id="some-id")
    assert req.force is False
    assert req.drop_pending is False
    assert req.emit_metrics is False
    assert req.fanout_claims is False
    assert req.shard_date is None


def test_result_dataclass_has_claim_results():
    result = CommitResult(
        session_id="x",
        new_state="committed",
        claim_results=[{"ok": True}],
    )
    assert result.claim_results == [{"ok": True}]


def test_result_defaults_to_empty_claim_results():
    result = CommitResult(session_id="x", new_state="committed")
    assert result.claim_results == []


def test_commit_raises_lookup_error_for_unknown_session():
    req = CommitRequest(session_id="missing-session-does-not-exist")
    with pytest.raises(LookupError):
        commit_session(req)
