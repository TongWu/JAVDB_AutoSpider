# IMP-ADR008-03: Frontend Rewrite — Phase 2: Full CLI Surface Coverage

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Data, Operations, Diagnostics, and GH Actions monitor pages — covering every CLI-expressible operation through the UI.

**Architecture:** See [ADR-008](ADR-008-frontend-rewrite-architecture.md) for all architectural decisions.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, httpx (GH Actions), Playwright, Vue 3.5, Naive UI, Vitest.

**Source spec:** `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md` §6.2 Phase 2, §8.4 Phase 2

**Related:** [ADR-008](ADR-008-frontend-rewrite-architecture.md), [IMP-ADR008-02](IMP-ADR008-02-frontend-phase1-completion.md) (Phase 1), [IMP-ADR008-04](IMP-ADR008-04-frontend-phase3-power-user.md) (Phase 3)

**Prerequisites:** Phase 1 cutover complete ([IMP-ADR008-02](IMP-ADR008-02-frontend-phase1-completion.md) Task 6 done). FE Docker image published. `apps/web/` deleted.

---

## Design Decisions (from grilling session, 2026-05-18)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| History search | BE-side SQL `LIKE` + cursor pagination | FTS overkill for 10K-scale tables; D1 has no FTS |
| CSV export | BE-side streaming endpoint | Operators expect full-dataset export, not current-page only |
| GH Actions integration | Direct `httpx` calls to GitHub REST API | Only 6-7 API calls needed; no library justified |
| GH Actions token | Reuse existing `GIT_PASSWORD` (PAT) | Already a PAT; split later if scope issues arise |
| PikPak endpoint | Batch mode only (`POST /api/ops/pikpak/transfer`) | Single-torrent transfer is edge case |
| Rclone endpoint | Single endpoint + flags (`POST /api/ops/rclone/run`) | FE offers "Quick dedup" preset + advanced flag toggles |
| Email history | New `EmailNotificationHistory` table in `operations.db` | Journey 12 requires "resend failed notification" |

---

## File Structure Overview

### Main repo — new/modified files

```
apps/api/routers/
├── history.py          # NEW — movies + torrents search + CSV export
├── operations.py       # NEW — qB, PikPak, Rclone, Email, Cleanup
├── diagnostics.py      # NEW — JavDB session status + refresh
└── gh_actions.py       # NEW — workflows, runs, dispatch, logs

apps/api/schemas/
├── history.py          # NEW — request/response models
├── operations.py       # NEW — request/response models
├── diagnostics.py      # NEW — request/response models
└── gh_actions.py       # NEW — request/response models

javdb/integrations/
├── notify/email.py     # MODIFY — append to EmailNotificationHistory after send
└── gh_actions/         # NEW — httpx GitHub REST API client
    ├── __init__.py
    └── client.py

javdb/storage/repos/
├── history_repo.py     # MODIFY — add search_movies(), search_torrents(), export_movies_csv(), export_torrents_csv()
└── operations_repo.py  # MODIFY — add email notification history CRUD

javdb/migrations/d1/
└── 0018_email_notification_history.sql  # NEW — D1 migration
```

### Web repo — new files

```
src/pages/
├── data/
│   ├── MoviesPage.vue
│   └── TorrentsPage.vue
├── operations/
│   ├── QBittorrentPage.vue
│   ├── PikPakPage.vue
│   ├── RclonePage.vue
│   ├── EmailPage.vue
│   └── CleanupPage.vue
├── diagnostics/
│   ├── HealthPage.vue
│   ├── ParseTesterPage.vue
│   └── JavdbSessionPage.vue
└── gh-actions/
    └── RunsPage.vue

src/api/
├── history.ts          # NEW — typed wrappers
├── operations.ts       # NEW
├── diagnostics.ts      # NEW
└── gh-actions.ts       # NEW

tests/e2e/
├── data-search.spec.ts
├── ops-qb.spec.ts
├── ops-pikpak.spec.ts
├── ops-rclone.spec.ts
├── ops-email.spec.ts
├── ops-cleanup.spec.ts
├── diag-parse-tester.spec.ts
├── diag-javdb-session.spec.ts
├── diag-health.spec.ts
└── gh-actions-monitor.spec.ts
```

---

# Part 1: Backend Endpoints (main repo)

## Task 1: History Search Endpoints

