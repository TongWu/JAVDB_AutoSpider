# Dual-Backend Query Contract — Phase 2: TS Consumption, Drift CI & Dispatch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **This IMP spans two repos** — see the Repos note before starting.

**Goal:** Consume the Contract Golden in the TypeScript backend and make drift fail CI. Pin the TS query builders to the vendored golden (catches TS-side drift), add a CI freshness step that re-fetches the Python-`main` golden and diffs it against the vendored copy (catches Python-side drift), and automate re-vendoring via `repository_dispatch`. This realizes ADR-018 D5/D6.

**Architecture:** Mirror the existing `openapi.json` → `api.gen.ts` pipeline exactly. The golden is **vendored** (committed) into the TS repo like `src/types/api.gen.ts`; a `gen:query-golden` script (mirroring `fetch-openapi.mjs`) refreshes it; `ci.yml` runs a gen-diff freshness check (mirroring the `api.gen.ts` step) plus a vitest conformance test. A Python-repo workflow dispatches to the TS repo on golden change.

**Tech Stack:** TypeScript, Hono, Vitest (`@cloudflare/vitest-pool-workers`), GitHub Actions (`repository_dispatch`, `peter-evans/create-pull-request`). Node `fetch-*.mjs` scripts.

**Repos:**
- **TS repo** — `TongWu/JAVDB_AutoSpider_Web` (checked out at `JAVDB_AutoSpider_Web/`). All TS paths below are relative to its root.
- **Python repo** — `TongWu/JAVDB_AutoSpider_CICD` (this repo). Hosts the golden + the dispatch-source workflow.

**Related:** [ADR-018](ADR-018-dual-backend-query-contract.md), [IMP-ADR018-01](IMP-ADR018-01-python-golden-generator.md) (must land first)

**Status:** Implemented (2026-05-30) — Tasks 1–7 done and locally verified across both repos. TS builders extracted (`buildMovieWhere`/`buildTorrentWhere`/`buildSessionQuery`); golden vendored; vitest conformance green (25 golden cases); `ci.yml` gains the freshness gen-diff + a `test:server` step (CI did not previously run `test:server`); Python `publish-query-contract.yml` + TS `revendor-query-golden.yml` dispatch pair created. **Pre-existing drift reconciled:** the TS `/sessions` handler now over-fetches `limit + 1` to match the Python golden (fixes a phantom `next_cursor` at exact-multiple boundaries); pinned by two new route tests. Both drift simulations confirmed the guard fails as intended. **Task 8 (stats) remains deferred** (blocked on the Python router→builder extraction). **Pending ops (cannot be done from code):** create `WEB_REPO_DISPATCH_TOKEN` (PAT, `contents:write` on the web repo) in the Python repo's `Production` environment **and** `REVENDOR_PR_TOKEN` (PAT, `contents:write` + `pull-requests:write` on the web repo) in the TS repo's secrets — may be the same PAT (so the re-vendor PR triggers CI); post-merge CI dry-run + `workflow_dispatch` dispatch dry-run.

---

## Prerequisites

- [ ] **IMP-ADR018-01 is merged on Python `main`** — `docs/api/contract/query-builders.golden.json` exists with `movie_filters`, `torrent_filters`, and `session_query` cases, and the content-hash `version` field (D6).
- [ ] A PAT secret with **read** access to the private Python repo is available to TS CI as `CICD_REPO_TOKEN` (already used by the `api.gen.ts` step in `ci.yml`).
- [ ] A PAT secret with **write** access to the TS repo is available to the Python repo as `WEB_REPO_DISPATCH_TOKEN` (new — Task 6).
- [ ] A PAT secret with **write** access to the TS repo (`contents:write` + `pull-requests:write`) is available to TS CI as `REVENDOR_PR_TOKEN` (new — Task 7; needed so the re-vendor PR triggers CI). May be the same PAT as `WEB_REPO_DISPATCH_TOKEN`.

---

## Scope & deviations

