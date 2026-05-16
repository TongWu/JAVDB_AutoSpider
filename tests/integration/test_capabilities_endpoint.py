def test_capabilities_returns_locked_shape(admin_client):
    r = admin_client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "2.0.0"
    assert body["ingestion_mode"] in {"local", "github", "dual"}
    assert body["gh_actions"]["tier"] in {"none", "monitor", "edit", "admin"}
    assert "repo" in body["gh_actions"]
    assert "token_configured" in body["gh_actions"]
    assert body["storage_backend"] in {"sqlite", "d1", "dual"}
    assert isinstance(body["features"], dict)
    for key in ("pikpak", "rclone", "smtp", "proxy_pool", "javdb_login", "proxy_preview"):
        assert key in body["features"]
        assert isinstance(body["features"][key], bool)
    assert body["deployment"] in {"colocated", "split", "unknown"}
    assert "build" in body
    assert "frontend_version" in body["build"]
    assert "backend_version" in body["build"]
    assert "git_sha" in body["build"]


def test_capabilities_readable_by_readonly(readonly_client):
    r = readonly_client.get("/api/capabilities")
    assert r.status_code == 200


def test_capabilities_requires_auth(anon_client):
    r = anon_client.get("/api/capabilities")
    assert r.status_code == 401