**Files:**
- Create: `apps/api/routers/history.py`
- Create: `apps/api/schemas/history.py`
- Modify: `javdb/storage/repos/history_repo.py`
- Modify: `apps/api/server.py` (register router)

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/history/movies` | Search MovieHistory with filters + cursor pagination |
| `GET` | `/api/history/torrents` | Search TorrentHistory with filters + cursor pagination |
| `GET` | `/api/history/movies/export` | Stream full-dataset CSV |
| `GET` | `/api/history/torrents/export` | Stream full-dataset CSV |

- [ ] **Step 1: Define Pydantic schemas.**

  ```python
  # apps/api/schemas/history.py
  from pydantic import BaseModel, Field
  from typing import Optional, List

  class MovieSearchParams(BaseModel):
      q: Optional[str] = None                     # LIKE search on VideoCode, ActorName, SupportingActors
      actor: Optional[str] = None                  # Exact match on ActorName
      perfect_match: Optional[bool] = None         # Filter PerfectMatchIndicator
      hi_res: Optional[bool] = None                # Filter HiResIndicator
      session_id: Optional[str] = None             # Filter by SessionId
      date_from: Optional[str] = None              # DateTimeCreated >= (ISO 8601)
      date_to: Optional[str] = None                # DateTimeCreated <= (ISO 8601)
      cursor: Optional[str] = None                 # Base64-encoded Id for keyset pagination
      limit: int = Field(default=50, ge=1, le=200)

  class MovieSearchItem(BaseModel):
      id: int
      video_code: str
      href: str
      actor_name: Optional[str]
      actor_gender: Optional[str]
      supporting_actors: Optional[str]
      perfect_match: bool
      hi_res: bool
      datetime_created: str
      datetime_updated: Optional[str]
      session_id: Optional[str]
      torrent_count: int                           # COUNT of joined TorrentHistory rows

  class MovieSearchResponse(BaseModel):
      items: List[MovieSearchItem]
      next_cursor: Optional[str]
      total_estimate: int

  class TorrentSearchParams(BaseModel):
      q: Optional[str] = None                     # LIKE on parent movie VideoCode
      resolution_type: Optional[int] = None        # 0=unknown, 1=SD, 2=HD, 3=FHD, 4=4K
      has_subtitle: Optional[bool] = None
      uncensored: Optional[bool] = None
      session_id: Optional[str] = None
      date_from: Optional[str] = None
      date_to: Optional[str] = None
      cursor: Optional[str] = None
      limit: int = Field(default=50, ge=1, le=200)

  class TorrentSearchItem(BaseModel):
      id: int
      movie_video_code: str                       # Joined from MovieHistory
      movie_href: str                             # Joined from MovieHistory
      magnet_uri: str
      size: Optional[str]
      subtitle_indicator: int
      censor_indicator: int
      resolution_type: int
      file_count: int
      datetime_created: str
      session_id: Optional[str]

  class TorrentSearchResponse(BaseModel):
      items: List[TorrentSearchItem]
      next_cursor: Optional[str]
      total_estimate: int
  ```

- [ ] **Step 2: Add search methods to HistoryRepo.**

  In `javdb/storage/repos/history_repo.py`, add:

  ```python
  def search_movies(self, q=None, actor=None, perfect_match=None, hi_res=None,
                    session_id=None, date_from=None, date_to=None,
                    cursor=None, limit=50) -> tuple[list[dict], str | None, int]:
      """Return (items, next_cursor, total_estimate).

      Search uses SQL LIKE on VideoCode, ActorName, SupportingActors.
      Pagination uses keyset on Id (WHERE Id > cursor_id ORDER BY Id LIMIT N+1).
      total_estimate from a separate COUNT(*) with same WHERE (capped at 10000).
      """
  ```

  Same pattern for `search_torrents()` with JOIN to MovieHistory for video_code/href.

  ```python
  def export_movies_csv(self, q=None, **filters) -> Iterator[str]:
      """Yield CSV rows (header first) matching filters. No pagination limit."""

  def export_torrents_csv(self, q=None, **filters) -> Iterator[str]:
      """Yield CSV rows with joined movie data."""
  ```

- [ ] **Step 3: Write unit tests for search methods.**

  ```python
  # tests/unit/test_history_search.py
  def test_search_movies_by_video_code(history_repo_with_seed):
      items, cursor, total = history_repo_with_seed.search_movies(q="ABC-123")
      assert len(items) == 1
      assert items[0]["VideoCode"] == "ABC-123"

  def test_search_movies_pagination(history_repo_with_seed):
      items1, cursor1, _ = history_repo_with_seed.search_movies(limit=2)
      assert len(items1) == 2
      assert cursor1 is not None
      items2, cursor2, _ = history_repo_with_seed.search_movies(cursor=cursor1, limit=2)
      assert items2[0]["Id"] > items1[-1]["Id"]

  def test_search_movies_filter_actor(history_repo_with_seed):
      items, _, _ = history_repo_with_seed.search_movies(actor="Test Actor")
      assert all(i["ActorName"] == "Test Actor" for i in items)

  def test_export_movies_csv(history_repo_with_seed):
      rows = list(history_repo_with_seed.export_movies_csv())
      assert rows[0].startswith("Id,VideoCode,")  # header
      assert len(rows) > 1
  ```

  Run: `pytest tests/unit/test_history_search.py -v`

- [ ] **Step 4: Implement router.**

  ```python
  # apps/api/routers/history.py
  from fastapi import APIRouter, Depends, Query
  from fastapi.responses import StreamingResponse
  from apps.api.schemas.history import *

  router = APIRouter(prefix="/api/history", tags=["history"])

  @router.get("/movies", response_model=MovieSearchResponse)
  async def search_movies(params: MovieSearchParams = Depends()):
      repo = HistoryRepo(get_db("history"))
      items, next_cursor, total = repo.search_movies(**params.model_dump(exclude_none=True))
      return MovieSearchResponse(items=items, next_cursor=next_cursor, total_estimate=total)

  @router.get("/movies/export")
  async def export_movies_csv(params: MovieSearchParams = Depends()):
      repo = HistoryRepo(get_db("history"))
      return StreamingResponse(
          repo.export_movies_csv(**params.model_dump(exclude={"cursor", "limit"}, exclude_none=True)),
          media_type="text/csv",
          headers={"Content-Disposition": "attachment; filename=movies.csv"},
      )

  # Same pattern for /torrents and /torrents/export
  ```

- [ ] **Step 5: Register router in server.py.**

  Add `from apps.api.routers.history import router as history_router` and `app.include_router(history_router)`.

- [ ] **Step 6: Write integration test for endpoints.**

  ```python
  # tests/integration/test_history_endpoints.py
  def test_search_movies_endpoint(test_client):
      resp = test_client.get("/api/history/movies", params={"q": "ABC", "limit": 10})
      assert resp.status_code == 200
      data = resp.json()
      assert "items" in data
      assert "next_cursor" in data

  def test_export_movies_csv_endpoint(test_client):
      resp = test_client.get("/api/history/movies/export")
      assert resp.status_code == 200
      assert resp.headers["content-type"].startswith("text/csv")
  ```

- [ ] **Step 7: Run all tests.**

  ```bash
  pytest tests/unit/test_history_search.py tests/integration/test_history_endpoints.py -v
  ```

- [ ] **Step 8: Commit.**

  ```bash
  git add apps/api/routers/history.py apps/api/schemas/history.py \
          javdb/storage/repos/history_repo.py apps/api/server.py tests/
  git commit -m "feat(api): add history search + CSV export endpoints (Phase 2)"
  ```

---

## Task 2: Email Notification History Table + Migration

**Files:**
- Create: `javdb/migrations/d1/0018_email_notification_history.sql`
- Modify: `javdb/storage/repos/operations_repo.py`
- Modify: `javdb/integrations/notify/email.py`

**Context:** New `EmailNotificationHistory` table for tracking sent notifications and enabling resend.

- [ ] **Step 1: Write D1 migration.**

  ```sql
  -- javdb/migrations/d1/0018_email_notification_history.sql
  CREATE TABLE IF NOT EXISTS EmailNotificationHistory (
      Id          INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId   TEXT,
      Recipient   TEXT NOT NULL,
      Subject     TEXT NOT NULL,
      Status      TEXT NOT NULL DEFAULT 'sent',  -- sent | failed | resent
      ErrorMessage TEXT,
      AttachmentNames TEXT,                       -- JSON array of filenames
      SentAt      TEXT NOT NULL,                  -- ISO 8601
      ResentAt    TEXT,
      CreatedBy   TEXT DEFAULT 'pipeline'         -- pipeline | manual | resend
  );

  CREATE INDEX IF NOT EXISTS idx_email_history_session ON EmailNotificationHistory(SessionId);
  CREATE INDEX IF NOT EXISTS idx_email_history_status ON EmailNotificationHistory(Status);
  ```

- [ ] **Step 2: Add CRUD to OperationsRepo.**

  ```python
  # In javdb/storage/repos/operations_repo.py — new methods:
  def append_email_history(self, session_id, recipient, subject, status, error=None, attachments=None):
      """Insert a notification record after send attempt."""

  def list_email_history(self, status=None, limit=50, cursor=None) -> tuple[list[dict], str | None]:
      """List notifications with optional status filter + cursor pagination."""

  def get_email_history_by_id(self, record_id: int) -> dict | None:
      """Get a single record for resend."""

  def mark_email_resent(self, record_id: int):
      """Update Status='resent', ResentAt=now()."""
  ```

- [ ] **Step 3: Wire into email sending code.**

  In `javdb/integrations/notify/email.py`, after each `smtp.send_message()` call, append to history:

  ```python
  try:
      smtp.send_message(msg)
      ops_repo.append_email_history(session_id, recipient, subject, "sent", attachments=attachment_names)
  except Exception as e:
      ops_repo.append_email_history(session_id, recipient, subject, "failed", error=str(e))
      raise
  ```

- [ ] **Step 4: Write unit test.**

  ```python
  # tests/unit/test_email_history.py
  def test_append_and_list_email_history(ops_repo):
      ops_repo.append_email_history("sess-1", "user@example.com", "Daily Report", "sent")
      ops_repo.append_email_history("sess-1", "user@example.com", "Daily Report", "failed", error="SMTP timeout")
      records, _ = ops_repo.list_email_history()
      assert len(records) == 2
      failed = [r for r in records if r["Status"] == "failed"]
      assert len(failed) == 1
  ```

- [ ] **Step 5: Apply migration locally + run tests.**

  ```bash
  python3 -m apps.cli.db.migration --apply
  pytest tests/unit/test_email_history.py -v
  ```

- [ ] **Step 6: Commit.**

  ```bash
  git add javdb/migrations/d1/0018_email_notification_history.sql \
          javdb/storage/repos/operations_repo.py javdb/integrations/notify/email.py tests/
  git commit -m "feat(db): add EmailNotificationHistory table + wire into email sender"
  ```

---

## Task 3: Operations Endpoints

**Files:**
- Create: `apps/api/routers/operations.py`
- Create: `apps/api/schemas/operations.py`
- Modify: `apps/api/server.py`

**Endpoints:**

| Method | Path | Request body | Purpose |
|--------|------|-------------|---------|
| `GET` | `/api/ops/qb/torrents` | — | List qB torrents (BE-proxied) |
| `POST` | `/api/ops/qb/filter-small` | `{ min_size_mb, days, dry_run, categories, delete_local_files }` | Trigger qb_file_filter |
| `GET` | `/api/ops/pikpak/queue` | — | PikPak bridge queue (pending transfers from PikpakHistory) |
| `POST` | `/api/ops/pikpak/transfer` | `{ days, dry_run }` | Trigger batch PikPak transfer |
| `GET` | `/api/ops/rclone/last` | — | Last RcloneInventory scan + DedupRecords summary |
| `POST` | `/api/ops/rclone/run` | `{ scan, report, execute, dry_run }` | Run rclone manager with flag combination |
| `POST` | `/api/ops/email/test` | `{ recipient }` | Send test email |
| `GET` | `/api/ops/email/history` | `?status=&cursor=&limit=` | List EmailNotificationHistory |
| `POST` | `/api/ops/email/{id}/resend` | — | Resend a failed notification |
| `POST` | `/api/ops/cleanup/stale-sessions` | `{ older_than_hours, dry_run, scope, include_legacy }` | Wrap cleanup_stale_in_progress.py |
| `POST` | `/api/ops/cleanup/claim-stages` | `{ shard_dates, older_than_hours }` | Wrap sweep_claim_stages.py |

- [ ] **Step 1: Define Pydantic schemas.**

  ```python
  # apps/api/schemas/operations.py

  # --- qBittorrent ---
  class QbTorrentItem(BaseModel):
      hash: str
      name: str
      size: int
      progress: float
      state: str
      category: str
      added_on: int              # Unix timestamp
      completion_on: int

  class QbTorrentsResponse(BaseModel):
      items: List[QbTorrentItem]
      total: int

  class QbFilterSmallRequest(BaseModel):
      min_size_mb: float = 100.0
      days: int = 2
      dry_run: bool = True
      categories: Optional[List[str]] = None
      delete_local_files: bool = False

  class QbFilterSmallResponse(BaseModel):
      filtered_count: int
      torrents_scanned: int
      dry_run: bool
      details: List[dict]        # Per-torrent filter results

  # --- PikPak ---
  class PikPakQueueItem(BaseModel):
      id: int
      torrent_hash: str
      torrent_name: str
      category: str
      transfer_status: str       # pending | success | failed
      error_message: Optional[str]
      datetime_added_to_qb: Optional[str]

  class PikPakQueueResponse(BaseModel):
      items: List[PikPakQueueItem]
      total: int

  class PikPakTransferRequest(BaseModel):
      days: int = 7
      dry_run: bool = True

  class PikPakTransferResponse(BaseModel):
      transferred: int
      failed: int
      skipped: int
      dry_run: bool
      details: List[dict]

  # --- Rclone ---
  class RcloneLastResponse(BaseModel):
      inventory_count: int
      last_scan_time: Optional[str]
      dedup_pending: int         # DedupRecords WHERE IsDeleted = 0
      dedup_completed: int       # DedupRecords WHERE IsDeleted = 1
      total_freed_bytes: int

  class RcloneRunRequest(BaseModel):
      scan: bool = True
      report: bool = True
      execute: bool = False
      dry_run: bool = True

  class RcloneRunResponse(BaseModel):
      phase_results: dict        # { scan: {...}, report: {...}, execute: {...} }
      dry_run: bool

  # --- Email ---
  class EmailTestRequest(BaseModel):
      recipient: Optional[str] = None  # Defaults to EMAIL_TO from config

  class EmailHistoryItem(BaseModel):
      id: int
      session_id: Optional[str]
      recipient: str
      subject: str
      status: str
      error_message: Optional[str]
      sent_at: str
      resent_at: Optional[str]

  class EmailHistoryResponse(BaseModel):
      items: List[EmailHistoryItem]
      next_cursor: Optional[str]

  # --- Cleanup ---
  class CleanupStaleRequest(BaseModel):
      older_than_hours: float = 48.0
      dry_run: bool = True
      scope: str = "all"         # reports | operations | history | all
      include_legacy: bool = False

  class CleanupStaleResponse(BaseModel):
      sessions_found: int
      sessions_cleaned: int
      sessions_failed: int
      dry_run: bool
      details: List[dict]

  class CleanupClaimStagesRequest(BaseModel):
      shard_dates: Optional[List[str]] = None  # YYYY-MM-DD; defaults to today + 2 prior
      older_than_hours: float = 6.0

  class CleanupClaimStagesResponse(BaseModel):
      shards_processed: int
      stages_reaped: int
      details: List[dict]
  ```

- [ ] **Step 2: Implement operations router.**

  Each handler wraps the corresponding CLI/integration module. Pattern:

  ```python
  # apps/api/routers/operations.py
  router = APIRouter(prefix="/api/ops", tags=["operations"])

  @router.get("/qb/torrents", response_model=QbTorrentsResponse)
  async def list_qb_torrents(user=Depends(_require_auth)):
      """Proxy qBittorrent torrents list through BE."""
      from javdb.integrations.qb.client import QBittorrentClient
      client = QBittorrentClient()
      torrents = client.list_torrents()
      return QbTorrentsResponse(items=torrents, total=len(torrents))

  @router.post("/qb/filter-small", response_model=QbFilterSmallResponse)
  async def filter_small_files(body: QbFilterSmallRequest, user=Depends(_require_admin)):
      """Trigger qb_file_filter with given parameters."""
      from javdb.integrations.qb.file_filter import run_file_filter
      result = run_file_filter(
          min_size_mb=body.min_size_mb, days=body.days,
          dry_run=body.dry_run, categories=body.categories,
          delete_local_files=body.delete_local_files,
      )
      return QbFilterSmallResponse(**result)
  ```

  Same pattern for PikPak, Rclone, Email, Cleanup — each calls the underlying integration/CLI module.

  **Rclone validation**: reject invalid flag combinations (`scan=False, report=False, execute=True` → 422).

  **Email resend**: `POST /api/ops/email/{id}/resend` loads the original record, re-sends with same subject/recipient, updates status.

  **Cleanup stale-sessions**: calls `cleanup_stale_in_progress` logic (not subprocess — import the main function). Same for claim-stages.

- [ ] **Step 3: Write unit tests for each handler.**

  ```python
  # tests/unit/test_operations_endpoints.py
  def test_qb_filter_dry_run(test_client, mock_qb):
      resp = test_client.post("/api/ops/qb/filter-small", json={"dry_run": True, "min_size_mb": 50})
      assert resp.status_code == 200
      assert resp.json()["dry_run"] is True

  def test_rclone_invalid_flags(test_client):
      resp = test_client.post("/api/ops/rclone/run", json={"scan": False, "report": False, "execute": True})
      assert resp.status_code == 422  # execute requires report

  def test_email_resend_not_found(test_client):
      resp = test_client.post("/api/ops/email/99999/resend")
      assert resp.status_code == 404

  def test_cleanup_stale_dry_run(test_client, mock_db):
      resp = test_client.post("/api/ops/cleanup/stale-sessions", json={"dry_run": True})
      assert resp.status_code == 200
      assert resp.json()["dry_run"] is True
  ```

- [ ] **Step 4: Register router + run tests.**

  ```bash
  pytest tests/unit/test_operations_endpoints.py -v
  ```

- [ ] **Step 5: Commit.**

  ```bash
  git add apps/api/routers/operations.py apps/api/schemas/operations.py apps/api/server.py tests/
  git commit -m "feat(api): add operations endpoints — qB, PikPak, Rclone, Email, Cleanup (Phase 2)"
  ```

---

## Task 4: Diagnostics Endpoints

**Files:**
- Create: `apps/api/routers/diagnostics.py`
- Create: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/server.py`

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/diag/javdb-session` | Cookie status, expiry, last refresh time |
| `POST` | `/api/diag/javdb-session/refresh` | Refresh javdb session (headless login or cookie paste) |

**Note:** `POST /api/health-check` (deep health), `POST /api/login/refresh` (headless login), and all `POST /api/parse/*` endpoints already exist in `apps/api/routers/system.py` and `apps/api/routers/explore.py`. Phase 2 FE pages will wire to these existing endpoints. Only the javdb-session diagnostics endpoints are new.

- [ ] **Step 1: Define schemas.**

  ```python
  # apps/api/schemas/diagnostics.py
  class JavdbSessionStatus(BaseModel):
      cookie_present: bool
      cookie_value_preview: Optional[str]    # First 8 chars + "..."
      last_refresh_time: Optional[str]       # From system_state KV
      estimated_expiry: Optional[str]        # Best-effort from cookie metadata
      is_likely_valid: bool                  # Heuristic: refreshed < 24h ago

  class JavdbSessionRefreshRequest(BaseModel):
      method: str = "headless"               # headless | cookie_paste
      cookie_value: Optional[str] = None     # Required when method=cookie_paste

  class JavdbSessionRefreshResponse(BaseModel):
      success: bool
      method: str
      new_cookie_preview: Optional[str]
      error: Optional[str]
  ```

- [ ] **Step 2: Implement router.**

  `GET /api/diag/javdb-session`: reads `JAVDB_SESSION_COOKIE` from config + `system_state.last_javdb_refresh` KV.

  `POST /api/diag/javdb-session/refresh`:
  - `method=headless` → calls existing `javdb/spider/auth/login.py` login flow → updates config + system_state
  - `method=cookie_paste` → validates cookie format → persists to config + system_state

- [ ] **Step 3: Write tests.**

  ```python
  def test_javdb_session_status(test_client):
      resp = test_client.get("/api/diag/javdb-session")
      assert resp.status_code == 200
      assert "cookie_present" in resp.json()

  def test_javdb_session_refresh_cookie_paste(test_client):
      resp = test_client.post("/api/diag/javdb-session/refresh", json={
          "method": "cookie_paste",
          "cookie_value": "test_cookie_value_abc123"
      })
      assert resp.status_code == 200
      assert resp.json()["success"] is True
  ```

- [ ] **Step 4: Run tests + commit.**

  ```bash
  pytest tests/unit/test_diagnostics_endpoints.py -v
  git add apps/api/routers/diagnostics.py apps/api/schemas/diagnostics.py apps/api/server.py tests/
  git commit -m "feat(api): add diagnostics endpoints — JavDB session status + refresh (Phase 2)"
  ```

---

## Task 5: GitHub Actions Endpoints

**Files:**
- Create: `javdb/integrations/gh_actions/__init__.py`
- Create: `javdb/integrations/gh_actions/client.py`
- Create: `apps/api/routers/gh_actions.py`
- Create: `apps/api/schemas/gh_actions.py`
- Modify: `apps/api/server.py`

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/gh-actions/workflows` | List workflows with last run status |
| `GET` | `/api/gh-actions/runs` | List runs with `?workflow=` filter |
| `POST` | `/api/gh-actions/runs` | Dispatch a workflow run |
| `GET` | `/api/gh-actions/runs/{id}/logs` | Stream run logs |

**GH Actions tier gate:** All endpoints require `capabilities.gh_actions.tier >= 'monitor'`. Router-level dependency.

- [ ] **Step 1: Implement httpx GitHub client.**

  ```python
  # javdb/integrations/gh_actions/client.py
  import httpx

  class GitHubActionsClient:
      """Thin wrapper around GitHub REST API v3 for Actions resources."""

      BASE = "https://api.github.com"

      def __init__(self, token: str, repo: str):
          """token: PAT (from GIT_PASSWORD). repo: 'owner/name'."""
          self._client = httpx.Client(
              base_url=self.BASE,
              headers={
                  "Authorization": f"Bearer {token}",
                  "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28",
              },
              timeout=30.0,
          )
          self._repo = repo

      def list_workflows(self) -> list[dict]:
          resp = self._client.get(f"/repos/{self._repo}/actions/workflows")
          resp.raise_for_status()
          return resp.json()["workflows"]

      def list_runs(self, workflow_id: int | None = None, per_page: int = 30) -> list[dict]:
          params = {"per_page": per_page}
          if workflow_id:
              url = f"/repos/{self._repo}/actions/workflows/{workflow_id}/runs"
          else:
              url = f"/repos/{self._repo}/actions/runs"
          resp = self._client.get(url, params=params)
          resp.raise_for_status()
          return resp.json()["workflow_runs"]

      def dispatch_workflow(self, workflow_id: int, ref: str = "main", inputs: dict | None = None) -> None:
          body = {"ref": ref}
          if inputs:
              body["inputs"] = inputs
          resp = self._client.post(
              f"/repos/{self._repo}/actions/workflows/{workflow_id}/dispatches",
              json=body,
          )
          resp.raise_for_status()  # 204 No Content on success

      def get_run_logs_url(self, run_id: int) -> str:
          resp = self._client.get(
              f"/repos/{self._repo}/actions/runs/{run_id}/logs",
              follow_redirects=False,
          )
          if resp.status_code == 302:
              return resp.headers["location"]
          resp.raise_for_status()
          return ""

      def close(self):
          self._client.close()
  ```

- [ ] **Step 2: Write unit tests with httpx mocking.**

  ```python
  # tests/unit/test_gh_actions_client.py
  import httpx
  from javdb.integrations.gh_actions.client import GitHubActionsClient

  def test_list_workflows(httpx_mock):
      httpx_mock.add_response(
          url="https://api.github.com/repos/owner/repo/actions/workflows",
          json={"workflows": [{"id": 1, "name": "CI", "state": "active"}]},
      )
      client = GitHubActionsClient(token="test", repo="owner/repo")
      workflows = client.list_workflows()
      assert len(workflows) == 1
      assert workflows[0]["name"] == "CI"

  def test_dispatch_workflow(httpx_mock):
      httpx_mock.add_response(
          url="https://api.github.com/repos/owner/repo/actions/workflows/1/dispatches",
          status_code=204,
      )
      client = GitHubActionsClient(token="test", repo="owner/repo")
      client.dispatch_workflow(1, inputs={"dry_run": "true"})
  ```

  Run: `pytest tests/unit/test_gh_actions_client.py -v`

- [ ] **Step 3: Implement router.**

  ```python
  # apps/api/routers/gh_actions.py
  router = APIRouter(prefix="/api/gh-actions", tags=["gh-actions"])

  def _require_gh_monitor(caps=Depends(get_capabilities)):
      """Dependency: require gh_actions.tier >= 'monitor'."""
      if caps.gh_actions.tier == "none":
          raise HTTPException(403, "GitHub Actions integration not configured")

  @router.get("/workflows", dependencies=[Depends(_require_gh_monitor)])
  async def list_workflows(user=Depends(_require_auth)):
      client = _get_gh_client()
      workflows = client.list_workflows()
      # Enrich each workflow with its latest run status
      for wf in workflows:
          runs = client.list_runs(workflow_id=wf["id"], per_page=1)
          wf["last_run"] = runs[0] if runs else None
      return {"workflows": workflows}

  @router.get("/runs", dependencies=[Depends(_require_gh_monitor)])
  async def list_runs(workflow: int | None = None, user=Depends(_require_auth)):
      client = _get_gh_client()
      return {"runs": client.list_runs(workflow_id=workflow)}

  @router.post("/runs", dependencies=[Depends(_require_gh_monitor)])
  async def dispatch_run(body: DispatchRequest, user=Depends(_require_admin)):
      client = _get_gh_client()
      client.dispatch_workflow(body.workflow_id, ref=body.ref, inputs=body.inputs)
      return {"dispatched": True}

  @router.get("/runs/{run_id}/logs", dependencies=[Depends(_require_gh_monitor)])
  async def get_run_logs(run_id: int, user=Depends(_require_auth)):
      client = _get_gh_client()
      logs_url = client.get_run_logs_url(run_id)
      return {"logs_url": logs_url}
  ```

- [ ] **Step 4: Write integration test + commit.**

  ```bash
  pytest tests/unit/test_gh_actions_client.py tests/unit/test_gh_actions_endpoints.py -v
  git add javdb/integrations/gh_actions/ apps/api/routers/gh_actions.py \
          apps/api/schemas/gh_actions.py apps/api/server.py tests/
  git commit -m "feat(api): add GitHub Actions endpoints — workflows, runs, dispatch, logs (Phase 2)"
  ```

---

# Part 2: Frontend Pages (web repo)

## Task 6: Data — Movies Page

**Files:**
- Create: `src/pages/data/MoviesPage.vue`
- Create: `src/api/history.ts`
- Modify: `src/router/index.ts`
- Create: `tests/unit/pages/movies-page.spec.ts`

- [ ] **Step 1: Create API wrappers.**

  ```typescript
  // src/api/history.ts
  import { httpClient } from './client';
  import type { paths } from '@/types/api.gen';

  export function searchMovies(params: {
    q?: string; actor?: string; perfect_match?: boolean;
    hi_res?: boolean; session_id?: string;
    date_from?: string; date_to?: string;
    cursor?: string; limit?: number;
  }) {
    return httpClient.get('/api/history/movies', { params });
  }

  export function exportMoviesCsv(params: Record<string, unknown>) {
    return httpClient.get('/api/history/movies/export', {
      params,
      responseType: 'blob',
    });
  }

  export function searchTorrents(params: { /* similar */ }) {
    return httpClient.get('/api/history/torrents', { params });
  }

  export function exportTorrentsCsv(params: Record<string, unknown>) {
    return httpClient.get('/api/history/torrents/export', {
      params,
      responseType: 'blob',
    });
  }
  ```

- [ ] **Step 2: Build MoviesPage component.**

  Layout:
  - Top: search bar (`n-input` with search icon) + filter row (actor dropdown, perfect match toggle, hi-res toggle, date range picker, session ID input)
  - Center: `n-data-table` with columns: VideoCode, ActorName, PerfectMatch (badge), HiRes (badge), Created, Torrents (count), SessionId
  - Row click → expand row showing nested torrents
  - Bottom: "Load more" button (cursor pagination) + total estimate display
  - Top-right: "Export CSV" button → calls `exportMoviesCsv()` with current filters → triggers browser download

  Use `useApi` composable for initial load, manual fetch on filter change (debounced 300ms).

- [ ] **Step 3: Add route.**

  ```typescript
  { path: '/data/movies', component: () => import('@/pages/data/MoviesPage.vue'), meta: { roles: ['admin', 'readonly'] } }
  ```

- [ ] **Step 4: Write unit test.**

  ```typescript
  // tests/unit/pages/movies-page.spec.ts
  import { mount } from '@vue/test-utils';
  import MoviesPage from '@/pages/data/MoviesPage.vue';

  test('renders search bar and table', async () => {
    const wrapper = mount(MoviesPage, { /* provide mocked API */ });
    expect(wrapper.find('[data-testid="movie-search"]').exists()).toBe(true);
    expect(wrapper.find('.n-data-table').exists()).toBe(true);
  });

  test('export button triggers CSV download', async () => {
    // Mock exportMoviesCsv to return a Blob
    // Click export → verify createObjectURL was called
  });
  ```

- [ ] **Step 5: Browser-test the page + commit.**

  ```bash
  npm run dev  # Start Vite
  # Navigate to /data/movies, verify search, filter, pagination, CSV export
  git add src/pages/data/MoviesPage.vue src/api/history.ts src/router/index.ts tests/
  git commit -m "feat(fe): add Movies data page with search + CSV export"
  ```

---

## Task 7: Data — Torrents Page

**Files:**
- Create: `src/pages/data/TorrentsPage.vue`
- Create: `tests/unit/pages/torrents-page.spec.ts`

Same pattern as MoviesPage but with TorrentHistory-specific columns:

- Columns: VideoCode (joined), MagnetUri (truncated), Size, Resolution (badge), Subtitle (badge), Censor (badge), FileCount, Created, SessionId
- Filters: resolution type dropdown, subtitle toggle, uncensored toggle, session ID, date range
- CSV export with same blob download mechanism

- [ ] **Step 1: Build TorrentsPage component.**
- [ ] **Step 2: Add route + unit test.**
- [ ] **Step 3: Browser-test + commit.**

  ```bash
  git add src/pages/data/TorrentsPage.vue tests/
  git commit -m "feat(fe): add Torrents data page with search + CSV export"
  ```

---

## Task 8: Operations — qBittorrent Page

**Files:**
- Create: `src/pages/operations/QBittorrentPage.vue`
- Create: `src/api/operations.ts`

Layout:
- Top card: "Current Torrents" — `n-data-table` listing qB torrents (name, size, progress bar, state badge, category, added date). Refresh button.
- Bottom card: "Filter Small Files" — form with min_size_mb input (default 100), days input (default 2), categories multi-select, delete_local_files checkbox, dry-run toggle, "Run Filter" button. Result display below.

- [ ] **Step 1: Create `src/api/operations.ts` with typed wrappers for all operations endpoints.**
- [ ] **Step 2: Build QBittorrentPage.**
- [ ] **Step 3: Add route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add qBittorrent operations page"
  ```