- **Comparable unit = the builder's own output.** For `movie_filters`/`torrent_filters` the golden pins the **WHERE clause + bindings** (what Python `_build_movie_filters` returns and what the TS builder assembles into its local `where`). For `session_query` it pins the **full SQL + bindings**. The SELECT skeleton of the movie query (column list, `LEFT JOIN`, `GROUP BY`, `ORDER BY m.Id`) is **not** pinned by this golden — it is near-static; revisit only if it drifts.
- **Builder-input level.** The golden's `params` are the **decoded builder inputs** (Python kwarg names: `q`, `perfect_match`, `cursor_id`, …), not raw wire query params. The TS conformance test maps golden params → the TS builder input and compares output. The wire-decode layer (cursor base64 scheme, `"true"`→bool) is each repo's own concern; pin it separately later via cursor vectors if it drifts (out of scope here).
- **`stats` is gated.** TS `stats.ts` aggregations can only be pinned once the golden carries `stats` cases, which needs the Python router→builder extraction first (deferred from IMP-01). Task 8 is **blocked** on that prerequisite and may ship in a follow-up.

---

## File Map

### TS repo (`TongWu/JAVDB_AutoSpider_Web`)

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Modify | `server/routes/history.ts` | Extract `buildMovieWhere(input)` / `buildTorrentWhere(input)` returning `{ where, bindings }`; `buildMovieQuery`/`buildTorrentQuery` delegate |
| Modify | `server/routes/sessions.ts` | Extract `buildSessionQuery(input)` returning `{ sql, bindings }`; the `/sessions` handler delegates |
| Create | `scripts/fetch-query-golden.mjs` | Mirror of `fetch-openapi.mjs`: resolve golden (local path / raw URL+token), write vendored copy |
| Create | `server/__tests__/fixtures/query-builders.golden.json` | **Vendored** golden (generated output, committed) |
| Create | `server/__tests__/query-contract.test.ts` | Conformance: map golden cases → TS builders, assert normalized SQL + bindings |
| Modify | `package.json` | Add `"gen:query-golden": "node scripts/fetch-query-golden.mjs"` |
| Modify | `.github/workflows/ci.yml` | Add golden freshness gen-diff step (mirror the `api.gen.ts` step) |
| Create | `.github/workflows/revendor-query-golden.yml` | `on: repository_dispatch`; run `gen:query-golden`, open re-vendor PR |

### Python repo (`TongWu/JAVDB_AutoSpider_CICD`)

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `.github/workflows/publish-query-contract.yml` | Mirror `publish-openapi.yml`: regenerate golden on `main`, commit, **dispatch to TS repo on change** |

---

## Task 1: Extract pure builders in the TS backend (behavior-preserving)

The golden's comparable unit is the WHERE/query assembly. Extract it so it can be exercised directly, mirroring the Python `_build_*` functions.

- [ ] In `server/routes/history.ts`, extract from `buildMovieQuery` (L59):

```ts
// Input keys mirror the Python _build_movie_filters kwargs (decoded).
export interface MovieFilterInput {
  q?: string; actor?: string; perfect_match?: boolean; hi_res?: boolean;
  session_id?: string; date_from?: string; date_to?: string; cursor_id?: number;
}
export function buildMovieWhere(input: MovieFilterInput): { where: string; bindings: (string | number)[] } {
  const conditions: string[] = [];
  const bindings: (string | number)[] = [];
  if (input.cursor_id !== undefined) { conditions.push("m.Id > ?"); bindings.push(input.cursor_id); }
  if (input.q !== undefined) {
    const like = `%${input.q}%`;
    conditions.push("(m.VideoCode LIKE ? OR m.ActorName LIKE ? OR m.SupportingActors LIKE ?)");
    bindings.push(like, like, like);
  }
  // ... port every remaining branch from the current buildMovieQuery, in the SAME order as Python
  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  return { where, bindings };
}
```

- [ ] `buildMovieQuery` now decodes wire params → `MovieFilterInput`, calls `buildMovieWhere`, and assembles `selectSql`/`countSql` around the returned `where` — **no behavior change** (same SQL, same bindings, same ordering).
- [ ] Repeat for `buildTorrentWhere` (from `buildTorrentQuery`, L197).
- [ ] In `server/routes/sessions.ts`, extract `buildSessionQuery(input: { state?: string; cursor_sid?: string; limit: number }) -> { sql: string; bindings: (string|number)[] }` matching the Python `_build_session_query` full SQL (`... ORDER BY Id DESC LIMIT ?`). The `/sessions` handler decodes the cursor then delegates.
- [ ] **Verify (regression):** `npm run test:server` and `npm run test:unit` — existing history/sessions route tests pass unchanged.

