# ADR-022 Phase 6 — Web Frontend (C1, C3, C4, B3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four user-facing features in the Vue 3 frontend:
- **C1** — Inline star rating + tag chips + notes on the `/data` page
- **C3** — Keyboard-driven batch annotation mode on the `/data` page
- **C4** — Heart icon on actor/category/maker/director chips (writes to `ContentPreferences`)
- **B3** — Computed preference score column on `/data` and `/browse`

**Architecture:** A typed API client wraps the preference endpoints. A reusable `HeartButton.vue` component handles C4. The `/data` page (`DataPage.vue` or its table sub-component) is extended with rating UI and batch-mode keyboard handling. B3 score is a pure computed value — no extra API call needed.

**Tech Stack:** Vue 3, Naive UI (`NRate`, `NCheckboxGroup`, `NButton`, `NIcon`), Pinia, Axios, TypeScript.

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-05](IMP-ADR022-05-typescript-sync.md)

**Depends on:** IMP-ADR022-05 (Hono routes must be deployed or running in dev).

**Blocks:** Nothing — this is the last phase.

**Location:** Sibling repo `javdb-autospider-web/src/`.

---

## Status — ✅ Implemented (with deviations noted below)

All four features are implemented and merged/PR-open in `javdb-autospider-web`. The
final wiring landed across these commits (most recent first): the C1 tag-chips +
notes gap-fill and review fixes (`f34ebae`, `813c674`), the B3 `/browse` score +
C4 `/browse` hearts (`b9828e6`, `b6bf218`, `4ead773`), on top of the original
C1 inline-rating + B3 column (`a74e720`), C3 batch mode (`3529db9`),
`HeartButton` (`d8337c9`), and the API client (`27356ac`). Tracking PR:
[`TongWu/JAVDB_AutoSpider_Web#14`](https://github.com/TongWu/JAVDB_AutoSpider_Web/pull/14).

| Feature | Status | Notes |
|---------|--------|-------|
| C1 — inline star rating | ✅ Done | `/data/movies` (`NRate` column) |
| C1 — tag chips + notes | ✅ Done | Added in expandable row; **was missing from the original Task 3 steps** (see Task 3 addendum) |
| C3 — batch annotation | ✅ Done | `j/k/1–5/Enter/Space` keyboard mode |
| C4 — heart on actor | ✅ Done | `/data/movies` actor column + `/browse` actor chips |
| C4 — heart on category | ✅ Done | `/browse` tag/category chips (`content_type: category`) |
| C4 — heart on maker/director | ⛔ Deferred | **No maker/director chips are rendered anywhere in the frontend** — they live only in `MovieMetadataResponse`, not the rendered `ExploreResolveResponse`. Closing this needs a chip-rendering change first (tracked as a separate follow-up), not a `HeartButton` change. |
| B3 — score on `/data` | ✅ Done | Score column via `computePreferenceScore` |
| B3 — score on `/browse` | ✅ Done | Score chip on the Resolve detail card via shared `computePreferenceScore` |

**File-path note:** the plan below references `src/pages/DataPage.vue`. The actual
implementation lives in `src/pages/data/MoviesPage.vue` (the movies sub-page), with
`/browse` work in `src/components/browse/ResolveCard.vue`. Helpers landed as
`src/pages/data/rating-merge.ts`, `src/pages/data/preference-score.ts`, and
`src/components/browse/resolve-preferences.ts`.

**Key deviation — merge-preserving saves (correctness).** The backend rating upsert
(`PUT /api/preferences/movies/:href/rating`) is **full-replace**: a request omitting
`rating`/`tags`/`notes` clears the omitted fields. The plan's per-field saves (e.g.
`upsertMovieRating(href, { rating })`) would therefore wipe siblings once tags/notes
exist. All save paths now route through a single `saveRating()` that merges the patch
over the current row (`rating-merge.ts`) before the PUT. See the Task 3 addendum.

---

## Task 1 — Typed API client

**Files:**
- Create: `src/api/preferences.ts`

- [ ] **Step 1: Inspect the existing API client pattern**

Read one existing file in `src/api/` (e.g. `src/api/history.ts`) to confirm the exact import and usage of the shared Axios instance.

- [ ] **Step 2: Create `src/api/preferences.ts`**