---

## Task 9: Operations — PikPak Page

**Files:**
- Create: `src/pages/operations/PikPakPage.vue`

Layout:
- Top card: "Transfer Queue" — table of PikpakHistory records (torrent name, category, status badge, error, timestamps). Filter by status.
- Bottom card: "Batch Transfer" — days input (default 7), dry-run toggle, "Run Transfer" button + result summary.

- [ ] **Step 1: Build PikPakPage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add PikPak operations page"
  ```

---

## Task 10: Operations — Rclone Page

**Files:**
- Create: `src/pages/operations/RclonePage.vue`

Layout:
- Top card: "Last Scan Summary" — inventory count, last scan time, dedup pending/completed counts, total freed bytes. Auto-loaded from `GET /api/ops/rclone/last`.
- Bottom card: "Run Dedup" — two presets:
  - "Quick Dedup" button (scan + report + execute, dry_run=true)
  - "Advanced" collapsible: individual scan/report/execute toggles + dry-run toggle
  - Validation: reject `execute=true` without `report=true` (disable execute checkbox when report is off)
  - Result display with phase-by-phase breakdown.

- [ ] **Step 1: Build RclonePage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add Rclone operations page"
  ```

---

## Task 11: Operations — Email Page

**Files:**
- Create: `src/pages/operations/EmailPage.vue`

