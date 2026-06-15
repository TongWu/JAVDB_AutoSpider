"""Router tests for POST /api/explore/aggregate-magnets (ADR-054 WS3)."""

from fastapi.testclient import TestClient
import pytest

from apps.api.infra.auth import _require_auth
from apps.api.services import explore_service


def _auth_user():
    return {
        "sub": "readonly",
        "username": "readonly",
        "role": "readonly",
    }


def test_aggregate_magnets_returns_rows_for_read_authenticated_user(monkeypatch):
    from apps.api.services.runtime import app

    calls = []

    def fake_aggregate(video_code):
        calls.append(video_code)
        return [
            {
                "magnet_uri": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                "name": "ABC-001 1080p",
                "size": "1.4 GB",
                "tags": ["subtitle"],
                "file_count": 1,
                "info_hash": "0123456789abcdef0123456789abcdef01234567",
                "sources": ["sukebei"],
                "quality_score": 42.0,
                "quality_reasons": ["subtitle", "probe_unavailable"],
            }
        ]

    monkeypatch.setattr(explore_service, "aggregate_magnets", fake_aggregate)
    app.dependency_overrides[_require_auth] = _auth_user
    try:
        client = TestClient(app)
        response = client.post(
            "/api/explore/aggregate-magnets",
            json={"video_code": "ABC-001"},
        )
    finally:
        app.dependency_overrides.pop(_require_auth, None)

    assert response.status_code == 200, response.text
    assert calls == ["ABC-001"]
    body = response.json()
    assert body["video_code"] == "ABC-001"
    assert body["magnets"][0]["magnet_uri"].startswith("magnet:?xt=urn:btih:")
    assert body["magnets"][0]["name"] == "ABC-001 1080p"
    assert body["magnets"][0]["sources"] == ["sukebei"]
    assert body["magnets"][0]["quality_score"] == 42.0
    assert body["magnets"][0]["quality_reasons"] == [
        "subtitle",
        "probe_unavailable",
    ]


def test_aggregate_magnets_rejects_empty_video_code():
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = _auth_user
    try:
        client = TestClient(app)
        response = client.post(
            "/api/explore/aggregate-magnets",
            json={"video_code": ""},
        )
    finally:
        app.dependency_overrides.pop(_require_auth, None)

    assert response.status_code == 422


def test_aggregate_magnets_rejects_blank_video_code():
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = _auth_user
    try:
        client = TestClient(app)
        response = client.post(
            "/api/explore/aggregate-magnets",
            json={"video_code": "   "},
        )
    finally:
        app.dependency_overrides.pop(_require_auth, None)

    assert response.status_code == 422


def test_aggregate_magnets_rejects_overlong_video_code():
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = _auth_user
    try:
        client = TestClient(app)
        response = client.post(
            "/api/explore/aggregate-magnets",
            json={"video_code": "A" * 65},
        )
    finally:
        app.dependency_overrides.pop(_require_auth, None)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "video_code",
    [
        "https://example.com/a",
        "//example.com/a",
    ],
)
def test_aggregate_magnets_rejects_url_like_video_code(video_code):
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = _auth_user
    try:
        client = TestClient(app)
        response = client.post(
            "/api/explore/aggregate-magnets",
            json={"video_code": video_code},
        )
    finally:
        app.dependency_overrides.pop(_require_auth, None)

    assert response.status_code == 422


def test_aggregate_magnets_requires_authentication():
    from apps.api.services.runtime import app

    app.dependency_overrides.pop(_require_auth, None)
    client = TestClient(app)
    response = client.post(
        "/api/explore/aggregate-magnets",
        json={"video_code": "ABC-001"},
    )

    assert response.status_code == 401