```typescript
import { api } from './client'; // use the same import as other api/*.ts files

export interface MovieRating {
  href: string;
  video_code: string;
  rating: number | null;
  tags: string[];
  notes: string | null;
  rated_at: string | null;
  updated_at: string;
}

export interface MovieRatingListResponse {
  items: MovieRating[];
  total: number;
}

export interface ContentPreference {
  content_type: string;
  content_id: string;
  content_name: string;
  hearted: boolean;
  weight: number;
  updated_at: string;
}

export interface ContentPreferenceListResponse {
  items: ContentPreference[];
}

export const getMovieRating = (href: string) =>
  api.get<MovieRating>(`/api/preferences/movies/${encodeURIComponent(href)}/rating`);

export const upsertMovieRating = (
  href: string,
  payload: { rating?: number | null; tags?: string[]; notes?: string | null },
) =>
  api.put<MovieRating>(
    `/api/preferences/movies/${encodeURIComponent(href)}/rating`,
    payload,
  );

export const listMovieRatings = (params: { limit?: number; offset?: number }) =>
  api.get<MovieRatingListResponse>('/api/preferences/movies/ratings', { params });

export const upsertContentPreference = (
  contentType: string,
  contentId: string,
  payload: { content_name: string; hearted: boolean; weight?: number },
) =>
  api.put<ContentPreference>(
    `/api/preferences/${contentType}/${encodeURIComponent(contentId)}`,
    payload,
  );

export const listContentPreferences = (params?: {
  content_type?: string;
  hearted_only?: boolean;
}) => api.get<ContentPreferenceListResponse>('/api/preferences/', { params });
```

- [ ] **Step 3: Commit**

```bash
git add src/api/preferences.ts
git commit -m "feat(frontend): add preferences API client (ADR-022)"
```

---

## Task 2 — HeartButton component (C4)

**Files:**
- Create: `src/components/HeartButton.vue`

- [ ] **Step 1: Check which icon library is used in the project**

```bash
grep -r "HeartFilled\|HeartOutlined\|heart" src/components/ --include="*.vue" -l | head -5
grep "@vicons\|vicons" package.json
```

Use whichever icon set is already installed. The example below uses `@vicons/antd`; adjust the import if the project uses a different set.

- [ ] **Step 2: Create `src/components/HeartButton.vue`**

```vue
<template>
  <NButton
    :type="hearted ? 'error' : 'default'"
    :bordered="false"
    size="tiny"
    :loading="loading"
    :title="hearted ? 'Remove from favourites' : 'Add to favourites'"
    @click.stop="toggle"
  >
    <template #icon>
      <NIcon>
        <HeartFilled v-if="hearted" />
        <HeartOutlined v-else />
      </NIcon>
    </template>
  </NButton>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { NButton, NIcon, useMessage } from 'naive-ui';
import { HeartFilled, HeartOutlined } from '@vicons/antd';
import { upsertContentPreference } from '@/api/preferences';

const props = defineProps<{
  contentType: 'actor' | 'category' | 'maker' | 'director';
  contentId: string;
  contentName: string;
  initialHearted?: boolean;
}>();

const emit = defineEmits<{ (e: 'change', hearted: boolean): void }>();

const hearted = ref(props.initialHearted ?? false);
const loading = ref(false);
const message = useMessage();

async function toggle() {
  loading.value = true;
  try {
    await upsertContentPreference(props.contentType, props.contentId, {
      content_name: props.contentName,
      hearted: !hearted.value,
    });
    hearted.value = !hearted.value;
    emit('change', hearted.value);
  } catch {
    message.error('Failed to update preference');
  } finally {
    loading.value = false;
  }
}
</script>
```

- [ ] **Step 3: Commit**

```bash
git add src/components/HeartButton.vue
git commit -m "feat(frontend): add HeartButton component (C4, ADR-022)"
```

---

## Task 3 — Inline rating widget on /data page (C1) + B3 score column

**Files:**
- Modify: `src/pages/DataPage.vue` (or the table sub-component that renders movie rows)

Before making changes, read `src/pages/DataPage.vue` in full to understand the existing column definition structure and how rows are typed.

- [ ] **Step 1: Load ratings on page mount**

Add a `ratings` map to the page's reactive state and load it after the movie list loads:

```typescript
import { listMovieRatings, upsertMovieRating, upsertContentPreference } from '@/api/preferences';
import type { MovieRating } from '@/api/preferences';

// Reactive state additions
const ratings = ref<Map<string, MovieRating>>(new Map());
const actorHearted = ref<Map<string, boolean>>(new Map());

async function loadRatings() {
  const { data } = await listMovieRatings({ limit: 1000, offset: 0 });
  ratings.value = new Map(data.items.map((r) => [r.href, r]));
}

// Call loadRatings() after the existing data-fetch call in onMounted or watch.
```

- [ ] **Step 2: Add B3 score computation**

```typescript
const VALID_TAGS = [
  'quality_high','quality_low','resolution_bad','encoding_bad',
  'plot_good','actress_standout','not_my_type','category_miss',
  'would_rewatch','keep_long_term','delete_candidate','upgrade_wanted',
];

function preferenceScore(href: string, actorHref: string | null): number {
  const r = ratings.value.get(href);
  const movieScore = r?.rating != null ? r.rating / 5.0 : 0;
  const actorScore = actorHref
    ? (actorHearted.value.get(actorHref) ? 1.0 : 0.5)
    : 0.5;
  // Category match placeholder: 0.5 until ContentPreferences is loaded.
  return movieScore * 0.5 + actorScore * 0.3 + 0.5 * 0.2;
}
```