Layout:
- Top card: "Send Test Email" — optional recipient input (defaults to config EMAIL_TO), "Send" button + success/error toast.
- Bottom card: "Notification History" — table of EmailNotificationHistory (subject, recipient, status badge, sent_at, error). Status filter. Row action: "Resend" button on `failed` rows → calls `POST /api/ops/email/{id}/resend`.

- [ ] **Step 1: Build EmailPage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add Email operations page with history + resend"
  ```

---

## Task 12: Operations — Cleanup Page

**Files:**
- Create: `src/pages/operations/CleanupPage.vue`

Layout:
- Card 1: "Stale Session Cleanup" — older_than_hours input (default 48), scope dropdown (all/reports/operations/history), include_legacy checkbox, dry-run toggle, "Run Cleanup" button. Result: sessions found / cleaned / failed.
- Card 2: "Claim Stage Sweep" — shard_dates multi-date picker (default: today + 2 prior), older_than_hours input (default 6), "Run Sweep" button. Result: shards processed / stages reaped.

Both operations default to dry-run. The "Apply" button is a separate action that re-runs with `dry_run=false`.

- [ ] **Step 1: Build CleanupPage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add Cleanup operations page — stale sessions + claim stages"
  ```

---

## Task 13: Diagnostics — Health Page

**Files:**
- Create: `src/pages/diagnostics/HealthPage.vue`

