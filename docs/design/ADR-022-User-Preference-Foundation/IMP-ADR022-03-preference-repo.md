# ADR-022 Phase 3 — PreferenceRepo + Python CRUD API

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `PreferenceRepo`, the predefined tag vocabulary constant, and FastAPI CRUD endpoints for `MovieRatings` and `ContentPreferences`.

**Architecture:** `PreferenceRepo` is a stateless repo (same pattern as `MetadataRepo`). Tag validation uses a `frozenset` constant. Five FastAPI endpoints are added to the existing `preferences` router created in IMP-ADR022-02.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite/D1 via `get_db()`.

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-01](IMP-ADR022-01-db-schema.md) · [IMP-ADR022-02](IMP-ADR022-02-metadata-repo.md) · [IMP-ADR022-04](IMP-ADR022-04-upload-gate.md) · [IMP-ADR022-05](IMP-ADR022-05-typescript-sync.md)

**Depends on:** IMP-ADR022-01 (tables), IMP-ADR022-02 (router + schema files exist).

**Blocks:** IMP-ADR022-04, IMP-ADR022-05, IMP-ADR022-06.

---

## Task 1 — Predefined tag vocabulary

**Files:**
- Create: `javdb/storage/preference_tags.py`

- [ ] **Step 1: Create the file**

```python
"""Predefined tag vocabulary for MovieRatings (ADR-022)."""

VALID_TAGS: frozenset[str] = frozenset({
    # Quality / Technical
    "quality_high",
    "quality_low",
    "resolution_bad",
    "encoding_bad",
    # Content preference
    "plot_good",
    "actress_standout",
    "not_my_type",
    "category_miss",
    # Collection / Decision
    "would_rewatch",
    "keep_long_term",
    "delete_candidate",
    "upgrade_wanted",
})

TAG_GROUPS: dict[str, list[str]] = {
    "quality":    ["quality_high", "quality_low", "resolution_bad", "encoding_bad"],
    "content":    ["plot_good", "actress_standout", "not_my_type", "category_miss"],
    "collection": ["would_rewatch", "keep_long_term", "delete_candidate", "upgrade_wanted"],
}
```

- [ ] **Step 2: Verify import**

```bash
python3 -c "from javdb.storage.preference_tags import VALID_TAGS; print(len(VALID_TAGS), 'tags')"
```

Expected: `12 tags`

- [ ] **Step 3: Commit**

```bash
git add javdb/storage/preference_tags.py
git commit -m "feat(storage): add predefined tag vocabulary (ADR-022)"
```

---

## Task 2 — Create PreferenceRepo

**Files:**
- Create: `javdb/storage/repos/preference_repo.py`

- [ ] **Step 1: Create the file**

```python
"""Repository for MovieRatings and ContentPreferences tables (ADR-022)."""

from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from javdb.storage.db import get_db, HISTORY_DB_PATH


class PreferenceRepo:
    """Typed wrapper over MovieRatings and ContentPreferences in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or HISTORY_DB_PATH

    # ------------------------------------------------------------------
    # MovieRatings
    # ------------------------------------------------------------------

    def upsert_rating(
        self,
        *,
        href: str,
        rating: Optional[int],
        tags: List[str],
        notes: Optional[str],
    ) -> dict:
        """UPSERT a movie rating. Returns the updated row as a dict."""
        video_code = re.sub(r'^/video/', '', href).strip('/')

        sql = """
            INSERT INTO MovieRatings
                (href, video_code, rating, tags, notes, rated_at, updated_at)
            VALUES (?, ?, ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(href) DO UPDATE SET
                rating     = excluded.rating,
                tags       = excluded.tags,
                notes      = excluded.notes,
                rated_at   = CASE WHEN excluded.rating IS NOT NULL
                                  THEN strftime('%Y-%m-%dT%H:%M:%fZ','now')
                                  ELSE rated_at END,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """
        with get_db(self._db_path) as conn:
            conn.execute(sql, (href, video_code, rating, json.dumps(tags), notes))
            row = conn.execute(
                "SELECT * FROM MovieRatings WHERE href = ?", (href,)
            ).fetchone()
        return dict(row)

    def get_rating(self, href: str) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM MovieRatings WHERE href = ?", (href,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_ratings(
        self, *, limit: int = 50, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count) for paginated listing."""
        with get_db(self._db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM MovieRatings"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM MovieRatings ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    # ------------------------------------------------------------------
    # ContentPreferences
    # ------------------------------------------------------------------

    def upsert_preference(
        self,
        *,
        content_type: str,
        content_id: str,
        content_name: str,
        hearted: bool,
        weight: float = 1.0,
    ) -> dict:
        """UPSERT a content preference. Returns the updated row as a dict."""
        sql = """
            INSERT INTO ContentPreferences
                (content_type, content_id, content_name, hearted, weight, updated_at)
            VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(content_type, content_id) DO UPDATE SET
                content_name = excluded.content_name,
                hearted      = excluded.hearted,
                weight       = excluded.weight,
                updated_at   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """
        with get_db(self._db_path) as conn:
            conn.execute(sql, (
                content_type, content_id, content_name,
                1 if hearted else 0, weight,
            ))
            row = conn.execute(
                "SELECT * FROM ContentPreferences WHERE content_type=? AND content_id=?",
                (content_type, content_id),
            ).fetchone()
        return dict(row)

    def get_preference(
        self, content_type: str, content_id: str
    ) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM ContentPreferences WHERE content_type=? AND content_id=?",
                (content_type, content_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_preferences(
        self,
        *,
        content_type: Optional[str] = None,
        hearted_only: bool = False,
    ) -> List[dict]:
        conditions: list[str] = []
        params: list = []
        if content_type:
            conditions.append("content_type = ?")
            params.append(content_type)
        if hearted_only:
            conditions.append("hearted = 1")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            f"SELECT * FROM ContentPreferences {where} "
            "ORDER BY content_type, content_name"
        )
        with get_db(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def is_actor_blocked(self, actor_href: str) -> bool:
        """True if the actor has an explicit hearted=0 ContentPreferences entry."""
        row = self.get_preference('actor', actor_href)
        return row is not None and row['hearted'] == 0
```

