"""Regression: PUT /api/config must NOT regenerate config.py and lose fields.

Root cause (fixed): update_config_payload() and set_javdb_session_cookie() used
to call run_config_generator() unconditionally. That function shells out to
apps.cli.ops.config_generator --output config.py, which rewrites config.py from
CONFIG_MAP only. Any field in the user's config.py NOT in CONFIG_MAP — including
ADMIN_*, API_SECRET_KEY, READONLY_*, QB_URL_ADHOC — gets silently deleted.

The override store (reports/api_config_store.json) is sufficient. load_runtime_config()
already merges config.py + store, so reads see new values via that merge.
"""
from __future__ import annotations


def test_put_config_does_not_call_run_config_generator(admin_client, monkeypatch):
    """PUT /api/config must persist via the override store only; must NOT call
    run_config_generator which would rewrite config.py and drop unregistered fields."""
    called = {"yes": False}
    monkeypatch.setattr(
        "apps.api.services.config_service.run_config_generator",
        lambda *a, **kw: called.update(yes=True),
    )
    # Stub load_runtime_config to a known set so we don't depend on real config.py.
    # QB_URL must be in CONFIG_SCHEMA (it is), so coerce_value won't reject it.
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {"QB_URL": ""},
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.save_store",
        lambda data: None,
    )
    r = admin_client.put("/api/config", json={"QB_URL": "http://x:1"})
    assert r.status_code == 200
    assert called["yes"] is False, (
        "run_config_generator was called from PUT /api/config — this rewrites "
        "config.py from CONFIG_MAP and silently deletes fields not registered "
        "there (ADMIN_*, API_SECRET_KEY, READONLY_*, QB_URL_ADHOC, etc.). "
        "The override store is sufficient; do not call the generator here."
    )


def test_sync_cookie_does_not_call_run_config_generator(admin_client, monkeypatch):
    """POST /api/explore/sync-cookie must persist via the override store only;
    must NOT call run_config_generator."""
    called = {"yes": False}
    monkeypatch.setattr(
        "apps.api.services.config_service.run_config_generator",
        lambda *a, **kw: called.update(yes=True),
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {},
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.save_store",
        lambda data: None,
    )
    r = admin_client.post(
        "/api/explore/sync-cookie",
        json={"cookie": "session_token_abc123"},
    )
    # The endpoint may return 200 or another success code depending on
    # internal validation; what matters is the generator was NOT called.
    assert called["yes"] is False, (
        "run_config_generator was called from POST /api/explore/sync-cookie "
        "(set_javdb_session_cookie path) — same data-loss risk as PUT /api/config. "
        "The override store is sufficient; do not call the generator here."
    )
