"""Verify that FE-consumed endpoints expose typed (referenced) 200 response schemas
in the generated OpenAPI document. The frontend toolchain generates TypeScript types
from this schema, so endpoints that return ad-hoc dicts produce unusable `unknown` types.
"""

from __future__ import annotations


def test_each_fe_consumed_endpoint_has_typed_200_response(admin_client):
    schema = admin_client.get("/openapi.json").json()
    paths = schema["paths"]
    must_be_typed = [
        ("/api/tasks/daily", "post"),
        ("/api/tasks/adhoc", "post"),
        ("/api/tasks", "get"),
        ("/api/tasks/stats", "get"),
        ("/api/tasks/{job_id}", "get"),
        ("/api/jobs/spider", "post"),
        ("/api/jobs/{job_id}/status", "get"),
        ("/api/auth/login", "post"),
        ("/api/auth/refresh", "post"),
        ("/api/config", "get"),
        ("/api/config", "put"),
        ("/api/explore/resolve", "post"),
        ("/api/explore/one-click", "post"),
        ("/api/explore/index-status", "post"),
        ("/api/explore/download-magnet", "post"),
        ("/api/explore/search-by-video-code", "post"),
    ]
    for path, method in must_be_typed:
        assert path in paths, f"missing path {path}"
        assert method in paths[path], f"missing method {method} on {path}"
        op = paths[path][method]
        resp200 = op["responses"]["200"]
        schema_block = resp200["content"]["application/json"]["schema"]
        # An untyped FastAPI return produces `schema: {}` (no $ref, no type, no items).
        # A typed response_model produces either `$ref` or a primitive `type`.
        is_typed = (
            "$ref" in schema_block
            or "allOf" in schema_block
            or "oneOf" in schema_block
            or "anyOf" in schema_block
            or schema_block.get("type") in {"array", "string", "integer", "number", "boolean"}
        )
        assert is_typed, (
            f"{method.upper()} {path} returns untyped object — add response_model= "
            f"(schema was {schema_block!r})"
        )


def test_login_refresh_and_sync_cookie_exist_and_typed(admin_client):
    schema = admin_client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/login/refresh" in paths
    op = paths["/api/login/refresh"]["post"]
    assert "$ref" in op["responses"]["200"]["content"]["application/json"]["schema"]

    assert "/api/explore/sync-cookie" in paths
    op = paths["/api/explore/sync-cookie"]["post"]
    assert "$ref" in op["responses"]["200"]["content"]["application/json"]["schema"]
