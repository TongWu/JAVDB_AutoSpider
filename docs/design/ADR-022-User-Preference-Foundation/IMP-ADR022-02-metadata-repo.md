# ADR-022 Phase 2 — MetadataRepo + Spider Wiring

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `MetadataRepo`, extend `parse_detail()` to surface the `MovieDetail` object, wire a UPSERT call into `persist_parsed_detail_result()`, and expose a GET endpoint for movie metadata.

**Architecture:** `MetadataRepo` follows the existing stateless repo pattern (`__init__(*, db_path=None)`). The detail runner calls `MetadataRepo().upsert()` after `save_parsed_movie_to_history()`, outside the session flow. Write failures are silent and retriable. `parse_detail()` returns a 7-tuple (appending `MovieDetail`) so callers that need it can access it without breaking callers that don't.

**Tech Stack:** Python 3.11, FastAPI, SQLite/D1 via `get_db()` context manager.

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-01](IMP-ADR022-01-db-schema.md) · [IMP-ADR022-03](IMP-ADR022-03-preference-repo.md)

**Depends on:** IMP-ADR022-01 (tables must exist).

**Blocks:** IMP-ADR022-07 (tests run against this repo).

---

## Task 1 — Create MetadataRepo

**Files:**
- Create: `javdb/storage/repos/metadata_repo.py`

- [ ] **Step 1: Create the file**

```python
"""Repository for MovieMetadata table (ADR-022)."""

from __future__ import annotations

import json
import re
from typing import Optional

from javdb.storage.db import get_db, HISTORY_DB_PATH


class MetadataRepo:
    """Thin typed wrapper over MovieMetadata in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or HISTORY_DB_PATH

    def upsert(self, href: str, detail: dict) -> None:
        """UPSERT a MovieDetail dict into MovieMetadata.

        ``detail`` is MovieDetail.__dict__ or an equivalent mapping.
        Keys match MovieDetail field names (snake_case).
        """
        def _link(obj) -> Optional[str]:
            if obj is None:
                return None
            if hasattr(obj, 'name') and hasattr(obj, 'href'):
                return json.dumps({'name': obj.name, 'href': obj.href})
            return json.dumps(obj)

        def _links(lst) -> Optional[str]:
            if not lst:
                return None
            return json.dumps([{'name': x.name, 'href': x.href} for x in lst])

        def _urls(lst) -> Optional[str]:
            if not lst:
                return None
            return json.dumps(list(lst))

        def _duration(s: Optional[str]) -> Optional[int]:
            if not s:
                return None
            m = re.search(r'(\d+)', str(s))
            return int(m.group(1)) if m else None

        def _float(s) -> Optional[float]:
            if s is None:
                return None
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        def _int(s) -> Optional[int]:
            if s is None:
                return None
            try:
                return int(s)
            except (ValueError, TypeError):
                return None

        sql = """
            INSERT INTO MovieMetadata (
                href, title, video_code, release_date, duration_minutes,
                rate, comment_count, review_count, want_count, watched_count,
                maker, publisher, series, directors, categories,
                poster_url, fanart_urls, trailer_url,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                strftime('%Y-%m-%dT%H:%M:%fZ','now')
            )
            ON CONFLICT(href) DO UPDATE SET
                title             = excluded.title,
                video_code        = excluded.video_code,
                release_date      = excluded.release_date,
                duration_minutes  = excluded.duration_minutes,
                rate              = excluded.rate,
                comment_count     = excluded.comment_count,
                review_count      = excluded.review_count,
                want_count        = excluded.want_count,
                watched_count     = excluded.watched_count,
                maker             = excluded.maker,
                publisher         = excluded.publisher,
                series            = excluded.series,
                directors         = excluded.directors,
                categories        = excluded.categories,
                poster_url        = excluded.poster_url,
                fanart_urls       = excluded.fanart_urls,
                trailer_url       = excluded.trailer_url,
                updated_at        = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """
        params = (
            href,
            detail.get('title'),
            detail.get('video_code'),
            detail.get('release_date'),
            _duration(detail.get('duration')),
            _float(detail.get('rate')),
            _int(detail.get('comment_count')),
            _int(detail.get('review_count')),
            _int(detail.get('want_count')),
            _int(detail.get('watched_count')),
            _link(detail.get('maker')),
            _link(detail.get('publisher')),
            _link(detail.get('series')),
            _links(detail.get('directors')),
            _links(detail.get('tags')),      # MovieDetail.tags = categories
            detail.get('poster_url'),
            _urls(detail.get('fanart_urls')),
            detail.get('trailer_url'),
        )
        with get_db(self._db_path) as conn:
            conn.execute(sql, params)

    def get(self, href: str) -> Optional[dict]:
        """Return the MovieMetadata row for *href*, or None."""
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM MovieMetadata WHERE href = ?", (href,)
            ).fetchone()
        return dict(row) if row is not None else None
```