Layout:
- Single card with "Run Deep Health Check" button → calls existing `POST /api/health-check`.
- Result renders as a structured checklist: each subsystem (DB, qB, proxy, SMTP, D1, coordinator) with green/red/amber status dot + detail message.

- [ ] **Step 1: Build HealthPage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add Health diagnostics page"
  ```

---

## Task 14: Diagnostics — Parse Tester Page

**Files:**
- Create: `src/pages/diagnostics/ParseTesterPage.vue`

Layout per spec §6.3:
- Left panel: textarea (paste HTML) OR URL input + parser selector dropdown (`index` / `detail` / `category` / `top` / `tags` / `auto-detect`). "Parse" button.
  - Auto-detect calls `POST /api/detect-page-type` first, then the appropriate parse endpoint.
  - URL mode calls `POST /api/parse/url`.
  - HTML mode calls `POST /api/parse/{type}` with `HtmlPayload`.
- Right panel: structured result as collapsible JSON tree (`n-tree` or recursive component). Copy-to-clipboard button.

All parse endpoints already exist. FE just wires to them.

- [ ] **Step 1: Build ParseTesterPage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add Parse Tester diagnostics page"
  ```

---

## Task 15: Diagnostics — JavDB Session Page

**Files:**
- Create: `src/pages/diagnostics/JavdbSessionPage.vue`

