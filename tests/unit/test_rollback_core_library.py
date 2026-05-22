"""Library-shape tests for ``javdb.storage.rollback``.

This is the public contract the new Sessions HTTP endpoints (and any other
non-CLI caller) will rely on. Behavioural parity with the CLI is covered
by the pre-existing ``tests/unit/test_rollback*.py`` suites, which call
``apps.cli.db.rollback.main`` end-to-end and continue to pass after the
extraction.
"""

from __future__ import annotations

import pytest

from javdb.storage.rollback import (
    RollbackPlan,
    RollbackRequest,
    RollbackResult,
    apply_rollback,
    plan_rollback,
)


def test_request_dataclass_has_expected_fields():
    req = RollbackRequest(
        session_id="20260516T000000.000000Z-0001-0001",
        dry_run=True,
        include_pending=True,
    )
    assert req.session_id == "20260516T000000.000000Z-0001-0001"
    assert req.dry_run is True
    assert req.include_pending is True


def test_request_defaults_match_cli_defaults():
    """The library defaults must mirror argparse defaults exactly so HTTP
    callers that send an empty body get the same safe behaviour as
    ``python -m apps.cli.db.rollback --session-id <id>``.
    """
    req = RollbackRequest(session_id="some-id")
    # Safe defaults — dry-run on, scope=all, no force, no orphan sweep.
    assert req.dry_run is True
    assert req.scope == "all"
    assert req.force is False
    assert req.include_orphaned is False
    # Pending sessions are processed by default (matches
    # ``--auto-resume-finalizing``).
    assert req.include_pending is True


def test_plan_returns_lookup_error_for_unknown_session():
    """plan_rollback must raise ``LookupError`` when the request cannot
    be resolved to any known session — HTTP callers map this to 404.
    """
    req = RollbackRequest(
        session_id="missing-session-does-not-exist",
        dry_run=True,
    )
    with pytest.raises(LookupError):
        plan_rollback(req)


def test_plan_rollback_returns_rollback_plan_shape():
    """plan_rollback returns a ``RollbackPlan`` with ``session_id`` and
    ``actions`` / ``summary`` fields. We only check shape here; the
    end-to-end CLI tests verify the values.
    """
    # Use a known-empty request: no session_id, no run_id -> empty
    # target set; the library treats "no targets" as a benign empty plan
    # (not a LookupError — LookupError is only for explicit unknown id).
    req = RollbackRequest(dry_run=True)
    plan = plan_rollback(req)
    assert isinstance(plan, RollbackPlan)
    assert isinstance(plan.actions, list)
    assert isinstance(plan.summary, dict)


def test_apply_rollback_returns_rollback_result_shape():
    req = RollbackRequest(dry_run=False)
    result = apply_rollback(req)
    assert isinstance(result, RollbackResult)
    assert isinstance(result.applied, list)
    assert isinstance(result.summary, dict)
