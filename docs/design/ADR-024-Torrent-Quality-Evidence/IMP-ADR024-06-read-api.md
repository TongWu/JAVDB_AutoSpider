# IMP-ADR024-06: ADR-024 Phase 1 — Read-Only API Surface

**Status:** Proposed — design-reviewed & hardened 2026-05-31 (see Design Review note).

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the collected shadow evidence/evaluations through read-only FastAPI endpoints under `/api/quality`, following the ADR-026 diagnostics router pattern (auth-gated GET, repo-per-request, Pydantic response schemas).

**Architecture:** A new router `apps/api/routers/quality.py` + schemas `apps/api/schemas/quality.py`. Reads go through `TorrentQualityRepo` (IMP-02) against `REPORTS_DB_PATH`. Endpoints: list recent evaluations, list evaluations for a movie, get evidence by `info_hash`. Registered in `apps/api/services/runtime.py`. Read-only — no writes, no production effect.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md) roadmap (Phase 1 ships "logs/API report"). ADR-018 dual-backend note: the **TypeScript** backend lives in the standalone `javdb-autospider-web` repo (not in this tree) and is out of scope for this IMP — the Python read surface is the Phase 1 deliverable; mirror to TS in a linked follow-up if/when the Web surface needs it.

**Related:** [IMP-ADR024-02](IMP-ADR024-02-models-repo.md) (repo) · [IMP-ADR024-05](IMP-ADR024-05-evidence-collection.md) (writes the rows).

**Depends on:** IMP-02 (repo + tables). Best run after IMP-05 so there is data to read, but not strictly blocked by it.

**Blocks:** Nothing in Phase 1.

---

## Design Review note (2026-05-31)

A `brainstorming` review fixed one defect carried over from the hardened IMP-02:

- **`_repo()` could not construct a connection-less repo.** The draft returned
  `TorrentQualityRepo()` (no args), which no longer exists — IMP-02 is now
  conn-injected. Fixed to mirror the diagnostics router's per-request pattern: a
  `@contextmanager` `_repo()` opens `with get_db(REPORTS_DB_PATH) as conn` and
  yields `TorrentQualityRepo(conn)`; endpoints use `with _repo() as repo:`. The
  test's monkeypatch seam is preserved by swapping the fake to
  `lambda: nullcontext(_FakeRepo())`.
- **Repo rows are already JSON-decoded.** `TorrentQualityRepo._to_dict()` strips
  the `_json` suffix and returns decoded `reasons` / `javdb_tags` values, so the
  router adapter consumes list-valued `reasons` from repo rows. Tests may still
  include raw `reasons_json` fixtures only to prove the adapter tolerates older
  row shapes.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `apps/api/schemas/quality.py` | Pydantic response models for evidence + evaluation. |
| Create | `apps/api/routers/quality.py` | Read-only `/api/quality` endpoints. |
| Modify | `apps/api/services/runtime.py` | Import + register the quality router. |
| Create | `tests/unit/test_quality_api.py` | Endpoint response + auth tests. |

## Scope Boundaries

- Read-only: no POST/PUT/DELETE in this IMP.
- No TypeScript backend changes (separate repo; out of scope per ADR-018 note above).
- Reuse the existing `_require_auth` dependency; do not invent new auth.
- Reuse `TorrentQualityRepo` read methods from IMP-02; do not add new SQL here
  beyond what the repo already exposes (add a repo method if a new query is needed).

---

## Task 1 — Response schemas

**Files:**
- Create: `apps/api/schemas/quality.py`

- [ ] **Step 1: Create the schemas**

Create `apps/api/schemas/quality.py` (mirrors `apps/api/schemas/diagnostics.py` style):

```python
"""Schemas for /api/quality/* torrent-quality endpoints (ADR-024)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TorrentQualityEvidenceSchema(BaseModel):
    """Torrent-level objective evidence."""

    info_hash: str
    probe_schema_version: str
    target_role: str
    probe_target_name: Optional[str] = None
    metadata_status: Optional[str] = None
    total_size_bytes: Optional[int] = None
    main_video_size_bytes: Optional[int] = None
    main_video_ratio: Optional[float] = None
    video_file_count: Optional[int] = None
    subtitle_file_count: Optional[int] = None
    non_video_file_count: Optional[int] = None
    junk_size_bytes: Optional[int] = None
    junk_size_ratio: Optional[float] = None
    suspicious_file_count: Optional[int] = None
    reasons: list[str] = []


class TorrentQualityEvaluationSchema(BaseModel):
    """Movie-context shadow evaluation."""

    info_hash: str
    movie_href: str
    scoring_version: str
    video_code: Optional[str] = None
    javdb_category: Optional[str] = None
    magnet_name: Optional[str] = None
    inferred_category: Optional[str] = None
    category_consistent: Optional[bool] = None
    subtitle_evidence: Optional[str] = None
    score: Optional[float] = None
    shadow_rank: Optional[int] = None
    would_replace_current_choice: Optional[bool] = None
    policy_mode: Optional[str] = None
    decision: Optional[str] = None
    reasons: list[str] = []


class TorrentQualityEvaluationListResponse(BaseModel):
    items: list[TorrentQualityEvaluationSchema]


__all__ = [
    "TorrentQualityEvidenceSchema",
    "TorrentQualityEvaluationSchema",
    "TorrentQualityEvaluationListResponse",
]
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/schemas/quality.py
git commit -m "feat(api): add torrent quality response schemas (ADR-024)"
```