- [ ] **Step 2: Verify import works**

```bash
python3 -c "from javdb.storage.repos.metadata_repo import MetadataRepo; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add javdb/storage/repos/metadata_repo.py
git commit -m "feat(storage): add MetadataRepo for MovieMetadata (ADR-022)"
```

---

## Task 2 — Surface the MovieDetail object for metadata upsert

> **⚠ Divergence note (recorded during implementation, 2026-05-30).**
> This task's original plan assumed a `parse_detail()` function returning a
> 6-tuple in `javdb/spider/parse_legacy_adapters.py`. **That file and function
> do not exist in the current codebase.** The real detail parser is
> `parse_detail_page(html) -> MovieDetail` (`javdb.parsing`). Two paths consume
> it:
> - **Parallel mode** (production default, proxy pool): `_spider_parse_fn()` in
>   `javdb/spider/detail/parallel_mode.py` builds the per-entry `data` dict.
> - **Sequential mode** (`--no-proxy` fallback): `SequentialFetchBackend` →
>   `fetch_detail_page_with_fallback()` → `_parse_detail_to_tuple()` in
>   `javdb/spider/fetch/fallback.py` (the real "legacy 6-tuple" the plan meant).
>
> **What was implemented:** `_spider_parse_fn()` now adds `'movie_detail': detail`
> to its returned dict; `process_detail_entries()` forwards
> `data.get('movie_detail')` to `persist_parsed_detail_result()` (Task 3). This
> captures metadata for the **parallel path** (the production default).
>
> **Descoped:** Threading `MovieDetail` through `fetch_detail_page_with_fallback()`
> (15+ return sites across nested helpers, plus `javdb/legacy/` and
> `javdb/migrations/` callers) was judged a disproportionate blast radius on a
> critical fetch path. The **sequential/fallback path does not capture metadata
> live**; this gap is covered by the [IMP-ADR022-08](IMP-ADR022-08-metadata-backfill.md)
> backfill tool, which fills `MovieMetadata` for any `MovieHistory` row lacking
> it. Follow-up: revisit live sequential capture if backfill proves insufficient.

The steps below are retained for historical reference; they describe the
non-existent `parse_detail()` shape and were superseded by the divergence note.

**Files:**
- Modify: `javdb/spider/parse_legacy_adapters.py`

`parse_detail()` currently returns a 6-tuple. We append `detail` (the `MovieDetail` object) as a 7th element. All callers are updated to unpack 7 values.

- [ ] **Step 1: Update `parse_detail()` return value**

In `javdb/spider/parse_legacy_adapters.py`, locate the `parse_detail` function (line ~71). Change the final `return` statement from:

```python
    return magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success
```

to:

```python
    return magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success, detail
```

- [ ] **Step 2: Find all callers that need updating**

```bash
grep -rn "parse_detail(" javdb/spider/ apps/ --include="*.py" | grep -v "def parse_detail"
```

Expected call sites (excluding `javdb/legacy/` and `javdb/migrations/`):
- `javdb/spider/detail/parallel_mode.py`
- `javdb/spider/fetch/fallback.py`
- `apps/cli/ops/profile_hot_paths.py`

- [ ] **Step 3: Update `javdb/spider/detail/parallel_mode.py`**