> The branch **order** must match Python (cursor → q → actor → …) so the joined WHERE string is byte-identical after normalization. Order mismatch is exactly what the golden will catch.

---

## Task 2: Vendor script + vendored golden

- [ ] Create `scripts/fetch-query-golden.mjs`, mirroring `fetch-openapi.mjs`:

```js
#!/usr/bin/env node
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT = path.join(ROOT, 'server', '__tests__', 'fixtures', 'query-builders.golden.json')

const SRC_PATH = process.env['QUERY_GOLDEN_PATH'] ?? ''
const SRC_URL = process.env['QUERY_GOLDEN_URL'] ??
  'https://raw.githubusercontent.com/TongWu/JAVDB_AutoSpider_CICD/main/docs/api/contract/query-builders.golden.json'
const TOKEN = process.env['QUERY_GOLDEN_TOKEN'] ?? process.env['OPENAPI_TOKEN'] ?? process.env['GITHUB_TOKEN'] ?? ''

async function resolve() {
  if (SRC_PATH) return await readFile(SRC_PATH, 'utf-8')
  const res = await fetch(SRC_URL, { headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {} })
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${SRC_URL}`)
  return await res.text()
}

const json = await resolve()
JSON.parse(json) // validate
await mkdir(path.dirname(OUT), { recursive: true })
await writeFile(OUT, json, 'utf-8')
console.log(`[fetch-query-golden] wrote ${OUT}`)
```

- [ ] Add to `package.json` scripts: `"gen:query-golden": "node scripts/fetch-query-golden.mjs"`.
- [ ] Vendor the current golden: `QUERY_GOLDEN_PATH=../docs/api/contract/query-builders.golden.json npm run gen:query-golden`, then **commit** `server/__tests__/fixtures/query-builders.golden.json`.

---

## Task 3: Vitest conformance test (pins TS builders to vendored golden)

- [ ] Create `server/__tests__/query-contract.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import golden from "./fixtures/query-builders.golden.json";
import { buildMovieWhere, buildTorrentWhere } from "../routes/history";
import { buildSessionQuery } from "../routes/sessions";

// MUST be byte-identical to the Python normalize_sql (collapse whitespace runs + trim).
const normalizeSql = (s: string) => s.replace(/\s+/g, " ").trim();

const RUN: Record<string, (p: any) => { sql: string; bindings: (string|number)[] }> = {
  movie_filters:   (p) => { const r = buildMovieWhere(p);   return { sql: r.where, bindings: r.bindings }; },
  torrent_filters: (p) => { const r = buildTorrentWhere(p); return { sql: r.where, bindings: r.bindings }; },
  session_query:   (p) => buildSessionQuery(mapSessionParams(p)),
};

describe("ADR-018 query Contract Golden conformance", () => {
  for (const c of (golden as any).cases) {
    it(`${c.builder}:${c.name}`, () => {
      const { sql, bindings } = RUN[c.builder](c.params);
      expect(normalizeSql(sql)).toBe(c.sql);
      expect(bindings).toEqual(c.bindings);
    });
  }
});
```

- [ ] Implement `mapSessionParams` to translate the golden's session params (incl. the pre-encoded `cursor`) into `buildSessionQuery`'s input (decode the cursor with the TS `cursorDecode`), so the same decoded `sid` flows in.
- [ ] Ensure `query-contract.test.ts` runs under `test:server` (its include glob is `server/__tests__/**/*.test.ts`).
- [ ] **Run:** `npm run test:server`.

---

## Task 4: Reconcile any pre-existing drift

The first conformance run may go **red** because the two builders already diverge (e.g., a cursor operator, a branch order, a `LIKE` shape). That is the guard doing its job on day one.

- [ ] For each failure: decide the single correct form, fix the TS builder (and, if Python is wrong, fix Python + `python -m apps.cli.ops.dump_query_contract` + re-vendor via Task 2).
- [ ] Re-run until `npm run test:server` is green with the committed vendored golden.

---

## Task 5: CI freshness gen-diff (mirror the `api.gen.ts` step)

- [ ] In `.github/workflows/ci.yml`, add a step modeled on the existing "Regenerate API types" step:

```yaml
      - name: Refresh query Contract Golden from main repo
        # Confirms the committed vendored golden matches Python main. Fails on drift.
        env:
          QUERY_GOLDEN_URL: https://raw.githubusercontent.com/TongWu/JAVDB_AutoSpider_CICD/main/docs/api/contract/query-builders.golden.json
          QUERY_GOLDEN_TOKEN: ${{ secrets.CICD_REPO_TOKEN }}
        run: |
          npm run gen:query-golden
          if ! git diff --quiet server/__tests__/fixtures/query-builders.golden.json; then
            echo "::error::Vendored query golden is out of sync with Python main."
            echo "A builder changed upstream — run 'npm run gen:query-golden' and reconcile the TS builders."
            git --no-pager diff server/__tests__/fixtures/query-builders.golden.json | head -100
            exit 1
          fi