Layout:
- Status card: cookie present/absent, preview, last refresh time, estimated expiry, validity indicator. Auto-loaded from `GET /api/diag/javdb-session`.
- Actions:
  - "Refresh (headless login)" button → `POST /api/diag/javdb-session/refresh { method: "headless" }`
  - "Paste cookie" → expand textarea + submit → `POST /api/diag/javdb-session/refresh { method: "cookie_paste", cookie_value: "..." }`
  - "Re-login (full)" button → calls existing `POST /api/login/refresh` (headless re-login from `apps/api/routers/system.py`)

- [ ] **Step 1: Build JavdbSessionPage + route + unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add JavDB Session diagnostics page"
  ```

---

## Task 16: GH Actions — Runs Page (Monitor Tier)

**Files:**
- Create: `src/pages/gh-actions/RunsPage.vue`
- Create: `src/api/gh-actions.ts`

**Conditional rendering:** Sidebar entry + route only visible when `capabilities.gh_actions.tier !== 'none'`.

Layout:
- Left sidebar: workflow list with last-run status dot (green/red/amber). Click selects workflow.
- Main area: runs table for selected workflow (run #, status badge, trigger type, started_at, duration, commit SHA).
- Top-right: "Dispatch" button → modal form auto-generated from `workflow_dispatch.inputs` metadata (input name → label, `type` → input widget, `default` → prefill, `description` → tooltip, `required` → validation). Submit → `POST /api/gh-actions/runs`.
- Row click → drawer with run details. "View Logs" button → opens logs URL from `GET /api/gh-actions/runs/{id}/logs` in new tab (GitHub's log download is a ZIP — future enhancement to stream in-app).
- Polling: `usePolling(fetchRuns, 15_000)` pauses on hidden tab.

- [ ] **Step 1: Create `src/api/gh-actions.ts` with typed wrappers.**
- [ ] **Step 2: Build RunsPage with workflow list + runs table + dispatch modal.**
- [ ] **Step 3: Add conditional route + sidebar visibility gate.**
- [ ] **Step 4: Unit test + browser-test + commit.**

  ```bash
  git commit -m "feat(fe): add GitHub Actions monitor page — workflows, runs, dispatch"
  ```

---

## Task 17: Update Sidebar Navigation

**Files:**
- Modify: `src/components/layout/Sidebar.vue` (or equivalent)
- Modify: `src/router/index.ts`

Add Phase 2 nav entries per spec §6.1:

```
💾 Data
   ├ Movies           /data/movies
   └ Torrents         /data/torrents