Locate the unpack line (~line 47):
```python
    magnets, actor_info, actor_gender, actor_link, supporting, ok = (
        parse_detail(html, task.entry_index, skip_sleep=True)
    )
```

Change to:
```python
    magnets, actor_info, actor_gender, actor_link, supporting, ok, movie_detail = (
        parse_detail(html, task.entry_index, skip_sleep=True)
    )
```

Then add `movie_detail` to the returned dict:
```python
    return {
        'magnets': magnets,
        'actor_info': actor_info,
        'actor_gender': actor_gender or '',
        'actor_link': actor_link or '',
        'supporting': supporting or '',
        'movie_detail': movie_detail,
    }
```

- [ ] **Step 4: Update `javdb/spider/fetch/fallback.py`**

Find all lines that unpack `parse_detail(...)` (there are two, ~lines 420 and 432). For each, add `_detail` as the 7th unpack variable:

```python
m = parse_detail(html, entry_index, skip_sleep=skip_sleep)
# becomes:
magnets, actor_info, actor_gender, actor_link, supporting, ok, _detail = \
    parse_detail(html, entry_index, skip_sleep=skip_sleep)
```

Match the existing variable names already used in that function body.

- [ ] **Step 5: Update `apps/cli/ops/profile_hot_paths.py`**

Find the call (~line 162):
```python
lambda: parse_detail(html, index=1, skip_sleep=True),
```

This is a lambda used for profiling — the return value is measured but not unpacked. No change needed here.

- [ ] **Step 6: Verify no remaining 6-tuple unpack errors**

```bash
python3 -c "
from javdb.spider.parse_legacy_adapters import parse_detail
# parse_detail returns 7-tuple; verify length with a minimal HTML stub
result = parse_detail('<html></html>')
assert len(result) == 7, f'Expected 7, got {len(result)}'
print('OK')
"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add javdb/spider/parse_legacy_adapters.py \
        javdb/spider/detail/parallel_mode.py \
        javdb/spider/fetch/fallback.py
git commit -m "feat(spider): extend parse_detail to return MovieDetail object (ADR-022)"
```

---

## Task 3 — Wire MetadataRepo into persist_parsed_detail_result()

**Files:**
- Modify: `javdb/spider/detail/runner.py`

- [ ] **Step 1: Add import at top of file**

In `javdb/spider/detail/runner.py`, add to the existing imports block:

```python
from javdb.storage.repos.metadata_repo import MetadataRepo
```

- [ ] **Step 2: Add UPSERT call after save_parsed_movie_to_history()**

In `persist_parsed_detail_result()`, locate the block ending at line ~1004:

```python
            save_parsed_movie_to_history(
                history_file,
                href,
                ...
                supporting_actors=supporting_actors,
            )
```

Immediately after that closing parenthesis, add:

```python
        # ADR-022: persist rich metadata outside the session flow
        movie_detail = entry.get('movie_detail')
        if movie_detail is not None and not dry_run:
            try:
                MetadataRepo().upsert(href, movie_detail.__dict__)
            except Exception:
                logger.debug(
                    "MovieMetadata upsert failed for %s — will retry on next scrape",
                    href,
                    exc_info=True,
                )
```

- [ ] **Step 3: Verify spider dry-run completes cleanly**

```bash
python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 1 --no-proxy
```

Expected: completes without errors; no `MovieMetadata upsert failed` in logs.

- [ ] **Step 4: Commit**

```bash
git add javdb/spider/detail/runner.py
git commit -m "feat(spider): wire MovieMetadata upsert into detail runner (ADR-022)"
```

---

## Task 4 — FastAPI GET /api/preferences/metadata/{href}

**Files:**
- Create: `apps/api/schemas/preferences.py`
- Create: `apps/api/routers/preferences.py` (metadata section only — full CRUD added in IMP-ADR022-03)
- Modify: `apps/api/services/runtime.py`

- [ ] **Step 1: Create `apps/api/schemas/preferences.py`**

