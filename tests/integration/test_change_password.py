"""Integration tests for POST /api/auth/change-password.

Covers:
- Successful change with correct current password updates the in-memory hash
- Persists the new hash to the API config store so it survives BE restart
- Returns 400 when current_password is wrong (and does not mutate state)
- Returns 422 when new_password is shorter than the minimum length
- Requires authentication (anon → 401)
"""

from __future__ import annotations

import pytest


def _setup_admin_password(monkeypatch, plain: str) -> None:
    from apps.api.infra import auth as auth_infra

    new_hash = auth_infra.PASSWORD_CTX.hash(plain)
    auth_infra.USERS["admin"] = {"role": "admin", "password_hash": new_hash}


def test_change_password_success_updates_hash_and_persists(
    admin_client, monkeypatch
):
    from apps.api.infra import auth as auth_infra

    _setup_admin_password(monkeypatch, "old-password-1234")

    captured: dict[str, dict] = {}

    def fake_update(updates, username):
        captured["updates"] = updates
        captured["username"] = username
        return {"status": "ok"}

    monkeypatch.setattr(
        "apps.api.services.config_service.update_config_payload",
        fake_update,
    )

    r = admin_client.post(
        "/api/auth/change-password",
        json={
            "current_password": "old-password-1234",
            "new_password": "new-password-5678",
        },
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}
    # In-memory hash updated
    assert auth_infra.PASSWORD_CTX.verify(
        "new-password-5678", auth_infra.USERS["admin"]["password_hash"]
    )
    # Persisted to the config store under the admin hash key
    assert captured["username"] == "admin"
    assert "ADMIN_PASSWORD_HASH" in captured["updates"]
    persisted_hash = captured["updates"]["ADMIN_PASSWORD_HASH"]
    assert auth_infra.PASSWORD_CTX.verify("new-password-5678", persisted_hash)


def test_change_password_wrong_current_returns_401(admin_client, monkeypatch):
    from apps.api.infra import auth as auth_infra

    _setup_admin_password(monkeypatch, "old-password-1234")
    original_hash = auth_infra.USERS["admin"]["password_hash"]

    def fake_update(updates, username):  # pragma: no cover — should not be called
        pytest.fail("update_config_payload must not be called on wrong current pw")

    monkeypatch.setattr(
        "apps.api.services.config_service.update_config_payload",
        fake_update,
    )

    r = admin_client.post(
        "/api/auth/change-password",
        json={
            "current_password": "wrong-old-password",
            "new_password": "new-password-5678",
        },
    )

    assert r.status_code == 400, r.text
    # In-memory hash unchanged
    assert auth_infra.USERS["admin"]["password_hash"] == original_hash


def test_change_password_short_new_returns_422(admin_client, monkeypatch):
    _setup_admin_password(monkeypatch, "old-password-1234")

    r = admin_client.post(
        "/api/auth/change-password",
        json={
            "current_password": "old-password-1234",
            "new_password": "short",
        },
    )
    assert r.status_code == 422, r.text


def test_change_password_requires_auth(anon_client):
    r = anon_client.post(
        "/api/auth/change-password",
        json={
            "current_password": "anything",
            "new_password": "new-password-5678",
        },
    )
    # CSRF middleware may reject first (403) or auth dependency rejects (401).
    # Either proves the endpoint is gated and not anonymously callable.
    assert r.status_code in (401, 403), r.text