- [ ] **Step 2: Verify import**

```bash
python3 -c "from javdb.storage.repos.preference_repo import PreferenceRepo; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add javdb/storage/repos/preference_repo.py
git commit -m "feat(storage): add PreferenceRepo for ratings and preferences (ADR-022)"
```

---

## Task 3 — Extend Pydantic schemas

**Files:**
- Modify: `apps/api/schemas/preferences.py`

- [ ] **Step 1: Add rating and preference schemas**

Append to `apps/api/schemas/preferences.py` (after `MovieMetadataResponse`):

```python
class MovieRatingUpsert(BaseModel):
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class MovieRatingResponse(BaseModel):
    href: str
    video_code: str
    rating: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    rated_at: Optional[str] = None
    updated_at: Optional[str] = None


class MovieRatingListResponse(BaseModel):
    items: List[MovieRatingResponse]
    total: int


class ContentPreferenceUpsert(BaseModel):
    content_name: str
    hearted: bool = False
    weight: float = Field(default=1.0, ge=0.0, le=10.0)


class ContentPreferenceResponse(BaseModel):
    content_type: str
    content_id: str
    content_name: str
    hearted: bool
    weight: float
    updated_at: Optional[str] = None


class ContentPreferenceListResponse(BaseModel):
    items: List[ContentPreferenceResponse]
```

- [ ] **Step 2: Remove the placeholder comment at the bottom of the schemas file**

Delete the line: `# Rating and preference schemas are added in IMP-ADR022-03.`

- [ ] **Step 3: Commit**

```bash
git add apps/api/schemas/preferences.py
git commit -m "feat(api): add rating and preference Pydantic schemas (ADR-022)"
```

---

## Task 4 — Add CRUD endpoints to preferences router

**Files:**
- Modify: `apps/api/routers/preferences.py`

- [ ] **Step 1: Add imports**

At the top of `apps/api/routers/preferences.py`, add to the existing imports:

```python
from apps.api.schemas.preferences import (
    ContentPreferenceListResponse,
    ContentPreferenceResponse,
    ContentPreferenceUpsert,
    MovieRatingListResponse,
    MovieRatingResponse,
    MovieRatingUpsert,
)
from javdb.storage.repos.preference_repo import PreferenceRepo
from javdb.storage.preference_tags import VALID_TAGS
```

- [ ] **Step 2: Add constants**

After the existing `_NOT_FOUND` constant, add:

```python
_VALID_CONTENT_TYPES = {"actor", "category", "maker", "director"}
_INVALID_CONTENT_TYPE = {
    "error": {
        "code": "preferences.invalid_content_type",
        "message": "content_type must be one of: actor, category, maker, director",
    }
}
```

- [ ] **Step 3: Add row-to-model helpers**

After the existing `_row_to_metadata` helper, add:

```python
def _row_to_rating(row: dict) -> MovieRatingResponse:
    import json as _json
    return MovieRatingResponse(
        href=row["href"],
        video_code=row["video_code"],
        rating=row.get("rating"),
        tags=_parse_json_field(row.get("tags")) or [],
        notes=row.get("notes"),
        rated_at=row.get("rated_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_pref(row: dict) -> ContentPreferenceResponse:
    return ContentPreferenceResponse(
        content_type=row["content_type"],
        content_id=row["content_id"],
        content_name=row["content_name"],
        hearted=bool(row.get("hearted", 0)),
        weight=row.get("weight", 1.0),
        updated_at=row.get("updated_at"),
    )
```

- [ ] **Step 4: Add the five CRUD endpoints**

Append to `apps/api/routers/preferences.py`:

```python
@router.put("/movies/{href:path}/rating", response_model=MovieRatingResponse)
def upsert_movie_rating(
    href: str,
    body: MovieRatingUpsert,
    _user=Depends(_require_auth),
):
    invalid = [t for t in body.tags if t not in VALID_TAGS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "preferences.invalid_tags",
                    "message": f"Unknown tags: {invalid}. Valid: {sorted(VALID_TAGS)}",
                }
            },
        )
    row = PreferenceRepo().upsert_rating(
        href=href, rating=body.rating, tags=body.tags, notes=body.notes
    )
    return _row_to_rating(row)


@router.get("/movies/{href:path}/rating", response_model=MovieRatingResponse)
def get_movie_rating(href: str, _user=Depends(_require_auth)):
    row = PreferenceRepo().get_rating(href)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_rating(row)


@router.get("/movies/ratings", response_model=MovieRatingListResponse)
def list_movie_ratings(
    limit: int = 50,
    offset: int = 0,
    _user=Depends(_require_auth),
):
    items, total = PreferenceRepo().list_ratings(limit=limit, offset=offset)
    return MovieRatingListResponse(
        items=[_row_to_rating(r) for r in items], total=total
    )


@router.put("/{content_type}/{content_id:path}", response_model=ContentPreferenceResponse)
def upsert_content_preference(
    content_type: str,
    content_id: str,
    body: ContentPreferenceUpsert,
    _user=Depends(_require_auth),
):
    if content_type not in _VALID_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=_INVALID_CONTENT_TYPE)
    row = PreferenceRepo().upsert_preference(
        content_type=content_type,
        content_id=content_id,
        content_name=body.content_name,
        hearted=body.hearted,
        weight=body.weight,
    )
    return _row_to_pref(row)


@router.get("/", response_model=ContentPreferenceListResponse)
def list_content_preferences(
    content_type: Optional[str] = None,
    hearted_only: bool = False,
    _user=Depends(_require_auth),
):
    if content_type is not None and content_type not in _VALID_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=_INVALID_CONTENT_TYPE)
    items = PreferenceRepo().list_preferences(
        content_type=content_type, hearted_only=hearted_only,
    )
    return ContentPreferenceListResponse(items=[_row_to_pref(r) for r in items])
```

- [ ] **Step 5: Verify all endpoints appear in OpenAPI**

```bash
python3 -m uvicorn apps.api.server:app --reload --host 127.0.0.1 --port 8100 &
sleep 3
curl -s http://127.0.0.1:8100/openapi.json | python3 -c "
import json, sys
spec = json.load(sys.stdin)
pref_paths = sorted(p for p in spec['paths'] if 'preferences' in p)
for p in pref_paths: print(p)
"
```

Expected output (order may vary):
```
/api/preferences/
/api/preferences/metadata/{href}
/api/preferences/movies/ratings
/api/preferences/movies/{href}/rating
/api/preferences/{content_type}/{content_id}
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/routers/preferences.py
git commit -m "feat(api): add MovieRatings and ContentPreferences CRUD endpoints (ADR-022)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Tag vocabulary has 12 entries | `python3 -c "from javdb.storage.preference_tags import VALID_TAGS; assert len(VALID_TAGS)==12"` → no error |
| 2 | `PreferenceRepo` imports cleanly | `python3 -c "from javdb.storage.repos.preference_repo import PreferenceRepo; print('OK')"` → `OK` |
| 3 | All 5 preference endpoints in OpenAPI spec | 5 paths under `/api/preferences/` present in `/openapi.json` |
| 4 | Invalid tag rejected | `curl -X PUT .../movies/.../rating -d '{"tags":["fake_tag"]}'` → HTTP 422 |
| 5 | Invalid content_type rejected | `curl -X PUT .../invalid_type/foo -d '{...}'` → HTTP 422 |
| 6 | PreferenceRepo unit tests pass | `pytest tests/unit/test_preference_repo.py -v` → all PASS (written in IMP-ADR022-07) |
