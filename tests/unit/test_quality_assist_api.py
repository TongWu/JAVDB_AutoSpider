"""ADR-024 IMP-08 Task 5: API tests for assist endpoints.

Covers:
- GET /api/quality/recommendations?movie_href=
- GET /api/quality/needs-review?limit=
- POST /api/quality/review-labels
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.routers import quality as quality_router


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
        "shadow_rank": 2,
        "would_replace_current_choice": 0,
        "policy_mode": "assist",
        "decision": "accepted_shadow",
        "reasons": ["main_video_detected", "subtitle_file_present"],
    }
    row.update(overrides)
    return row


class _FakeRepo:
    """Fake TorrentQualityRepo that satisfies the router's needs."""

    def __init__(self, evaluations=None, needs_review_rows=None):
        self.evaluations = evaluations if evaluations is not None else [_evaluation_row()]
        self.needs_review_rows = needs_review_rows if needs_review_rows is not None else []
        self.calls = []

    def list_evaluations_for_movie(self, movie_href):
        self.calls.append(("movie", movie_href))
        return self.evaluations

    def list_recent_evaluations(self, *, limit=50):
        self.calls.append(("recent", limit))
        return self.evaluations

    def list_needs_review(self, *, limit=50):
        self.calls.append(("needs_review", limit))
        return self.needs_review_rows


class _FakeReviewRepo:
    """Fake TorrentQualityReviewRepo for review-label write tests."""

    def __init__(self):
        self.written = []

    def upsert_label(self, label, *, reviewed_at):
        self.written.append({"label": label, "reviewed_at": reviewed_at})