⚙️ Operations
   ├ qBittorrent      /ops/qb
   ├ PikPak           /ops/pikpak
   ├ Rclone           /ops/rclone
   ├ Email            /ops/email
   └ Cleanup          /ops/cleanup
🔧 Diagnostics
   ├ Health           /diag/health
   ├ Parse tester     /diag/parse
   └ JavDB session    /diag/javdb
🚀 GitHub Actions     /gh-actions       (conditional: tier != 'none')
```

Operations sub-items render as in-page tabs (single route `/ops` with tab query param) OR as sidebar sub-items — follow whichever pattern Phase 1 Activity (Tasks/Sessions) established.

- [ ] **Step 1: Add all routes and sidebar items.**
- [ ] **Step 2: Verify capability-gated items hide/show correctly.**
- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "feat(fe): add Phase 2 sidebar navigation entries"
  ```

---

# Part 3: E2E Journeys

## Task 18: E2E Journeys 9–17

**Files:**
- Create: 10 spec files listed in File Structure Overview above

Each journey uses `page.route()` mocks for external services (qB, PikPak, Rclone, GitHub API, javdb.com). Fixture responses in `tests/e2e/fixtures/`.

| # | Spec file | Journey | Key assertions |
|---|-----------|---------|---------------|
| 9 | `ops-qb.spec.ts` | qB filter small files | Table renders torrents → filter dry-run shows preview → apply filters |
| 10 | `ops-pikpak.spec.ts` | PikPak batch transfer | Queue table renders → trigger transfer dry-run → verify result summary |
| 11 | `ops-rclone.spec.ts` | Rclone dedup | Last scan summary renders → Quick Dedup dry-run → result with phase breakdown |
| 12 | `ops-email.spec.ts` | Email test + history + resend | Send test → appears in history → fail a send (mock) → resend → status updates |
| 12a | `ops-cleanup.spec.ts` | Cleanup stale + claim stages | Stale sessions dry-run → apply → claim stages sweep → result |
| 13 | `diag-parse-tester.spec.ts` | Parse tester (HTML + URL + auto-detect) | Paste HTML → parse → JSON tree renders. URL → auto-detect → correct parser called |
| 14 | `diag-javdb-session.spec.ts` | JavDB session refresh | Status card renders → cookie paste → success → status updates |
| 15 | `diag-health.spec.ts` | Deep health check | Run check → all subsystems render with status dots |
| 16 | `gh-actions-monitor.spec.ts` | GH Actions monitor | Workflow list → select → runs table → dispatch modal → fill inputs → submit |
| 17 | `data-search.spec.ts` | Data Movies/Torrents search + CSV export | Search by code → results render → filter by actor → results narrow → export CSV → blob downloads |

