# ADR-022 Phase 5 — TypeScript Backend Sync

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror the Python Phase 3 endpoints in the Hono backend so the shared D1 query surface stays in sync. Add D1 query helpers for `MovieMetadata`, `MovieRatings`, and `ContentPreferences`, and expose Hono routes that match the FastAPI contract.

**Architecture:** A new `preference_service.ts` holds all D1 queries. A new `preferences.ts` route file maps Hono routes to those queries. Both follow the patterns already established in the sibling repo. The shared `openapi.json` contract is updated to include the new schemas.

**Tech Stack:** TypeScript, Hono, Cloudflare D1, Cloudflare Workers.

**Related:** [ADR-022](../JAVDB_AutoSpider_CICD/docs/design/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) · [IMP-ADR022-03](../JAVDB_AutoSpider_CICD/docs/design/ADR-022-User-Preference-Foundation/IMP-ADR022-03-preference-repo.md)

**Depends on:** IMP-ADR022-03 (Python CRUD API must be final — SQL and response shapes must not change after this phase).

**Blocks:** IMP-ADR022-06 (frontend consumes these endpoints).

**Location:** Sibling repo `javdb-autospider-web/`.

---

## Task 1 — D1 query service

**Files:**
- Create: `server/services/preference_service.ts`

- [ ] **Step 1: Create the file**

```typescript
import type { D1Database } from '@cloudflare/workers-types';

export interface MovieMetadataRow {
  href: string;
  title: string | null;
  video_code: string | null;
  release_date: string | null;
  duration_minutes: number | null;
  rate: number | null;
  comment_count: number | null;
  review_count: number | null;
  want_count: number | null;
  watched_count: number | null;
  maker: string | null;
  publisher: string | null;
  series: string | null;
  directors: string | null;
  categories: string | null;
  poster_url: string | null;
  fanart_urls: string | null;
  trailer_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface MovieRatingRow {
  href: string;
  video_code: string;
  rating: number | null;
  tags: string;
  notes: string | null;
  rated_at: string | null;
  updated_at: string;
}

export interface ContentPreferenceRow {
  content_type: string;
  content_id: string;
  content_name: string;
  hearted: number;
  weight: number;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// MovieMetadata
// ---------------------------------------------------------------------------

export async function getMetadata(
  db: D1Database,
  href: string,
): Promise<MovieMetadataRow | null> {
  return db
    .prepare('SELECT * FROM MovieMetadata WHERE href = ?')
    .bind(href)
    .first<MovieMetadataRow>();
}

// ---------------------------------------------------------------------------
// MovieRatings
// ---------------------------------------------------------------------------

export async function getRating(
  db: D1Database,
  href: string,
): Promise<MovieRatingRow | null> {
  return db
    .prepare('SELECT * FROM MovieRatings WHERE href = ?')
    .bind(href)
    .first<MovieRatingRow>();
}

export async function upsertRating(
  db: D1Database,
  href: string,
  videoCode: string,
  rating: number | null,
  tags: string[],
  notes: string | null,
): Promise<MovieRatingRow> {
  await db
    .prepare(`
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
    `)
    .bind(href, videoCode, rating, JSON.stringify(tags), notes)
    .run();
  return (await getRating(db, href))!;
}

export async function listRatings(
  db: D1Database,
  limit: number,
  offset: number,
): Promise<{ items: MovieRatingRow[]; total: number }> {
  const total =
    (await db
      .prepare('SELECT COUNT(*) AS n FROM MovieRatings')
      .first<{ n: number }>())?.n ?? 0;
  const result = await db
    .prepare(
      'SELECT * FROM MovieRatings ORDER BY updated_at DESC LIMIT ? OFFSET ?',
    )
    .bind(limit, offset)
    .all<MovieRatingRow>();
  return { items: result.results, total };
}

// ---------------------------------------------------------------------------
// ContentPreferences
// ---------------------------------------------------------------------------

export async function upsertPreference(
  db: D1Database,
  contentType: string,
  contentId: string,
  contentName: string,
  hearted: boolean,
  weight: number,
): Promise<ContentPreferenceRow> {
  await db
    .prepare(`
      INSERT INTO ContentPreferences
        (content_type, content_id, content_name, hearted, weight, updated_at)
      VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
      ON CONFLICT(content_type, content_id) DO UPDATE SET
        content_name = excluded.content_name,
        hearted      = excluded.hearted,
        weight       = excluded.weight,
        updated_at   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
    `)
    .bind(contentType, contentId, contentName, hearted ? 1 : 0, weight)
    .run();
  return (await db
    .prepare(
      'SELECT * FROM ContentPreferences WHERE content_type=? AND content_id=?',
    )
    .bind(contentType, contentId)
    .first<ContentPreferenceRow>())!;
}

export async function listPreferences(
  db: D1Database,
  contentType: string | null,
  heartedOnly: boolean,
): Promise<ContentPreferenceRow[]> {
  const conditions: string[] = [];
  const params: (string | number)[] = [];
  if (contentType) {
    conditions.push('content_type = ?');
    params.push(contentType);
  }
  if (heartedOnly) {
    conditions.push('hearted = 1');
  }
  const where =
    conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
  const result = await db
    .prepare(
      `SELECT * FROM ContentPreferences ${where} ORDER BY content_type, content_name`,
    )
    .bind(...params)
    .all<ContentPreferenceRow>();
  return result.results;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd javdb-autospider-web
npx tsc --noEmit
```

Expected: no errors in `server/services/preference_service.ts`.

- [ ] **Step 3: Commit**

```bash
git add server/services/preference_service.ts
git commit -m "feat(server): add preference D1 query service (ADR-022)"
```