---

## Task 2 — Router

**Files:**
- Create: `apps/api/routers/quality.py`
- Test: `tests/unit/test_quality_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_quality_api.py`:

```python
"""Tests for /api/quality endpoints (ADR-024)."""

from __future__ import annotations

import json
from contextlib import nullcontext

from apps.api.routers import quality as quality_router


class _FakeRepo:
    def list_recent_evaluations(self, *, limit=50):
        return [self._eval_row()]

    def list_evaluations_for_movie(self, movie_href):
        return [self._eval_row()] if movie_href == "/v/abc" else []

    def get_evidence(self, info_hash, probe_schema_version, target_role):
        if info_hash == "HASH1":
            return {
                "info_hash": "HASH1",
                "probe_schema_version": "adr024-probe-v1",
                "target_role": "production_download",
                "probe_target_name": "production",
                "metadata_status": "metadata_received",
                "total_size_bytes": 100,
                "main_video_size_bytes": 90,
                "main_video_ratio": 0.9,
                "video_file_count": 1,
                "subtitle_file_count": 0,
                "non_video_file_count": 0,
                "junk_size_bytes": 0,
                "junk_size_ratio": 0.0,
                "suspicious_file_count": 0,
                "reasons_json": json.dumps(["main_video_detected"]),
            }
        return None

    @staticmethod
    def _eval_row():
        return {
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
            "reasons_json": json.dumps(["main_video_detected", "subtitle_file_present"]),
        }


def test_list_recent_evaluations(monkeypatch):
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(_FakeRepo()))
    resp = quality_router.list_evaluations(limit=10, movie_href=None, _user={"role": "admin"})
    assert len(resp.items) == 1
    assert resp.items[0].decision == "accepted_shadow"
    assert resp.items[0].category_consistent is True
    assert "subtitle_file_present" in resp.items[0].reasons


def test_list_evaluations_for_movie(monkeypatch):
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(_FakeRepo()))
    resp = quality_router.list_evaluations(limit=10, movie_href="/v/abc", _user={"role": "admin"})
    assert len(resp.items) == 1


def test_get_evidence_found(monkeypatch):
    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(_FakeRepo()))
    out = quality_router.get_evidence(info_hash="HASH1", _user={"role": "admin"})
    assert out.info_hash == "HASH1"
    assert out.main_video_ratio == 0.9
    assert "main_video_detected" in out.reasons


def test_get_evidence_missing_raises_404(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.setattr(quality_router, "_repo", lambda: nullcontext(_FakeRepo()))
    with pytest.raises(HTTPException) as exc:
        quality_router.get_evidence(info_hash="NOPE", _user={"role": "admin"})
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_quality_api.py -v
```

Expected: FAIL with `ModuleNotFoundError: apps.api.routers.quality`.

- [ ] **Step 3: Implement the router**

Create `apps/api/routers/quality.py` (mirrors `apps/api/routers/diagnostics.py`):