- [ ] **Step 3: Add rating column to the movie table**

In the column definitions array, add a `rating` column after the existing columns:

```typescript
{
  title: 'Rating',
  key: 'rating',
  width: 180,
  render(row: MovieHistoryRow) {
    const rating = ratings.value.get(row.href);
    return h('div', { style: 'display:flex;flex-direction:column;gap:4px' }, [
      h(NRate, {
        value: rating?.rating ?? 0,
        count: 5,
        'onUpdate:value': async (val: number) => {
          await upsertMovieRating(row.href, { rating: val || null });
          await loadRatings();
        },
      }),
    ]);
  },
},
{
  title: 'Score',
  key: 'score',
  width: 70,
  render(row: MovieHistoryRow) {
    const score = preferenceScore(row.href, row.actor_link ?? null);
    return h('span', { style: 'font-size:12px;color:#999' }, score.toFixed(2));
  },
},
```

- [ ] **Step 4: Add HeartButton to actor chip**

Wherever the actor name is rendered in the row (likely as a tag or link), add a `HeartButton` alongside it:

```typescript
import HeartButton from '@/components/HeartButton.vue';

// Inside the actor cell render:
h(HeartButton, {
  contentType: 'actor',
  contentId: row.actor_link ?? row.actor_name,
  contentName: row.actor_name,
  initialHearted: actorHearted.value.get(row.actor_link ?? '') ?? false,
  onChange: (val: boolean) => {
    if (row.actor_link) actorHearted.value.set(row.actor_link, val);
  },
})
```

- [ ] **Step 5: Verify in browser**

Start the dev server:
```bash
npm run dev
```

Navigate to `/data`. Confirm:
- Star rating widget renders per row.
- Clicking a star calls the API (check Network tab).
- Score column shows values between 0 and 1.
- Heart icon visible next to actor name.

- [ ] **Step 6: Commit**

```bash
git add src/pages/DataPage.vue
git commit -m "feat(frontend): add C1 inline rating and B3 score column (ADR-022)"
```

### Task 3 addendum — tag chips + notes (C1 completion) + merge-preserving saves

> The Goal lists C1 as "star rating **+ tag chips + notes**", but Steps 1–6 above
> only covered the star column. The following was added to close that gap (commits
> `f34ebae`, `813c674`). Implemented in `src/pages/data/MoviesPage.vue`.

- [x] **Step 7: Tag chips + notes in an expandable row**

  Add a `{ type: 'expand', renderExpand }` column to the movie table. The expanded
  panel holds:
  - **Tags** — `NSelect` (`multiple`) over the 12 `VALID_TAGS` slugs (mirrors the
    backend `VALID_TAGS`; labelled via i18n `movies.tags.<slug>`), saving on change.
  - **Notes** — a *controlled* `NInput` textarea backed by a per-row draft map,
    persisting **on blur only** (not per keystroke). (An earlier uncontrolled
    `defaultValue` version was replaced after review — it could drop in-progress
    text when a sibling control re-rendered the panel.)

- [x] **Step 8: Merge-preserving `saveRating()` (correctness)**

  Because the backend rating upsert is full-replace, introduce a pure helper
  `rating-merge.ts` → `mergeRating(current, patch)` that fills `rating`/`tags`/`notes`
  from the patch when the key is present (so an explicit `null` clears) else from the
  current row. Route **all four** save paths — star `NRate`, tags select, notes blur,
  and the C3 batch-annotation save — through a single `saveRating(href, patch)` that
  merges then PUTs, so editing one field never wipes the others. Unit-tested in
  `tests/unit/rating-merge.spec.ts`.

- [x] **Step 9: Reactive heart map**

  The `/data` actor `HeartButton` `onChange` reassigns a **new** `Map`
  (not in-place `.set`) so the B3 Score column recomputes when an actor heart toggles.

### Task 3b — B3 score + C4 hearts on `/browse` (ResolveCard)

> ADR-022 §B3 requires the score on **both** `/data` and `/browse`, and §C4 requires
> hearts "on all pages that display these dimensions". Implemented in
> `src/components/browse/ResolveCard.vue` (commits `b9828e6`, `b6bf218`).

- [x] **Step 1: Pure helper** — `resolve-preferences.ts` exposes
  `resolvePreferenceScore(rating, actors, actorHearted)` delegating to the shared
  `computePreferenceScore` (formula unchanged). Unit-tested in
  `tests/unit/resolve-preferences.spec.ts`.