```

- [ ] Confirm the existing "Contract tests" / `test:server` step runs the new conformance test in the same job.

---

## Task 6: Python-repo dispatch source (`publish-query-contract.yml`)

- [ ] In the **Python repo**, create `.github/workflows/publish-query-contract.yml`, mirroring `publish-openapi.yml`:

```yaml
name: Publish query Contract Golden
on:
  push:
    branches: [main]
    paths:
      - 'javdb/storage/repos/**'
      - 'apps/cli/ops/query_contract_cases.py'
      - 'apps/cli/ops/dump_query_contract.py'
  workflow_dispatch:
permissions:
  contents: write
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: Production
    steps:
      - uses: actions/checkout@v4
        with: { ssh-key: ${{ secrets.DEPLOY_KEY }} }
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -r requirements.txt
      - name: Regenerate golden
        run: python -m apps.cli.ops.dump_query_contract
      - name: Self-commit golden if a builder PR forgot to regenerate it
        id: commit
        run: |
          set -e
          if git diff --quiet docs/api/contract/query-builders.golden.json; then
            echo "self_committed=false" >> "$GITHUB_OUTPUT"; exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/api/contract/query-builders.golden.json
          git commit -m "docs(api): regenerate query Contract Golden [skip ci]"
          git push
          echo "self_committed=true" >> "$GITHUB_OUTPUT"
      - name: Did this push change the golden?
        id: pushdiff
        run: |
          set -e
          if [ "${{ github.event_name }}" != "push" ]; then
            echo "golden_changed=true" >> "$GITHUB_OUTPUT"; exit 0
          fi
          before="${{ github.event.before }}"
          if [ -z "$before" ] || [ "$before" = "0000000000000000000000000000000000000000" ]; then
            echo "golden_changed=true" >> "$GITHUB_OUTPUT"; exit 0
          fi
          if git diff --name-only "$before" "${{ github.sha }}" \
               | grep -qx 'docs/api/contract/query-builders.golden.json'; then
            echo "golden_changed=true" >> "$GITHUB_OUTPUT"
          else
            echo "golden_changed=false" >> "$GITHUB_OUTPUT"
          fi
      - name: Dispatch re-vendor to web repo
        if: steps.commit.outputs.self_committed == 'true' || steps.pushdiff.outputs.golden_changed == 'true'
        run: |
          curl -fsS -X POST \
            -H "Authorization: Bearer ${{ secrets.WEB_REPO_DISPATCH_TOKEN }}" \
            -H "Accept: application/vnd.github+json" \
            https://api.github.com/repos/TongWu/JAVDB_AutoSpider_Web/dispatches \
            -d '{"event_type":"query-golden-updated"}'
```

> **Dispatch trigger (corrected per PR review):** the dispatch must fire whenever
> the golden changes on `main` — most often the golden diff arrives *already
> committed* inside the builder PR (the ADR-018 D5 flow), so the regenerate step
> is a no-op and `self_committed=false`. Gating dispatch only on a self-commit
> would miss that (by far the common) case and leave the TS vendor permanently
> stale. So dispatch fires on `self_committed == 'true'` **or** when this push's
> diff (`github.event.before..github.sha`, needs `fetch-depth: 0`) touched the
> golden file. Manual `workflow_dispatch` always notifies.

- [ ] Create the `WEB_REPO_DISPATCH_TOKEN` secret (PAT with `contents:write` on the TS repo) in the Python repo's `Production` environment.

---

## Task 7: TS-repo dispatch listener (auto-open re-vendor PR)

- [ ] In the **TS repo**, create `.github/workflows/revendor-query-golden.yml`:

```yaml
name: Re-vendor query Contract Golden
on:
  repository_dispatch:
    types: [query-golden-updated]
  workflow_dispatch:
permissions:
  contents: write
  pull-requests: write
jobs:
  revendor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: ${{ env.NODE_VERSION || '20' }} }
      - run: npm ci
      - name: Refresh vendored golden
        env:
          QUERY_GOLDEN_TOKEN: ${{ secrets.CICD_REPO_TOKEN }}
        run: npm run gen:query-golden
      - name: Open PR
        uses: peter-evans/create-pull-request@v6
        with:
          # PAT with contents:write + pull-requests:write on THIS repo — required
          # so the PR triggers ci.yml. The default GITHUB_TOKEN is suppressed by
          # GitHub for workflow-created PRs (no CI → no conformance-red signal).
          token: ${{ secrets.REVENDOR_PR_TOKEN }}
          branch: chore/revendor-query-golden
          title: "chore: re-vendor query Contract Golden"
          body: |
            Upstream `query-builders.golden.json` changed on Python `main`.
            A builder diverged — **reconcile the TS builders** (`buildMovieWhere` / `buildTorrentWhere` / `buildSessionQuery`) so `npm run test:server` passes, then merge.
          commit-message: "chore: re-vendor query Contract Golden"
```

- [ ] The opened PR's CI runs Task-5 freshness (now green, vendored == main) **and** Task-3 conformance (red until a human reconciles the TS builders) — exactly the intended hand-off.
- [ ] **Token (corrected per PR review):** `create-pull-request` MUST be given a `token:` that is a PAT/App token with write access — without it the action falls back to `GITHUB_TOKEN`, and GitHub suppresses CI on the resulting PR, so the re-vendor PR would open with **no checks** and the conformance-red hand-off would never appear. Add a `REVENDOR_PR_TOKEN` secret to the TS repo (may be the same PAT as `WEB_REPO_DISPATCH_TOKEN`).

---

## Task 8: stats coverage (BLOCKED — prerequisite first)

> Gated on a Python-side router→builder extraction that does not exist yet.

- [ ] **Python repo prerequisite:** extract the `stats.ts`-equivalent aggregations from `apps/api/routers/stats.py` into importable builders, add `stats` cases to `query_contract_cases.py`, regenerate the golden (extends IMP-ADR018-01).
- [ ] **TS repo:** extract the matching builders from `server/routes/stats.ts`, add them to the conformance test's `RUN` map, re-vendor, reconcile.
- [ ] If the prerequisite is not ready, ship Tasks 1–7 and track stats as a follow-up.

---

## Task 9: Verification gates

- [ ] **TS repo:** `npm run test:server` (conformance green), `npm run test:unit`, `npm run typecheck`, `npm run build` all pass.
- [ ] **TS CI dry run:** push a branch; confirm the Task-5 freshness step passes when vendored == main.
- [ ] **Drift simulation (TS side):** change a clause in `buildMovieWhere` without re-vendoring → confirm `test:server` fails with the case id. Revert.
- [ ] **Drift simulation (Python side):** on a Python branch, change `_build_movie_filters` + regenerate golden; point TS `gen:query-golden` at that local golden → confirm the Task-5 freshness diff fails. Revert.
- [ ] **Dispatch dry run:** manually `workflow_dispatch` `publish-query-contract.yml` (Python) → confirm a re-vendor PR opens in the TS repo.
- [ ] Update this IMP's `Status` to `Completed` and check off `IMP-ADR018-02` in the ADR roadmap.

---

## Out of scope

- SELECT skeleton (column list / JOIN / GROUP BY / ORDER) pinning — near-static; revisit only on drift.
- Wire-decode layer (cursor base64, bool coercion) parity vectors — separate, optional.
- Shared filter-spec codegen ("eliminate", D7) → IMP-ADR018-03.