```python
"""Torrent quality evidence read endpoints (ADR-024 Phase 1, read-only).

GET /api/quality/evaluations            — recent shadow evaluations (optional movie_href filter)
GET /api/quality/evidence/{info_hash}   — torrent-level objective evidence
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import _require_auth
from apps.api.schemas.quality import (
    TorrentQualityEvaluationListResponse,
    TorrentQualityEvaluationSchema,
    TorrentQualityEvidenceSchema,
)
from javdb.quality.features import PROBE_SCHEMA_VERSION
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

router = APIRouter(prefix="/api/quality", tags=["quality"])

_PRODUCTION_ROLE = "production_download"


@contextmanager
def _repo() -> Iterator[TorrentQualityRepo]:
    """Yield a conn-injected repo for one request (mirrors the diagnostics
    router's per-request ``with get_db(...)``)."""
    with get_db(REPORTS_DB_PATH) as conn:
        yield TorrentQualityRepo(conn)


def _json_list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _eval_to_schema(row: dict) -> TorrentQualityEvaluationSchema:
    return TorrentQualityEvaluationSchema(
        info_hash=row["info_hash"],
        movie_href=row["movie_href"],
        scoring_version=row["scoring_version"],
        video_code=row.get("video_code"),
        javdb_category=row.get("javdb_category"),
        magnet_name=row.get("magnet_name"),
        inferred_category=row.get("inferred_category"),
        category_consistent=_bool_or_none(row.get("category_consistent")),
        subtitle_evidence=row.get("subtitle_evidence"),
        score=row.get("score"),
        shadow_rank=row.get("shadow_rank"),
        would_replace_current_choice=_bool_or_none(row.get("would_replace_current_choice")),
        policy_mode=row.get("policy_mode"),
        decision=row.get("decision"),
        reasons=_json_list(row.get("reasons_json")),
    )


def _evidence_to_schema(row: dict) -> TorrentQualityEvidenceSchema:
    return TorrentQualityEvidenceSchema(
        info_hash=row["info_hash"],
        probe_schema_version=row["probe_schema_version"],
        target_role=row["target_role"],
        probe_target_name=row.get("probe_target_name"),
        metadata_status=row.get("metadata_status"),
        total_size_bytes=row.get("total_size_bytes"),
        main_video_size_bytes=row.get("main_video_size_bytes"),
        main_video_ratio=row.get("main_video_ratio"),
        video_file_count=row.get("video_file_count"),
        subtitle_file_count=row.get("subtitle_file_count"),
        non_video_file_count=row.get("non_video_file_count"),
        junk_size_bytes=row.get("junk_size_bytes"),
        junk_size_ratio=row.get("junk_size_ratio"),
        suspicious_file_count=row.get("suspicious_file_count"),
        reasons=_json_list(row.get("reasons_json")),
    )


@router.get("/evaluations", response_model=TorrentQualityEvaluationListResponse)
def list_evaluations(
    limit: int = 50,
    movie_href: Optional[str] = None,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> TorrentQualityEvaluationListResponse:
    """List recent shadow evaluations, optionally filtered by movie_href."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be a positive integer")
    with _repo() as repo:
        if movie_href:
            rows = repo.list_evaluations_for_movie(movie_href)
        else:
            rows = repo.list_recent_evaluations(limit=min(limit, 200))
    return TorrentQualityEvaluationListResponse(
        items=[_eval_to_schema(r) for r in rows]
    )


@router.get("/evidence/{info_hash}", response_model=TorrentQualityEvidenceSchema)
def get_evidence(
    info_hash: str,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> TorrentQualityEvidenceSchema:
    """Return torrent-level evidence for the production-download role."""
    with _repo() as repo:
        row = repo.get_evidence(info_hash, PROBE_SCHEMA_VERSION, _PRODUCTION_ROLE)
    if row is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return _evidence_to_schema(row)


__all__ = ["get_evidence", "list_evaluations", "router"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_quality_api.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/quality.py tests/unit/test_quality_api.py
git commit -m "feat(api): add read-only torrent quality endpoints (ADR-024)"
```

---

## Task 3 — Register the router

**Files:**
- Modify: `apps/api/services/runtime.py`

- [ ] **Step 1: Add the import**

In `apps/api/services/runtime.py`, near the other router imports (around line 54,
next to `from apps.api.routers.preferences import router as preferences_router`),
add:

```python
from apps.api.routers.quality import router as quality_router
```

- [ ] **Step 2: Add to the registration tuple**

In the `for router in ( ... ):` block (lines ~170-188), add `quality_router,`
before the closing `)` (e.g. after `preferences_router,`):

```python
    preferences_router,
    quality_router,
):
    app.include_router(router)
```

- [ ] **Step 3: Verify the app imports and the routes register**

Run:

```bash
python3 -c "
from apps.api.services.runtime import app
paths = {r.path for r in app.routes}
assert '/api/quality/evaluations' in paths, sorted(p for p in paths if 'quality' in p)
assert '/api/quality/evidence/{info_hash}' in paths
print('routes ok')
"
```

Expected: `routes ok`.

- [ ] **Step 4: Commit**

```bash
git add apps/api/services/runtime.py
git commit -m "feat(api): register torrent quality router (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Endpoints respond | `pytest tests/unit/test_quality_api.py -v` → PASS |
| 2 | 404 on missing evidence | covered by `test_get_evidence_missing_raises_404` |
| 3 | Auth-gated | endpoints depend on `_require_auth` (same as diagnostics) |
| 4 | Routes registered | Task 3 Step 3 prints `routes ok` |
| 5 | Read-only | `rg -n "@router.(post\\|put\\|delete)" apps/api/routers/quality.py` → no output |