```python
"""Pydantic schemas for preferences and metadata endpoints (ADR-022)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MovieMetadataResponse(BaseModel):
    href: str
    title: Optional[str] = None
    video_code: Optional[str] = None
    release_date: Optional[str] = None
    duration_minutes: Optional[int] = None
    rate: Optional[float] = None
    comment_count: Optional[int] = None
    review_count: Optional[int] = None
    want_count: Optional[int] = None
    watched_count: Optional[int] = None
    maker: Optional[Dict[str, str]] = None
    publisher: Optional[Dict[str, str]] = None
    series: Optional[Dict[str, str]] = None
    directors: Optional[List[Dict[str, str]]] = None
    categories: Optional[List[Dict[str, str]]] = None
    poster_url: Optional[str] = None
    fanart_urls: Optional[List[str]] = None
    trailer_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# Rating and preference schemas are added in IMP-ADR022-03.
```

- [ ] **Step 2: Create `apps/api/routers/preferences.py`**

```python
"""Preferences and metadata API routes (ADR-022)."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import _require_auth
from apps.api.schemas.preferences import MovieMetadataResponse
from javdb.storage.repos.metadata_repo import MetadataRepo

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

_NOT_FOUND = {
    "error": {"code": "preferences.not_found", "message": "Record not found"}
}


def _parse_json_field(value: Optional[str]):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _row_to_metadata(row: dict) -> MovieMetadataResponse:
    return MovieMetadataResponse(
        href=row["href"],
        title=row.get("title"),
        video_code=row.get("video_code"),
        release_date=row.get("release_date"),
        duration_minutes=row.get("duration_minutes"),
        rate=row.get("rate"),
        comment_count=row.get("comment_count"),
        review_count=row.get("review_count"),
        want_count=row.get("want_count"),
        watched_count=row.get("watched_count"),
        maker=_parse_json_field(row.get("maker")),
        publisher=_parse_json_field(row.get("publisher")),
        series=_parse_json_field(row.get("series")),
        directors=_parse_json_field(row.get("directors")),
        categories=_parse_json_field(row.get("categories")),
        poster_url=row.get("poster_url"),
        fanart_urls=_parse_json_field(row.get("fanart_urls")),
        trailer_url=row.get("trailer_url"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.get("/metadata/{href:path}", response_model=MovieMetadataResponse)
def get_movie_metadata(href: str, _user=Depends(_require_auth)):
    row = MetadataRepo().get(href)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_metadata(row)
```

- [ ] **Step 3: Register the router in `apps/api/services/runtime.py`**

Add the import:
```python
from apps.api.routers.preferences import router as preferences_router
```

Add `preferences_router` to the `for router in (...)` loop alongside existing routers.

- [ ] **Step 4: Start API and verify endpoint**

```bash
python3 -m uvicorn apps.api.server:app --reload --host 127.0.0.1 --port 8100
```

In a second terminal:
```bash
curl -s http://127.0.0.1:8100/openapi.json | python3 -c "
import json, sys
spec = json.load(sys.stdin)
paths = [p for p in spec['paths'] if 'metadata' in p]
print(paths)
"
```

Expected: `['/api/preferences/metadata/{href}']`

- [ ] **Step 5: Commit**

```bash
git add apps/api/schemas/preferences.py \
        apps/api/routers/preferences.py \
        apps/api/services/runtime.py
git commit -m "feat(api): add MovieMetadata GET endpoint (ADR-022)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | `MetadataRepo` imports cleanly | `python3 -c "from javdb.storage.repos.metadata_repo import MetadataRepo; print('OK')"` → `OK` |
| 2 | Parallel path surfaces `movie_detail` | `_spider_parse_fn()` returns a dict containing `movie_detail`; `process_detail_entries()` forwards it to `persist_parsed_detail_result()` (see Task 2 divergence note). Sequential path descoped → covered by IMP-ADR022-08. |
| 3 | Spider dry-run produces no metadata errors | `python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 1 --no-proxy` → no `MovieMetadata upsert failed` |
| 4 | Metadata endpoint in OpenAPI spec | `/api/preferences/metadata/{href}` present in `/openapi.json` |
| 5 | MetadataRepo unit tests pass | `pytest tests/unit/test_metadata_repo.py -v` → all PASS (tests written in IMP-ADR022-07) |