- [x] **Step 2: Score chip** in the detail header (gated to the `detail` branch).
- [x] **Step 3: Hearts** on actor chips (`content_type: actor`) and tag/category
  chips (`content_type: category`), using the name-as-id convention so rows are
  shared with `/data`; initial state loaded via `listContentPreferences`.
- [x] **Step 4: Robustness** — `getMovieRating(href, { skipErrorToast: true })` so the
  "unrated ⇒ 404" common case does not pop an error toast; a latest-wins `reqUrl`
  guard prevents a stale resolve from overwriting a newer one.

---

## Task 4 — Batch annotation mode (C3)

**Files:**
- Modify: `src/pages/DataPage.vue`

- [ ] **Step 1: Add batch mode state**

```typescript
const batchMode = ref(false);
const focusedIndex = ref(0);
const pendingRating = ref<number | null>(null);
```

- [ ] **Step 2: Add keyboard handler**

```typescript
function handleKeydown(e: KeyboardEvent) {
  if (!batchMode.value) return;

  switch (e.key) {
    case 'j':
      focusedIndex.value = Math.min(focusedIndex.value + 1, rows.value.length - 1);
      break;
    case 'k':
      focusedIndex.value = Math.max(focusedIndex.value - 1, 0);
      break;
    case '1': case '2': case '3': case '4': case '5':
      pendingRating.value = Number(e.key);
      break;
    case 'Enter':
      if (pendingRating.value !== null) {
        const row = rows.value[focusedIndex.value];
        upsertMovieRating(row.href, { rating: pendingRating.value }).then(loadRatings);
      }
      focusedIndex.value = Math.min(focusedIndex.value + 1, rows.value.length - 1);
      pendingRating.value = null;
      break;
    case ' ':
      e.preventDefault();
      focusedIndex.value = Math.min(focusedIndex.value + 1, rows.value.length - 1);
      break;
  }
}

onMounted(() => window.addEventListener('keydown', handleKeydown));
onUnmounted(() => window.removeEventListener('keydown', handleKeydown));
```

- [ ] **Step 3: Add toggle button in page header**

```vue
<NButton
  :type="batchMode ? 'primary' : 'default'"
  size="small"
  @click="batchMode = !batchMode; focusedIndex = 0; pendingRating = null"
>
  {{ batchMode ? 'Exit Annotate' : 'Annotate' }}
</NButton>
```

- [ ] **Step 4: Highlight focused row**

In the table's `row-props` or `row-class-name` option, highlight the focused row when in batch mode:

```typescript
rowProps: (row: MovieHistoryRow, index: number) => ({
  style: batchMode.value && index === focusedIndex.value
    ? 'background: rgba(24,160,88,0.12);'
    : '',
}),
```

- [ ] **Step 5: Verify in browser**

Navigate to `/data`. Click "Annotate". Confirm:
- `j`/`k` moves highlight between rows.
- Pressing `1`–`5` sets a pending rating (show pending value in UI if desired).
- Pressing `Enter` saves the rating and advances to the next row.
- Pressing `Space` skips without rating.
- Clicking "Exit Annotate" deactivates batch mode.

- [ ] **Step 6: Commit**

```bash
git add src/pages/DataPage.vue
git commit -m "feat(frontend): add C3 batch annotation mode (ADR-022)"
```

---

## Definition of Done

| # | Gate | Check | Status |
|---|------|-------|--------|
| 1 | Preferences API client compiles | `npm run typecheck` → no errors | ✅ |
| 2 | HeartButton renders | `/data/movies` shows heart icon next to actor name | ✅ |
| 3 | C1 rating saves to DB | Click star on a row → PUT `/api/preferences/movies/.../rating` → 200 | ✅ |
| 4 | C1 tags + notes save | Expand a row → select tags / type notes (blur) → PUT persists; sibling fields preserved (merge) | ✅ |
| 5 | B3 score column visible (`/data`) | `/data/movies` shows "Score" column with values 0.00–1.00 | ✅ |
| 6 | B3 score visible (`/browse`) | Resolve a URL → detail card shows a "Score" chip | ✅ |
| 7 | C3 batch mode activates | Click "Annotate" → keyboard `j`/`k`/`1–5`/`Enter`/`Space` all work | ✅ |
| 8 | C4 heart persists (actor) | Click heart on actor (`/data` or `/browse`) → PUT `/api/preferences/actor/...` → 200; persists on refresh | ✅ |
| 9 | C4 heart persists (category) | Click heart on a `/browse` tag/category chip → PUT `/api/preferences/category/...` → 200 | ✅ |
| 10 | Unit suite green | `npm run typecheck && npm run lint && npm run test:unit` all pass | ✅ |
| 11 | C4 maker/director hearts | Heart on maker/director chips | ⛔ Deferred — no maker/director chips rendered yet (separate follow-up) |