---

## Task 2 — Hono route file

**Files:**
- Create: `server/routes/preferences.ts`

Read the existing route files in `server/routes/` to confirm the exact Hono router pattern used (how the router is created, how auth middleware is applied, how D1 binding is accessed). Then create `server/routes/preferences.ts` mirroring that pattern:

- [ ] **Step 1: Create the route file**

```typescript
import { Hono } from 'hono';
import { requireAuth } from '../middleware/auth';
import {
  getMetadata,
  getRating,
  upsertRating,
  listRatings,
  upsertPreference,
  listPreferences,
} from '../services/preference_service';

// Replace `Env` with the actual environment type used in this repo.
const preferences = new Hono<{ Bindings: Env }>();

const VALID_CONTENT_TYPES = new Set(['actor', 'category', 'maker', 'director']);

// GET /api/preferences/metadata/:href
preferences.get('/metadata/*', requireAuth, async (c) => {
  const href = c.req.path.replace('/api/preferences/metadata', '');
  const row = await getMetadata(c.env.DB, href);
  if (!row) return c.json({ error: { code: 'preferences.not_found', message: 'Record not found' } }, 404);

  const parse = (s: string | null) => { try { return s ? JSON.parse(s) : null; } catch { return null; } };
  return c.json({
    ...row,
    maker: parse(row.maker),
    publisher: parse(row.publisher),
    series: parse(row.series),
    directors: parse(row.directors),
    categories: parse(row.categories),
    fanart_urls: parse(row.fanart_urls),
  });
});

// GET /api/preferences/movies/ratings
preferences.get('/movies/ratings', requireAuth, async (c) => {
  const limit = Number(c.req.query('limit') ?? 50);
  const offset = Number(c.req.query('offset') ?? 0);
  const { items, total } = await listRatings(c.env.DB, limit, offset);
  return c.json({
    items: items.map((r) => ({ ...r, tags: JSON.parse(r.tags) })),
    total,
  });
});

// PUT /api/preferences/movies/:href/rating
preferences.put('/movies/*', requireAuth, async (c) => {
  const href = c.req.path.replace('/api/preferences/movies', '').replace('/rating', '');
  const body = await c.req.json<{ rating?: number | null; tags?: string[]; notes?: string | null }>();
  const videoCode = href.replace(/^\/video\//, '');
  const row = await upsertRating(
    c.env.DB, href, videoCode,
    body.rating ?? null, body.tags ?? [], body.notes ?? null,
  );
  return c.json({ ...row, tags: JSON.parse(row.tags) });
});

// GET /api/preferences/movies/:href/rating
preferences.get('/movies/*', requireAuth, async (c) => {
  const href = c.req.path.replace('/api/preferences/movies', '').replace('/rating', '');
  const row = await getRating(c.env.DB, href);
  if (!row) return c.json({ error: { code: 'preferences.not_found', message: 'Record not found' } }, 404);
  return c.json({ ...row, tags: JSON.parse(row.tags) });
});

// PUT /api/preferences/:contentType/:contentId
preferences.put('/:contentType/*', requireAuth, async (c) => {
  const contentType = c.req.param('contentType');
  if (!VALID_CONTENT_TYPES.has(contentType)) {
    return c.json({ error: { code: 'preferences.invalid_content_type', message: 'content_type must be one of: actor, category, maker, director' } }, 422);
  }
  const contentId = c.req.path.replace(`/api/preferences/${contentType}`, '');
  const body = await c.req.json<{ content_name: string; hearted: boolean; weight?: number }>();
  const row = await upsertPreference(
    c.env.DB, contentType, contentId, body.content_name, body.hearted, body.weight ?? 1.0,
  );
  return c.json({ ...row, hearted: Boolean(row.hearted) });
});

// GET /api/preferences/
preferences.get('/', requireAuth, async (c) => {
  const contentType = c.req.query('content_type') ?? null;
  const heartedOnly = c.req.query('hearted_only') === 'true';
  if (contentType && !VALID_CONTENT_TYPES.has(contentType)) {
    return c.json({ error: { code: 'preferences.invalid_content_type', message: 'content_type must be one of: actor, category, maker, director' } }, 422);
  }
  const items = await listPreferences(c.env.DB, contentType, heartedOnly);
  return c.json({ items: items.map((r) => ({ ...r, hearted: Boolean(r.hearted) })) });
});

export default preferences;
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/preferences.ts
git commit -m "feat(server): add preferences Hono routes (ADR-022)"
```

---

## Task 3 — Register routes in app.ts

**Files:**
- Modify: `server/app.ts`

- [ ] **Step 1: Import and mount**

Open `server/app.ts`. Following the existing pattern for other route files, add:

```typescript
import preferences from './routes/preferences';
```

Mount the router at `/api/preferences`:

```typescript
app.route('/api/preferences', preferences);
```

- [ ] **Step 2: Verify dev server starts**

```bash
cd javdb-autospider-web
npx wrangler dev
```

Expected: dev server starts without errors; `GET /api/preferences/` returns `{"items":[]}`.

- [ ] **Step 3: Commit**

```bash
git add server/app.ts
git commit -m "feat(server): register preferences routes in Hono app (ADR-022)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | TypeScript compiles | `npx tsc --noEmit` → no errors |
| 2 | Dev server starts | `npx wrangler dev` → no startup errors |
| 3 | List preferences returns empty array | `curl http://localhost:8787/api/preferences/` → `{"items":[]}` |
| 4 | Metadata route registered | `curl http://localhost:8787/api/preferences/metadata/video/TEST-001` → 404 `preferences.not_found` (not a 404 "route not found") |
