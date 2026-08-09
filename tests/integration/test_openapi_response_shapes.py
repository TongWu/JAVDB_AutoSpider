"""Verify that FE-consumed endpoints expose typed (referenced) 200 response schemas
in the generated OpenAPI document. The frontend toolchain generates TypeScript types
from this schema, so endpoints that return ad-hoc dicts produce unusable `unknown` types.
"""

from __future__ import annotations

import json
from pathlib import Path

# Repo root, used to read the committed contract artifact.
_REPO_ROOT = Path(__file__).resolve().parents[2]


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
        ("/api/explore/aggregate-magnets", "post"),
        ("/api/explore/one-click", "post"),
        ("/api/explore/index-status", "post"),
        ("/api/explore/download-magnet", "post"),
        ("/api/explore/search-by-video-code", "post"),
        ("/api/library/acquisition/summary", "get"),
        ("/api/library/acquisition/recent", "get"),
        ("/api/library/acquisition/trend", "get"),
        ("/api/diag/alert-policies", "get"),
        ("/api/diag/alert-policies/{incident_type}", "put"),
        ("/api/diag/ops-incidents/{incident_id}/alert-events", "get"),
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


# Bearer-token security contract: every operation guarded by the shared JWT
# dependency (_require_auth / require_role / _require_auth_or_token) must
# advertise `security: [{"BearerAuth": []}]` so OpenAPI consumers know a token
# is required. Regression guard for the ADR-035 PR #181 follow-up, where
# _require_auth parsed the Authorization header manually and therefore never
# propagated the requirement into the schema.
_BEARER_REQUIREMENT = [{"BearerAuth": []}]


def test_jwt_protected_operations_declare_bearer_security(admin_client):
    schema = admin_client.get("/openapi.json").json()
    paths = schema["paths"]

    # The requirement only resolves if the scheme is registered.
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert schemes.get("BearerAuth", {}).get("scheme") == "bearer"

    # One representative route per shared dependency, plus the quality route
    # that previously declared the requirement via openapi_extra (it must keep
    # exactly one entry, not a duplicate, now that the dependency emits it).
    protected = [
        ("/api/diag/ops-incidents", "get"),             # _require_auth
        ("/api/diag/alert-policies", "get"),            # _require_auth
        ("/api/diag/alert-policies/{incident_type}", "put"),  # require_role("admin")
        ("/api/diag/ops-incidents/{incident_id}/alert-events", "get"),  # _require_auth
        ("/api/sessions", "get"),                       # _require_auth
        ("/api/diag/javdb-session/refresh", "post"),    # require_role("admin")
        ("/api/sessions/{session_id}/commit", "post"),  # require_role("admin")
        ("/api/explore/proxy-page", "get"),             # _require_auth_or_token
        ("/api/quality/evaluations", "get"),            # was openapi_extra
    ]
    for path, method in protected:
        assert path in paths, f"missing path {path}"
        op = paths[path][method]
        assert op.get("security") == _BEARER_REQUIREMENT, (
            f"{method.upper()} {path} must declare security {_BEARER_REQUIREMENT}, "
            f"got {op.get('security')!r}"
        )


def test_security_requirement_is_clean_across_all_operations(admin_client):
    schema = admin_client.get("/openapi.json").json()
    paths = schema["paths"]

    # Public auth entrypoint must not require a bearer token.
    assert "security" not in paths["/api/auth/login"]["post"]

    # Any operation that declares security must declare exactly the single
    # BearerAuth requirement — never a duplicate (which deep_dict_update would
    # produce if both the dependency and openapi_extra emitted it).
    for path, methods in paths.items():
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            sec = op.get("security")
            if sec is not None:
                assert sec == _BEARER_REQUIREMENT, (
                    f"{method.upper()} {path} has unexpected security {sec!r}"
                )


def test_bearer_guarded_operations_document_401_403(admin_client):
    # Every JWT-guarded operation returns 401 (missing/invalid token) or 403
    # (wrong role / CSRF) at runtime, so the contract must document both bodies.
    # _custom_openapi backfills them centrally from the BearerAuth requirement;
    # this guards that backfill against regression and against new guarded
    # routes shipping without the auth-failure contract. The body shape is the
    # {"detail": "<string>"} that FastAPI's HTTPException serializes to.
    schema = admin_client.get("/openapi.json").json()
    detail_schema = {
        "type": "object",
        "required": ["detail"],
        "properties": {"detail": {"type": "string"}},
    }
    secured = 0
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict) or op.get("security") != _BEARER_REQUIREMENT:
                continue
            secured += 1
            responses = op["responses"]
            for status, description in (("401", "Unauthorized"), ("403", "Forbidden")):
                assert status in responses, (
                    f"{method.upper()} {path} missing {status} response"
                )
                body = responses[status]
                assert body["description"] == description, (
                    f"{method.upper()} {path} {status} description {body['description']!r}"
                )
                assert (
                    body["content"]["application/json"]["schema"] == detail_schema
                ), f"{method.upper()} {path} {status} body shape drifted"
    # Sanity: the suite would silently pass if no route were guarded.
    assert secured > 0


def _security_index(schema: dict) -> dict[tuple[str, str], list | None]:
    # Map every (path, method) operation -> its declared `security` (or None).
    # Skip the optional TEST_MODE-only router (prefix /api/test): it is never
    # part of the committed production contract and may be present at runtime if
    # another test reloaded the app with TEST_MODE=1.
    index: dict[tuple[str, str], list | None] = {}
    for path, methods in schema["paths"].items():
        if path == "/api/test" or path.startswith("/api/test/"):
            continue
        for method, op in methods.items():
            if isinstance(op, dict):
                index[(path, method)] = op.get("security")
    return index


def test_committed_openapi_matches_runtime_security_contract(admin_client):
    # The committed docs/api/openapi.json is the authoritative contract the TS
    # frontend derives from, but publish-openapi.yml only regenerates it after a
    # merge to main. Guard against a PR forgetting to run `dump_openapi` by
    # asserting the committed artifact's security contract equals the live app's
    # for every operation — this catches docs drift as well as any protected
    # route losing, or public route gaining, a `security` block in the artifact.
    runtime = admin_client.get("/openapi.json").json()
    committed = json.loads(
        (_REPO_ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    assert _security_index(runtime) == _security_index(committed)
