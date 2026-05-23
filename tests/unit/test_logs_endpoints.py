"""Unit tests for GET /api/logs/search endpoint.

Tests cover:
- Search with match → returns results with correct line numbers
- Search with job_id filter → only searches that job
- Search with date_from/date_to → filters by metadata timestamp
- No match → empty results
- Results exceed limit → truncated=true, total_matched > len(results)
- Empty logs/jobs/ dir → empty results
- Non-existent dir → empty results
- Admin-only access (readonly → 403, anon → 401)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def readonly_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def anon_client():
    from apps.api.services.runtime import app

    return TestClient(app)


def _write_job(logs_dir: Path, job_id: str, kind: str, created_at: str, lines: list[str]) -> None:
    """Helper: write a .log and .meta.json file pair into logs_dir."""
    meta = {"job_id": job_id, "kind": kind, "created_at": created_at}
    (logs_dir / f"{job_id}.meta.json").write_text(json.dumps(meta))
    (logs_dir / f"{job_id}.log").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# TestLogSearchResults
# ---------------------------------------------------------------------------


class TestLogSearchResults:
    def test_search_match_returns_results(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(
            tmp_path,
            "daily-20260523-092547-391a",
            "daily",
            "2026-05-23T09:25:47+00:00",
            [
                "18:20:36    Pipeline      STARTING JAVDB PIPELINE",
                "18:20:37    Spider        fetching page 1",
                "18:20:38    Spider        done",
            ],
        )
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "pipeline"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matched"] >= 1
        assert len(data["results"]) >= 1
        result = data["results"][0]
        assert result["job_id"] == "daily-20260523-092547-391a"
        assert result["line_number"] == 1
        assert "PIPELINE" in result["text"]
        assert result["kind"] == "daily"
        assert result["created_at"] == "2026-05-23T09:25:47+00:00"

    def test_search_is_case_insensitive(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(
            tmp_path,
            "job-abc",
            "daily",
            "2026-05-23T10:00:00+00:00",
            ["Hello World", "HELLO WORLD", "hello world"],
        )
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "HeLLo"})
        assert resp.status_code == 200
        assert resp.json()["total_matched"] == 3

    def test_search_correct_line_numbers(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(
            tmp_path,
            "job-lines",
            "daily",
            "2026-05-23T10:00:00+00:00",
            ["line one", "line two", "line three", "another line"],
        )
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "line"})
        assert resp.status_code == 200
        line_numbers = [r["line_number"] for r in resp.json()["results"]]
        assert line_numbers == [1, 2, 3, 4]

    def test_no_match_returns_empty(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(
            tmp_path,
            "job-nomatch",
            "daily",
            "2026-05-23T10:00:00+00:00",
            ["nothing relevant here"],
        )
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "xyzzy_not_found"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total_matched"] == 0
        assert data["truncated"] is False

    def test_truncated_when_results_exceed_limit(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        lines = [f"match line {i}" for i in range(20)]
        _write_job(
            tmp_path,
            "job-many",
            "daily",
            "2026-05-23T10:00:00+00:00",
            lines,
        )
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "match", "limit": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 5
        assert data["total_matched"] == 20
        assert data["truncated"] is True

    def test_not_truncated_when_results_within_limit(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(
            tmp_path,
            "job-few",
            "daily",
            "2026-05-23T10:00:00+00:00",
            ["match a", "match b", "unrelated line"],
        )
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "match", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matched"] == 2
        assert data["truncated"] is False


# ---------------------------------------------------------------------------
# TestLogSearchFilters
# ---------------------------------------------------------------------------


class TestLogSearchFilters:
    def test_job_id_filter_restricts_to_one_job(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(tmp_path, "job-a", "daily", "2026-05-23T08:00:00+00:00", ["needle here"])
        _write_job(tmp_path, "job-b", "daily", "2026-05-23T09:00:00+00:00", ["needle here too"])
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "needle", "job_id": "job-a"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matched"] == 1
        assert data["results"][0]["job_id"] == "job-a"

    def test_job_id_filter_no_match_returns_empty(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(tmp_path, "job-a", "daily", "2026-05-23T08:00:00+00:00", ["needle"])
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "needle", "job_id": "job-nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_date_from_excludes_older_jobs(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(tmp_path, "job-old", "daily", "2026-05-20T08:00:00+00:00", ["needle"])
        _write_job(tmp_path, "job-new", "daily", "2026-05-23T08:00:00+00:00", ["needle"])
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "needle", "date_from": "2026-05-22"})
        assert resp.status_code == 200
        data = resp.json()
        job_ids = {r["job_id"] for r in data["results"]}
        assert "job-new" in job_ids
        assert "job-old" not in job_ids

    def test_date_to_excludes_newer_jobs(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(tmp_path, "job-old", "daily", "2026-05-20T08:00:00+00:00", ["needle"])
        _write_job(tmp_path, "job-new", "daily", "2026-05-23T08:00:00+00:00", ["needle"])
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "needle", "date_to": "2026-05-21"})
        assert resp.status_code == 200
        data = resp.json()
        job_ids = {r["job_id"] for r in data["results"]}
        assert "job-old" in job_ids
        assert "job-new" not in job_ids

    def test_date_from_and_date_to_combined(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        _write_job(tmp_path, "job-early", "daily", "2026-05-18T00:00:00+00:00", ["needle"])
        _write_job(tmp_path, "job-mid", "daily", "2026-05-20T00:00:00+00:00", ["needle"])
        _write_job(tmp_path, "job-late", "daily", "2026-05-25T00:00:00+00:00", ["needle"])
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get(
            "/api/logs/search",
            params={"q": "needle", "date_from": "2026-05-19", "date_to": "2026-05-21"},
        )
        assert resp.status_code == 200
        data = resp.json()
        job_ids = {r["job_id"] for r in data["results"]}
        assert "job-mid" in job_ids
        assert "job-early" not in job_ids
        assert "job-late" not in job_ids


# ---------------------------------------------------------------------------
# TestLogSearchEdgeCases
# ---------------------------------------------------------------------------


class TestLogSearchEdgeCases:
    def test_empty_logs_dir_returns_empty(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        # tmp_path exists but has no files
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "anything"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total_matched"] == 0
        assert data["truncated"] is False

    def test_nonexistent_dir_returns_empty(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        non_existent = tmp_path / "does_not_exist"
        monkeypatch.setattr(logs_module, "_LOGS_DIR", non_existent)

        resp = admin_client.get("/api/logs/search", params={"q": "anything"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total_matched"] == 0
        assert data["truncated"] is False

    def test_meta_without_log_file_is_skipped(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        # Write only meta, no .log file
        meta = {"job_id": "orphan-job", "kind": "daily", "created_at": "2026-05-23T10:00:00+00:00"}
        (tmp_path / "orphan-job.meta.json").write_text(json.dumps(meta))
        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "anything"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_q_required(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search")
        assert resp.status_code == 422

    def test_q_too_long_rejected(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "a" * 201})
        assert resp.status_code == 422

    def test_limit_exceeds_hard_cap_rejected(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/logs/search", params={"q": "x", "limit": 501})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# TestLogSearchAuth
# ---------------------------------------------------------------------------


class TestLogSearchAuth:
    def test_readonly_returns_403(self, readonly_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = readonly_client.get("/api/logs/search", params={"q": "anything"})
        assert resp.status_code == 403

    def test_anon_returns_401(self, anon_client, tmp_path, monkeypatch):
        import apps.api.routers.logs as logs_module

        monkeypatch.setattr(logs_module, "_LOGS_DIR", tmp_path)

        resp = anon_client.get("/api/logs/search", params={"q": "anything"})
        assert resp.status_code == 401
