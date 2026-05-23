"""Pin down the default WriteMode resolution behaviour.

ADR-006 set 'pending' as the default. ADR-005 PR-4 retired audit mode
entirely — requesting 'audit' now falls back to 'pending' with a warning.

Resolution order under test:
  1. Process-local override via ``set_active_write_mode()``
  2. ``JAVDB_HISTORY_WRITE_MODE`` env var
  3. Default ``'pending'``
"""

from __future__ import annotations

import pytest

from javdb.storage.db import _db_session as db_session


@pytest.fixture(autouse=True)
def _clear_env_and_override(monkeypatch):
    """Each test starts with no env var and no process-local override."""
    monkeypatch.delenv("JAVDB_HISTORY_WRITE_MODE", raising=False)
    db_session.set_active_write_mode(None)
    yield
    db_session.set_active_write_mode(None)


def test_resolve_returns_pending_when_nothing_set():
    assert db_session._resolve_write_mode(None) == "pending"


def test_get_active_returns_pending_when_nothing_set():
    assert db_session.get_active_write_mode() == "pending"


def test_resolve_honours_explicit_pending():
    assert db_session._resolve_write_mode("pending") == "pending"


def test_resolve_audit_falls_back_to_pending():
    assert db_session._resolve_write_mode("audit") == "pending"


@pytest.mark.parametrize("env_value", ["pending", " pending "])
def test_resolve_honours_env_var(monkeypatch, env_value):
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", env_value)
    assert db_session._resolve_write_mode(None) == env_value.strip().lower()


def test_resolve_env_var_audit_falls_back_to_pending(monkeypatch):
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "audit")
    assert db_session._resolve_write_mode(None) == "pending"


def test_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "audit")
    assert db_session._resolve_write_mode("pending") == "pending"


def test_process_local_override_wins_over_default():
    db_session.set_active_write_mode("pending")
    assert db_session.get_active_write_mode() == "pending"


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        db_session._resolve_write_mode("not-a-mode")


def test_empty_string_falls_through_to_default(monkeypatch):
    """Empty env var is treated as unset, so the default 'pending' applies."""
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "")
    assert db_session._resolve_write_mode(None) == "pending"
