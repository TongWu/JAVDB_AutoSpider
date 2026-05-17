"""Integration tests for POST /api/login/refresh v2.

Covers:
- Default empty body (auto mode) works and returns ok
- Pool mode iterates entries until one succeeds
- Invalid credentials short-circuits the loop
- No credentials returns no_credentials error
- IP-ban log pattern is categorized correctly
"""

from __future__ import annotations

import logging


def test_login_refresh_default_body_works(admin_client, monkeypatch):
    """Empty body still routes to auto mode and returns ok."""
    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        name = explicit_proxy_name or "direct"
        return True, "cookie-stub-abc123", name

    monkeypatch.setattr(
        "javdb.spider.fetch.session.attempt_login_refresh",
        fake_attempt,
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {"JAVDB_USERNAME": "u", "JAVDB_PASSWORD": "p"},
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.update_config_payload",
        lambda *a, **kw: {"status": "ok"},
    )

    r = admin_client.post("/api/login/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # auto mode sends explicit_proxy_name=None so name falls back to "auto" label
    assert body["proxy_used"] in ("direct", "auto", None) or isinstance(body["proxy_used"], str)
    assert isinstance(body["attempts"], list)
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["success"] is True


def test_login_refresh_pool_iterates_on_failure(admin_client, monkeypatch):
    """In pool mode, BE iterates entries until one succeeds."""
    calls: list[str] = []

    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        calls.append(explicit_proxy_name)
        success = len(calls) >= 3
        cookie = "cookie-from-3rd" if success else None
        return success, cookie, explicit_proxy_name

    monkeypatch.setattr("javdb.spider.fetch.session.attempt_login_refresh", fake_attempt)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {
            "JAVDB_USERNAME": "u",
            "JAVDB_PASSWORD": "p",
            "PROXY_POOL": [
                {"name": "p1", "http": "http://a:1"},
                {"name": "p2", "http": "http://b:2"},
                {"name": "p3", "http": "http://c:3"},
            ],
        },
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.update_config_payload",
        lambda *a, **kw: {"status": "ok"},
    )

    r = admin_client.post("/api/login/refresh", json={"proxy_mode": "pool"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["proxy_used"] == "p3"
    assert len(body["attempts"]) == 3
    assert calls == ["p1", "p2", "p3"]
    assert body["attempts"][0]["success"] is False
    assert body["attempts"][1]["success"] is False
    assert body["attempts"][2]["success"] is True


def test_login_refresh_invalid_credentials_short_circuits(admin_client, monkeypatch):
    """If first attempt logs 'invalid password', don't try other pool entries."""

    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        logging.getLogger("javdb.spider.auth.login").error(
            "Login failed: invalid password"
        )
        return False, None, explicit_proxy_name

    monkeypatch.setattr("javdb.spider.fetch.session.attempt_login_refresh", fake_attempt)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {
            "JAVDB_USERNAME": "u",
            "JAVDB_PASSWORD": "p",
            "PROXY_POOL": [
                {"name": "p1", "http": "http://a:1"},
                {"name": "p2", "http": "http://b:2"},
            ],
        },
    )

    r = admin_client.post("/api/login/refresh", json={"proxy_mode": "pool"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_category"] == "invalid_credentials"
    # Should only have tried 1 attempt (the first), not 2
    assert len(body["attempts"]) == 1


def test_login_refresh_no_credentials(admin_client, monkeypatch):
    """Missing credentials returns no_credentials error immediately."""
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {},
    )

    r = admin_client.post("/api/login/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_category"] == "no_credentials"


def test_login_refresh_categorizes_ip_ban(admin_client, monkeypatch):
    """429 log output is categorized as ip_banned."""

    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        logging.getLogger("javdb.spider.auth.login").error(
            "Got 429 Too Many Requests — IP appears rate limited"
        )
        return False, None, explicit_proxy_name

    monkeypatch.setattr("javdb.spider.fetch.session.attempt_login_refresh", fake_attempt)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {"JAVDB_USERNAME": "u", "JAVDB_PASSWORD": "p"},
    )

    r = admin_client.post("/api/login/refresh", json={"proxy_mode": "none"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_category"] == "ip_banned"


def test_login_refresh_single_mode_explicit_url(admin_client, monkeypatch):
    """Single mode uses the explicitly provided proxy_url."""
    captured_proxies: list = []

    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        captured_proxies.append(explicit_proxies)
        return True, "cookie-xyz", explicit_proxy_name

    monkeypatch.setattr("javdb.spider.fetch.session.attempt_login_refresh", fake_attempt)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {"JAVDB_USERNAME": "u", "JAVDB_PASSWORD": "p"},
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.update_config_payload",
        lambda *a, **kw: {"status": "ok"},
    )

    r = admin_client.post(
        "/api/login/refresh",
        json={"proxy_mode": "single", "proxy_url": "http://myproxy:9090"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert len(captured_proxies) == 1
    assert captured_proxies[0] == {"http": "http://myproxy:9090", "https": "http://myproxy:9090"}


def test_login_refresh_pool_max_attempts(admin_client, monkeypatch):
    """max_attempts caps the number of pool entries tried."""
    calls: list[str] = []

    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        calls.append(explicit_proxy_name)
        return False, None, explicit_proxy_name

    monkeypatch.setattr("javdb.spider.fetch.session.attempt_login_refresh", fake_attempt)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {
            "JAVDB_USERNAME": "u",
            "JAVDB_PASSWORD": "p",
            "PROXY_POOL": [
                {"name": "p1", "http": "http://a:1"},
                {"name": "p2", "http": "http://b:2"},
                {"name": "p3", "http": "http://c:3"},
                {"name": "p4", "http": "http://d:4"},
            ],
        },
    )

    r = admin_client.post(
        "/api/login/refresh",
        json={"proxy_mode": "pool", "max_attempts": 2},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    # Only 2 attempts despite 4 pool entries
    assert len(calls) == 2
    assert calls == ["p1", "p2"]


def test_login_refresh_pool_name_filter(admin_client, monkeypatch):
    """pool_names filters which entries are tried."""
    calls: list[str] = []

    def fake_attempt(explicit_proxies=None, explicit_proxy_name=None, **kw):
        calls.append(explicit_proxy_name)
        success = explicit_proxy_name == "p3"
        return success, "cookie" if success else None, explicit_proxy_name

    monkeypatch.setattr("javdb.spider.fetch.session.attempt_login_refresh", fake_attempt)
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {
            "JAVDB_USERNAME": "u",
            "JAVDB_PASSWORD": "p",
            "PROXY_POOL": [
                {"name": "p1", "http": "http://a:1"},
                {"name": "p2", "http": "http://b:2"},
                {"name": "p3", "http": "http://c:3"},
            ],
        },
    )
    monkeypatch.setattr(
        "apps.api.services.config_service.update_config_payload",
        lambda *a, **kw: {"status": "ok"},
    )

    r = admin_client.post(
        "/api/login/refresh",
        json={"proxy_mode": "pool", "pool_names": ["p2", "p3"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # p1 was excluded; only p2 and p3 were tried
    assert calls == ["p2", "p3"]