- [ ] **Step 1: Create shared mock fixtures.**

  ```bash
  tests/e2e/fixtures/
  ├── qb-mocks.ts           # Mock qB API responses (torrent list, filter results)
  ├── pikpak-mocks.ts       # Mock PikPak queue and transfer responses
  ├── rclone-mocks.ts       # Mock rclone scan/report/execute responses
  ├── email-mocks.ts        # Mock email send + history
  ├── gh-actions-mocks.ts   # Mock GitHub API workflows/runs/dispatch
  └── history-mocks.ts      # Mock history search + CSV blob
  ```

- [ ] **Step 2: Write journey specs 9–12a (Operations).**

  Run: `npx playwright test ops-*.spec.ts --project=chromium`
  Expected: 5 specs pass.

- [ ] **Step 3: Write journey specs 13–15 (Diagnostics).**

  Run: `npx playwright test diag-*.spec.ts --project=chromium`
  Expected: 3 specs pass.

- [ ] **Step 4: Write journey spec 16 (GH Actions).**

  Run: `npx playwright test gh-actions-monitor.spec.ts --project=chromium`
  Expected: PASS.

- [ ] **Step 5: Write journey spec 17 (Data search).**

  Run: `npx playwright test data-search.spec.ts --project=chromium`
  Expected: PASS.

- [ ] **Step 6: Run full E2E suite (Phase 1 + Phase 2).**

  ```bash
  npx playwright test --project=chromium
  ```
  Expected: all 23 journeys pass (13 Phase 1 + 10 Phase 2).

- [ ] **Step 7: Commit.**

  ```bash
  git add tests/e2e/
  git commit -m "test(e2e): add Phase 2 journeys 9-17 — operations, diagnostics, GH Actions, data"
  ```

---

## Endpoint Coverage Matrix (Phase 2)

Every Phase 2 endpoint must appear in at least one journey.

| Endpoint | Journey |
|----------|---------|
| `GET /api/history/movies` | 17 |
| `GET /api/history/movies/export` | 17 |
| `GET /api/history/torrents` | 17 |
| `GET /api/history/torrents/export` | 17 |
| `GET /api/ops/qb/torrents` | 9 |
| `POST /api/ops/qb/filter-small` | 9 |
| `GET /api/ops/pikpak/queue` | 10 |
| `POST /api/ops/pikpak/transfer` | 10 |
| `GET /api/ops/rclone/last` | 11 |
| `POST /api/ops/rclone/run` | 11 |
| `POST /api/ops/email/test` | 12 |
| `GET /api/ops/email/history` | 12 |
| `POST /api/ops/email/{id}/resend` | 12 |
| `POST /api/ops/cleanup/stale-sessions` | 12a |
| `POST /api/ops/cleanup/claim-stages` | 12a |
| `GET /api/diag/javdb-session` | 14 |
| `POST /api/diag/javdb-session/refresh` | 14 |
| `POST /api/login/refresh` (existing) | 14 |
| `POST /api/health-check` (existing) | 15 |
| `POST /api/parse/index` (existing) | 13 |
| `POST /api/parse/detail` (existing) | 13 |
| `POST /api/parse/category` (existing) | 13 |
| `POST /api/parse/top` (existing) | 13 |
| `POST /api/parse/tags` (existing) | 13 |
| `POST /api/parse/url` (existing) | 13 |
| `POST /api/detect-page-type` (existing) | 13 |
| `GET /api/gh-actions/workflows` | 16 |
| `GET /api/gh-actions/runs` | 16 |
| `POST /api/gh-actions/runs` | 16 |
| `GET /api/gh-actions/runs/{id}/logs` | 16 |

---

## Suggested Execution Order

Tasks 1–5 (BE) can be worked in any order but must complete before FE pages that depend on them. Recommended:

```
Week 1:  Task 1 (history search) + Task 2 (email table)     — BE, independent
Week 2:  Task 3 (operations endpoints) + Task 4 (diagnostics) — BE, independent
Week 3:  Task 5 (GH Actions client + endpoints)               — BE
Week 4:  Task 6 (Movies) + Task 7 (Torrents) + Task 17 (nav) — FE
Week 5:  Tasks 8-12 (Operations pages)                        — FE
Week 6:  Tasks 13-16 (Diagnostics + GH Actions pages)         — FE
Week 7:  Task 18 (E2E journeys)                               — FE
```

BE and FE tracks can overlap: FE development against mock data can start as soon as schemas are defined (after Step 1 of each BE task).
