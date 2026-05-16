"""Pin down the default WriteMode resolution behaviour after ADR-006.

Prior to ADR-006 the resolution fallback in
``packages/python/javdb_platform/db_session._resolve_write_mode`` returned
``'audit'`` when neither an explicit argument nor the
``JAVDB_HISTORY_WRITE_MODE`` env var was set. ADR-006 flips that default
to ``'pending'`` so that pipeline runs follow the modern path without
opt-in. This regression test fails if any future change reverts the
default to ``'audit'`` (or to anything other than ``'pending'``) without
an accompanying ADR amendment.

Resolution order under test:
  1. Process-local override via ``set_active_write_mode()``
  2. ``JAVDB_HISTORY_WRITE_MODE`` env var
  3. Default ``'pending'``
"""

from __future__ import annotations

import pytest

from packages.python.javdb_platform import db_session


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


@pytest.mark.parametrize("explicit", ["audit", "pending"])
def test_resolve_honours_explicit_argument(explicit):
    assert db_session._resolve_write_mode(explicit) == explicit


@pytest.mark.parametrize("env_value", ["audit", "pending", "AUDIT", " pending "])
def test_resolve_honours_env_var(monkeypatch, env_value):
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", env_value)
    assert db_session._resolve_write_mode(None) == env_value.strip().lower()


def test_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "audit")
    assert db_session._resolve_write_mode("pending") == "pending"


def test_process_local_override_wins_over_default():
    db_session.set_active_write_mode("audit")
    assert db_session.get_active_write_mode() == "audit"


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        db_session._resolve_write_mode("not-a-mode")


def test_empty_string_falls_through_to_default(monkeypatch):
    """Empty env var is treated as unset, so the default 'pending' applies."""
    monkeypatch.setenv("JAVDB_HISTORY_WRITE_MODE", "")
    assert db_session._resolve_write_mode(None) == "pending"
