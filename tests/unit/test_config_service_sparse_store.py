"""Unit tests for sparse store-writes in config_service.

Regression: update_config_payload() previously called load_runtime_config()
(returning the FULL merged config from defaults + config.py + store) and
wrote the entire dict back via save_store(). That made the override store
behave as a config.py snapshot — any single wrong value entered once via
the Onboarding wizard (e.g. typing the ADHOC URL into the QB_URL field)
would land in the store as a permanent override, masking config.py
forever.

Sparse writes load only the existing store, mutate only the requested
keys, and persist back. Unrelated keys never get promoted into the
store, so config.py edits remain visible for fields the user never
touched via the API.
"""
from __future__ import annotations

import os
import sys
from types import ModuleType
from typing import Any, Dict


project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from apps.api.services import config_service  # noqa: E402


def _setup_writers(monkeypatch, initial_store: Dict[str, Any]):
    """Stub load_store/save_store to a captured in-memory dict.

    Returns the captured dict — callers assert on its final contents
    after calling update_config_payload / set_javdb_session_cookie.
    """
    captured: Dict[str, Any] = dict(initial_store)

    def _load() -> Dict[str, Any]:
        return dict(captured)

    def _save(values: Dict[str, Any]) -> None:
        captured.clear()
        captured.update(values)

    monkeypatch.setattr(config_service, "load_store", _load)
    monkeypatch.setattr(config_service, "save_store", _save)
    return captured


def test_update_writes_only_delta_to_store(monkeypatch):
    """PUT /api/config { QB_URL: ... } must NOT also persist unrelated
    fields from config.py (QB_URL_ADHOC, ADMIN_USERNAME, etc.) into the
    store. Only QB_URL lands."""
    legacy_config = ModuleType("config")
    legacy_config.QB_URL = "http://from-config-py:8080"
    legacy_config.QB_URL_ADHOC = "http://adhoc-from-config-py:9090"
    legacy_config.ADMIN_USERNAME = "admin-from-config-py"
    monkeypatch.setattr(
        config_service.importlib, "import_module", lambda _: legacy_config
    )
    captured = _setup_writers(monkeypatch, initial_store={})

    config_service.update_config_payload(
        {"QB_URL": "http://new-value:1234"}, username="admin"
    )

    # Only the changed key should be in the store. Unrelated keys
    # (QB_URL_ADHOC, ADMIN_USERNAME) MUST NOT be promoted into the store.
    assert captured == {"QB_URL": "http://new-value:1234"}
    assert "QB_URL_ADHOC" not in captured
    assert "ADMIN_USERNAME" not in captured


def test_update_preserves_existing_overrides(monkeypatch):
    """Existing store entries unrelated to the current update stay put."""
    legacy_config = ModuleType("config")
    legacy_config.QB_URL = "http://from-config-py:8080"
    monkeypatch.setattr(
        config_service.importlib, "import_module", lambda _: legacy_config
    )
    captured = _setup_writers(
        monkeypatch,
        initial_store={
            "ADMIN_USERNAME": "explicit-admin-from-wizard",
            "QB_USERNAME": "explicit-qb-user",
        },
    )

    config_service.update_config_payload(
        {"QB_URL": "http://changed:1"}, username="admin"
    )

    assert captured["QB_URL"] == "http://changed:1"
    assert captured["ADMIN_USERNAME"] == "explicit-admin-from-wizard"
    assert captured["QB_USERNAME"] == "explicit-qb-user"


def test_update_unchanged_sentinel_is_skipped(monkeypatch):
    """When the FE submits the masked sensitive placeholder ('********'),
    coerce_value returns '__UNCHANGED__' and that key must NOT be written
    to the store."""
    monkeypatch.setattr(
        config_service.importlib, "import_module", lambda _: ModuleType("config")
    )
    captured = _setup_writers(monkeypatch, initial_store={})

    config_service.update_config_payload(
        {"QB_PASSWORD": "********", "QB_URL": "http://only-me:1"},
        username="admin",
    )

    assert "QB_PASSWORD" not in captured
    assert captured.get("QB_URL") == "http://only-me:1"


def test_set_javdb_cookie_writes_only_cookie(monkeypatch):
    """set_javdb_session_cookie must persist only the cookie, not the
    full merged config."""
    legacy_config = ModuleType("config")
    legacy_config.QB_URL = "http://from-config-py:8080"
    legacy_config.ADMIN_USERNAME = "admin-from-config-py"
    monkeypatch.setattr(
        config_service.importlib, "import_module", lambda _: legacy_config
    )
    captured = _setup_writers(
        monkeypatch, initial_store={"ADMIN_USERNAME": "explicit-from-wizard"}
    )

    config_service.set_javdb_session_cookie(
        "session_cookie_abc123", username="admin"
    )

    assert captured["JAVDB_SESSION_COOKIE"] == "session_cookie_abc123"
    # Pre-existing override is preserved.
    assert captured["ADMIN_USERNAME"] == "explicit-from-wizard"
    # config.py value MUST NOT have been promoted into the store.
    assert "QB_URL" not in captured