def _authed_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "ted", "role": "admin", "typ": "access"}, 3600)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _readonly_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _unauthed_client():
    from apps.api.services.runtime import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/quality/recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:

    def test_requires_auth(self):
        response = _unauthed_client().get(
            "/api/quality/recommendations", params={"movie_href": "/v/abc"}
        )
        assert response.status_code in {401, 403}

    def test_missing_movie_href_returns_400(self, monkeypatch):
        repo = _FakeRepo()
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))
        client = _authed_client()
        response = client.get("/api/quality/recommendations")
        assert response.status_code == 400

    def test_returns_items_per_category(self, monkeypatch):
        # production row: would_replace_current_choice=False, shadow_rank=1
        # probe row: shadow_rank=1 (best) but production is NOT flagged because
        # prod is already rank 1 in this scenario
        prod_row = _evaluation_row(
            info_hash="PRODHASH",
            javdb_category="subtitle",
            shadow_rank=1,
            would_replace_current_choice=0,
            reasons=["main_video_detected"],
        )
        probe_row = _evaluation_row(
            info_hash="PROBEHASH",
            javdb_category="subtitle",
            shadow_rank=2,
            would_replace_current_choice=0,
            reasons=["main_video_detected", "subtitle_file_present"],
        )
        repo = _FakeRepo(evaluations=[prod_row, probe_row])
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        client = _authed_client()
        response = client.get(
            "/api/quality/recommendations", params={"movie_href": "/v/abc"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert item["javdb_category"] == "subtitle"
        assert "current" in item
        assert "recommended" in item
        assert "reason_diff" in item

    def test_reason_diff_contains_codes_in_recommended_not_in_current(self, monkeypatch):
        prod_row = _evaluation_row(
            info_hash="PRODHASH",
            shadow_rank=2,
            would_replace_current_choice=1,
            reasons=["main_video_detected"],
        )
        probe_row = _evaluation_row(
            info_hash="PROBEHASH",
            shadow_rank=1,
            would_replace_current_choice=0,
            reasons=["main_video_detected", "subtitle_file_present"],
        )
        repo = _FakeRepo(evaluations=[prod_row, probe_row])
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        client = _authed_client()
        response = client.get(
            "/api/quality/recommendations", params={"movie_href": "/v/abc"}
        )
        assert response.status_code == 200
        item = response.json()["items"][0]
        # subtitle_file_present is in recommended but NOT in current
        assert "subtitle_file_present" in item["reason_diff"]

    def test_no_evaluations_returns_empty_items(self, monkeypatch):
        repo = _FakeRepo(evaluations=[])
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        client = _authed_client()
        response = client.get(
            "/api/quality/recommendations", params={"movie_href": "/v/abc"}
        )
        assert response.status_code == 200
        assert response.json()["items"] == []


# ---------------------------------------------------------------------------
# GET /api/quality/needs-review
# ---------------------------------------------------------------------------

class TestNeedsReview:

    def test_requires_auth(self):
        response = _unauthed_client().get("/api/quality/needs-review")
        assert response.status_code in {401, 403}

    def test_non_positive_limit_returns_400(self, monkeypatch):
        repo = _FakeRepo()
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        client = _authed_client()
        response = client.get("/api/quality/needs-review", params={"limit": 0})
        assert response.status_code == 400

    def test_negative_limit_returns_400(self, monkeypatch):
        repo = _FakeRepo()
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        client = _authed_client()
        response = client.get("/api/quality/needs-review", params={"limit": -5})
        assert response.status_code == 400

    def test_limit_capped_at_200(self, monkeypatch):
        repo = _FakeRepo()
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        # Call the function directly to check the cap
        response = quality_router.list_needs_review(limit=500)
        assert ("needs_review", 200) in repo.calls

    def test_default_limit_is_50(self, monkeypatch):
        repo = _FakeRepo()
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        quality_router.list_needs_review()
        assert ("needs_review", 50) in repo.calls

    def test_returns_items_list(self, monkeypatch):
        nr_row = _evaluation_row(
            info_hash="NR1",
            decision="needs_review",
            shadow_rank=None,
            would_replace_current_choice=0,
        )
        repo = _FakeRepo(needs_review_rows=[nr_row])
        monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(repo))

        client = _authed_client()
        response = client.get("/api/quality/needs-review")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["info_hash"] == "NR1"


# ---------------------------------------------------------------------------
# POST /api/quality/review-labels
# ---------------------------------------------------------------------------

class TestReviewLabels:

    def test_requires_auth(self):
        response = _unauthed_client().post(
            "/api/quality/review-labels",
            json={
                "info_hash": "h1",
                "movie_href": "/v/abc",
                "scoring_version": "adr024-shadow-v1",
                "label": "accept",
            },
        )
        assert response.status_code in {401, 403}

    def test_readonly_role_is_rejected(self, monkeypatch):
        """Regression: the route is documented admin-only, but it used to hang off
        _require_auth, which accepts a readonly JWT — letting a readonly user
        overwrite the shared accept/reject/skip dataset."""
        fake_review_repo = _FakeReviewRepo()
        monkeypatch.setattr(
            quality_router, "_review_repo", lambda: nullcontext(fake_review_repo)
        )
        response = _readonly_client().post(
            "/api/quality/review-labels",
            json={
                "info_hash": "h1",
                "movie_href": "/v/abc",
                "scoring_version": "adr024-shadow-v1",
                "label": "accept",
            },
            headers={"X-CSRF-Token": "tok", "Cookie": "csrf_token=tok"},
        )
        assert response.status_code == 403
        assert fake_review_repo.written == []

    def test_invalid_label_returns_422(self, monkeypatch):
        fake_review_repo = _FakeReviewRepo()
        monkeypatch.setattr(
            quality_router, "_review_repo", lambda: nullcontext(fake_review_repo)
        )
        client = _authed_client()
        response = client.post(
            "/api/quality/review-labels",
            json={
                "info_hash": "h1",
                "movie_href": "/v/abc",
                "scoring_version": "adr024-shadow-v1",
                "label": "maybe",  # not in enum
            },
            headers={"X-CSRF-Token": "tok", "Cookie": "csrf_token=tok"},
        )
        assert response.status_code == 422

    def test_valid_label_records_and_returns_200(self, monkeypatch):
        fake_review_repo = _FakeReviewRepo()
        monkeypatch.setattr(
            quality_router, "_review_repo", lambda: nullcontext(fake_review_repo)
        )
        client = _authed_client()
        response = client.post(
            "/api/quality/review-labels",
            json={
                "info_hash": "h1",
                "movie_href": "/v/abc",
                "scoring_version": "adr024-shadow-v1",
                "label": "accept",
                "note": "looks good",
            },
            headers={"X-CSRF-Token": "tok", "Cookie": "csrf_token=tok"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "recorded"}
        assert len(fake_review_repo.written) == 1
        written = fake_review_repo.written[0]
        assert written["label"].label == "accept"
        assert written["label"].info_hash == "h1"
        assert written["label"].reviewer == "ted"  # from JWT sub
        assert written["reviewed_at"] is not None

    def test_reject_label_valid(self, monkeypatch):
        fake_review_repo = _FakeReviewRepo()
        monkeypatch.setattr(
            quality_router, "_review_repo", lambda: nullcontext(fake_review_repo)
        )
        client = _authed_client()
        response = client.post(
            "/api/quality/review-labels",
            json={
                "info_hash": "h2",
                "movie_href": "/v/xyz",
                "scoring_version": "adr024-shadow-v1",
                "label": "reject",
            },
            headers={"X-CSRF-Token": "tok", "Cookie": "csrf_token=tok"},
        )
        assert response.status_code == 200
        assert fake_review_repo.written[0]["label"].label == "reject"

    def test_skip_label_valid(self, monkeypatch):
        fake_review_repo = _FakeReviewRepo()
        monkeypatch.setattr(
            quality_router, "_review_repo", lambda: nullcontext(fake_review_repo)
        )
        client = _authed_client()
        response = client.post(
            "/api/quality/review-labels",
            json={
                "info_hash": "h3",
                "movie_href": "/v/skip",
                "scoring_version": "adr024-shadow-v1",
                "label": "skip",
            },
            headers={"X-CSRF-Token": "tok", "Cookie": "csrf_token=tok"},
        )
        assert response.status_code == 200
