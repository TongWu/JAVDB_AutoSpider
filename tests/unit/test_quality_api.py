from __future__ import annotations

from contextlib import nullcontext

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.routers import quality as quality_router


def _evaluation_row(**overrides):
    row = {
        "info_hash": "HASH1",
        "movie_href": "/v/abc",
        "scoring_version": "adr024-shadow-v1",
        "video_code": "ABC-123",
        "javdb_category": "subtitle",
        "magnet_name": "ABC-123-C",
        "inferred_category": "subtitle",
        "category_consistent": 1,
        "subtitle_evidence": "file_present",
        "score": 0.82,
        "shadow_rank": None,
        "would_replace_current_choice": 0,
        "policy_mode": "shadow",
        "decision": "accepted_shadow",
        "reasons": ["main_video_detected", "subtitle_file_present"],
    }
    row.update(overrides)
    return row


def _evidence_row(**overrides):
    row = {
        "info_hash": "HASH1",
        "probe_schema_version": "torrent-probe-v1",
        "target_role": "production_download",
        "probe_target_name": "ABC-123-C",
        "metadata_status": "complete",
        "total_size_bytes": 1000,
        "main_video_size_bytes": 900,
        "main_video_ratio": 0.9,
        "video_file_count": 1,
        "subtitle_file_count": 1,
        "non_video_file_count": 0,
        "junk_size_bytes": 0,
        "junk_size_ratio": 0.0,
        "suspicious_file_count": 0,
        "reasons": ["main_video_detected"],
    }
    row.update(overrides)
    return row


class _FakeRepo:
    def __init__(self, evaluations=None, evidence=None):
        self.evaluations = evaluations or [_evaluation_row()]
        self.evidence = evidence
        self.calls = []

    def list_recent_evaluations(self, *, limit=50):
        self.calls.append(("recent", limit))
        return self.evaluations

    def list_evaluations_for_movie(self, movie_href):
        self.calls.append(("movie", movie_href))
        return self.evaluations

    def get_evidence(self, info_hash, probe_schema_version, target_role):
        self.calls.append(("evidence", info_hash, probe_schema_version, target_role))
        return self.evidence


def _runtime_client(role="admin"):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": role, "role": role, "typ": "access"}, 3600)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_runtime_app_requires_auth_for_quality_evaluations():
    from apps.api.services.runtime import app

    response = TestClient(app).get("/api/quality/evaluations")

    assert response.status_code in {401, 403}


def test_runtime_app_registers_quality_evaluations_route(monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

    response = _runtime_client().get("/api/quality/evaluations")

    assert response.status_code == 200
    assert response.json()["items"][0]["info_hash"] == "HASH1"
    assert repo.calls == [("recent", 50)]


def test_recent_evaluations_list_returns_item_bool_conversion_and_reasons(monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

    response = quality_router.list_evaluations(limit=500)

    assert repo.calls == [("recent", 200)]
    assert len(response.items) == 1
    item = response.items[0]
    assert item.info_hash == "HASH1"
    assert item.category_consistent is True
    assert item.would_replace_current_choice is False
    assert item.reasons == ["main_video_detected", "subtitle_file_present"]


def test_movie_href_filter_calls_movie_specific_path(monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

    response = quality_router.list_evaluations(movie_href="/v/abc")

    assert repo.calls == [("movie", "/v/abc")]
    assert response.items[0].movie_href == "/v/abc"


def test_evidence_found_returns_schema_with_reasons(monkeypatch):
    repo = _FakeRepo(evidence=_evidence_row())
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

    response = quality_router.get_evidence("HASH1")

    assert repo.calls == [
        (
            "evidence",
            "HASH1",
            quality_router.PROBE_SCHEMA_VERSION,
            "production_download",
        )
    ]
    assert response.info_hash == "HASH1"
    assert response.target_role == "production_download"
    assert response.reasons == ["main_video_detected"]


def test_evidence_missing_raises_404(monkeypatch):
    repo = _FakeRepo(evidence=None)
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

    with pytest.raises(HTTPException) as exc_info:
        quality_router.get_evidence("HASH404")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Evidence not found"


def test_non_positive_limit_raises_400_without_repo_use(monkeypatch):
    def fail_repo():
        raise AssertionError("_repo should not be used")

    monkeypatch.setattr(quality_router, "_repo", fail_repo)

    with pytest.raises(HTTPException) as exc_info:
        quality_router.list_evaluations(limit=0)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "limit must be a positive integer"


def test_adapter_tolerates_legacy_reasons_json_string(monkeypatch):
    repo = _FakeRepo(
        evaluations=[
            _evaluation_row(
                reasons=None,
                reasons_json='["main_video_detected", "subtitle_file_present"]',
            )
        ]
    )
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

    response = quality_router.list_evaluations()

    assert response.items[0].reasons == [
        "main_video_detected",
        "subtitle_file_present",
    ]
