# IMP-ADR047-01: ADR-047 Phase 1 — Reconcile Dual-Backend Drift (cross-repo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-047](ADR-047-dual-backend-drift-reconciliation.md) — this is **Phase 1 (Reconcile)**. Phase 2 (Guard) is IMP-ADR047-02.

**Goal:** Make the two backends return the same answer for the three confirmed drift bugs — session `write_mode` default, history `total_estimate` cap, and the stats `/summary` aggregates (`total_torrents`, `avg_duration_seconds`, plus the `total_dedup_freed_bytes` filter) — per the ADR-047 decisions.

**Architecture:** This spans **two separate git repos**: the Python backend (this repo, `/Users/tedwu/JAVDB_AutoSpider_CICD`, branch `adr047-dual-backend-drift`) and the TypeScript Worker (`JAVDB_AutoSpider_Web/`, a **separate git repo**, origin `TongWu/JAVDB_AutoSpider_Web`). Each repo's changes commit on its own branch and ship as its own PR. The fixes are independent per field, so task order does not matter; each task makes one backend match the ADR-decided canonical behavior.

**Tech Stack:** Python 3 + `pytest` (run via the broken-venv workaround); TypeScript + `vitest`.

**Repo context & test commands (read before every task):**
- **Python repo** (`/Users/tedwu/JAVDB_AutoSpider_CICD`): the `.venv` is broken — run tests ONLY as `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <files> -q`. Commit on branch `adr047-dual-backend-drift` with `git -c user.name=Ted -c user.email=ted@wu.engineer commit`.
- **TS repo** (`JAVDB_AutoSpider_Web/`): `cd JAVDB_AutoSpider_Web` first; run tests as `npm run test:server` (vitest, config `vitest.server.config.ts`) or `npx vitest run server/__tests__/<file>`. Create a branch there (`git -C JAVDB_AutoSpider_Web switch -c adr047-dual-backend-drift`) and commit in that repo. It is a **distinct PR** to `TongWu/JAVDB_AutoSpider_Web`.
- Every commit message ends with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

**ADR-decided canonical behavior (baked in):** `write_mode` NULL → `"pending"`; `total_estimate` → capped at 10000 both sides; `/summary.total_torrents` → counts `TorrentHistory`; `/summary.avg_duration_seconds` → computed from `CommittedAt` both sides; `total_dedup_freed_bytes` → `WHERE IsDeleted=1` both sides; `proxy_bans_last_7d` + `/capabilities` env fields → deployment-intrinsic, left as-is.

---

## File Structure

| Repo | Path | Modify/Test | Responsibility |
| --- | --- | --- | --- |
| Python | `javdb/storage/repos/sessions_repo.py` | Modify | `write_mode` fallback `"audit"`→`"pending"`; remove dead `SessionList.total_estimate` |
| Python | `apps/api/routers/stats.py` | Modify | `/summary`: `total_torrents`→`TorrentHistory`; compute `avg_duration_seconds` |
| Python | `tests/unit/test_adr047_summary_reconcile.py` | **Create** | Pins the stats `/summary` query changes |
| Python | `tests/unit/test_adr047_write_mode.py` | **Create** | Pins `write_mode` NULL→`pending` |
| TS | `JAVDB_AutoSpider_Web/server/routes/history.ts` | Modify | Cap `total_estimate` count at 10000 (movie + torrent) |
| TS | `JAVDB_AutoSpider_Web/server/routes/stats.ts` | Modify | dedup filter `Status='completed'`→`IsDeleted=1` |
| TS | `JAVDB_AutoSpider_Web/server/__tests__/adr047-reconcile.test.ts` | **Create** | Pins the TS cap + dedup-filter changes |

---

## Task 1 (Python repo): `write_mode` NULL → `"pending"` + drop dead `total_estimate`

**Files:** Modify `javdb/storage/repos/sessions_repo.py`; create `tests/unit/test_adr047_write_mode.py`.

- [ ] **Step 1.1 — Write the failing test.** Create `tests/unit/test_adr047_write_mode.py`:

```python
"""ADR-047 D1a: a session row with NULL WriteMode maps to 'pending' (not the
retired 'audit'), matching the TS backend."""
from javdb.storage.repos.sessions_repo import _row_to_session


def _row(**over):
    base = {
        "Id": "S1", "Status": None, "WriteMode": None,
        "RunId": None, "RunAttempt": None, "DateTimeCreated": "2026-06-02T00:00:00Z",
        "ReportType": None, "ReportDate": None, "FailureReason": None,
    }
    base.update(over)
    return base


def test_null_write_mode_defaults_to_pending():
    assert _row_to_session(_row(WriteMode=None)).write_mode == "pending"


def test_explicit_write_mode_preserved():
    assert _row_to_session(_row(WriteMode="pending")).write_mode == "pending"
```

(`_row_to_session` reads rows via `r["x"]` and `r.keys()`; a plain `dict` satisfies both, so no DB is needed.)

- [ ] **Step 1.2 — Run it, verify it FAILS.** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_adr047_write_mode.py -q` → FAIL (`test_null_write_mode_defaults_to_pending` gets `"audit"`).

- [ ] **Step 1.3 — Fix the fallback.** In `javdb/storage/repos/sessions_repo.py`, in `_row_to_session`, replace:

```python
        write_mode=r["WriteMode"] or "audit",
```
with:
```python
        write_mode=r["WriteMode"] or "pending",  # ADR-047 D1a: 'audit' retired (ADR-005 PR-4)
```

- [ ] **Step 1.4 — Drop the dead `total_estimate` field (guarded).** First confirm it is unused:

```bash
grep -rn "\.total_estimate\|total_estimate=" javdb apps tests --include='*.py' | grep -i session
```
Expected: only the `SessionList` dataclass declaration. **If** the grep shows no other reader/writer, replace the dataclass:
```python
@dataclass
class SessionList:
    items: list[SessionRow]
    next_cursor: str | None
    total_estimate: int | None = None
```
with:
```python
@dataclass
class SessionList:
    items: list[SessionRow]
    next_cursor: str | None
```
**If** the grep shows any other reference, SKIP this removal (leave the field) and note it in the task report — the `write_mode` fix is the load-bearing part of this task.

- [ ] **Step 1.5 — Run it, verify PASS.** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_adr047_write_mode.py tests/unit/test_sessions_repo.py -q` (include the existing sessions-repo suite if present) → PASS. If any existing test asserted the old `"audit"` default, update it to `"pending"` (intended contract change).

- [ ] **Step 1.6 — Commit (Python repo).**
```bash
git add javdb/storage/repos/sessions_repo.py tests/unit/test_adr047_write_mode.py
git -c user.name=Ted -c user.email=ted@wu.engineer commit -m "fix(api): session write_mode NULL defaults to pending, not retired audit (ADR-047 D1a)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 (Python repo): stats `/summary` — count `TorrentHistory`, compute `avg_duration`

**Files:** Modify `apps/api/routers/stats.py`; create `tests/unit/test_adr047_summary_reconcile.py`.

- [ ] **Step 2.1 — Write the failing test.** Create `tests/unit/test_adr047_summary_reconcile.py`:

```python
"""ADR-047 D1c: /summary counts TorrentHistory (not ReportTorrents) and computes
avg_duration from CommittedAt, matching the TS backend. DB-free: spy on the query
helper and assert the SQL the handler issues."""
import apps.api.routers.stats as stats


def test_summary_counts_torrent_history_and_computes_avg_duration(monkeypatch):
    calls = []
    monkeypatch.setattr(stats, "_safe_query_one", lambda db, sql, *a, **k: (calls.append(sql), 0)[1])
    monkeypatch.setattr(stats, "_count_proxy_bans_in_logs", lambda *_: 0)

    stats.stats_summary(_user={"sub": "test"})

    assert "SELECT COUNT(*) FROM TorrentHistory" in calls
    assert all("ReportTorrents" not in s for s in calls), "must not count ReportTorrents"
    assert any("CommittedAt" in s for s in calls), "avg_duration must query CommittedAt"
```

- [ ] **Step 2.2 — Run it, verify it FAILS.** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_adr047_summary_reconcile.py -q` → FAIL (handler still counts `ReportTorrents`; no `CommittedAt` query).

- [ ] **Step 2.3 — Count `TorrentHistory`.** In `apps/api/routers/stats.py` `stats_summary`, replace:
```python
    total_torrents = _safe_query_one(
        REPORTS_DB_PATH,
        "SELECT COUNT(*) FROM ReportTorrents",
    ) or 0
```
with:
```python
    # ADR-047 D1c: "total torrents" = canonical history total (matches TS).
    total_torrents = _safe_query_one(
        HISTORY_DB_PATH,
        "SELECT COUNT(*) FROM TorrentHistory",
    ) or 0
```
(`HISTORY_DB_PATH` is already imported — `total_movies` uses it.)

- [ ] **Step 2.4 — Compute `avg_duration_seconds`.** Still in `stats_summary`, immediately before the `return StatsSummary(`, add:
```python
    # ADR-047 D1c: compute avg committed-session duration (mirrors the TS backend).
    avg_duration_raw = _safe_query_one(
        REPORTS_DB_PATH,
        "SELECT AVG(CAST((julianday(CommittedAt) - julianday(DateTimeCreated)) * 86400 AS INTEGER)) "
        "FROM ReportSessions WHERE Status='committed' AND CommittedAt IS NOT NULL",
    )
    avg_duration_seconds = round(float(avg_duration_raw)) if avg_duration_raw is not None else None
```
and replace the return field:
```python
        avg_duration_seconds=None,
```
with:
```python
        avg_duration_seconds=avg_duration_seconds,
```
Leave `proxy_bans_last_7d=_count_proxy_bans_in_logs(7)` unchanged (ADR-047 D2: deployment-intrinsic).

- [ ] **Step 2.5 — Run it, verify PASS.** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_adr047_summary_reconcile.py tests/unit/test_stats*.py -q` → PASS. Fix any existing stats test that asserted the old `ReportTorrents`/`None` behavior (intended change).

- [ ] **Step 2.6 — Commit (Python repo).**
```bash
git add apps/api/routers/stats.py tests/unit/test_adr047_summary_reconcile.py
git -c user.name=Ted -c user.email=ted@wu.engineer commit -m "fix(api): /summary counts TorrentHistory + computes avg_duration to match TS backend (ADR-047 D1c)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 (TS repo): cap `total_estimate` at 10000 + align dedup filter

**Repo:** `JAVDB_AutoSpider_Web/` (separate git repo). **Files:** Modify `server/routes/history.ts`, `server/routes/stats.ts`; create `server/__tests__/adr047-reconcile.test.ts`.

- [ ] **Step 3.0 — Branch the TS repo.**
```bash
git -C JAVDB_AutoSpider_Web switch -c adr047-dual-backend-drift
```

- [ ] **Step 3.1 — Write the failing test.** Create `JAVDB_AutoSpider_Web/server/__tests__/adr047-reconcile.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// ADR-047 D1b/D1c: the TS count statements cap total_estimate at 10000, and the
// dedup-freed query filters on IsDeleted=1 (matching the Python backend).
const read = (p: string) => readFileSync(join(__dirname, "..", "routes", p), "utf-8");

describe("ADR-047 reconciliation", () => {
  it("history total_estimate counts are capped at 10000", () => {
    const src = read("history.ts");
    const countLines = src.split("\n").filter((l) => l.includes("COUNT(*)") && l.includes("cnt"));
    expect(countLines.length).toBeGreaterThanOrEqual(2);
    for (const l of countLines) expect(l).toContain("MIN(COUNT(*), 10000)");
  });

  it("dedup-freed query filters on IsDeleted=1, not Status='completed'", () => {
    const src = read("stats.ts");
    expect(src).toContain("IsDeleted=1");
    expect(src).not.toContain("Status='completed'");
  });
});
```
(A source-assertion test is the robust, DB-free way to pin these — the Worker's D1 binding is not available in unit tests. The byte-level guard comes in Phase 2 via the golden.)

- [ ] **Step 3.2 — Run it, verify FAILS.** `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/adr047-reconcile.test.ts` → FAIL (no `MIN(...)`; `Status='completed'` present).

- [ ] **Step 3.3 — Cap the movie count.** In `server/routes/history.ts`, replace:
```typescript
  const countSql = `SELECT COUNT(*) AS cnt FROM MovieHistory m ${where}`;
```
with:
```typescript
  // ADR-047 D1b: cap to match the Python backend (bounded D1 COUNT cost).
  const countSql = `SELECT MIN(COUNT(*), 10000) AS cnt FROM MovieHistory m ${where}`;
```

- [ ] **Step 3.4 — Cap the torrent count.** In the same file, replace:
```typescript
  const countSql = `
    SELECT COUNT(*) AS cnt
    FROM TorrentHistory t
    JOIN MovieHistory m ON m.Id = t.MovieHistoryId
    ${where}`;
```
with:
```typescript
  // ADR-047 D1b: cap to match the Python backend.
  const countSql = `
    SELECT MIN(COUNT(*), 10000) AS cnt
    FROM TorrentHistory t
    JOIN MovieHistory m ON m.Id = t.MovieHistoryId
    ${where}`;
```

- [ ] **Step 3.5 — Align the dedup filter.** In `server/routes/stats.ts`, find the dedup-freed query (around line 93, `WHERE Status='completed'`) and change its `WHERE` to `WHERE IsDeleted=1` so it matches the Python query (`SELECT COALESCE(SUM(ExistingFolderSize), 0) FROM DedupRecords WHERE IsDeleted=1`). Confirm by reading the surrounding lines first; replace only the `WHERE` predicate of that one statement. (`DedupRecords.IsDeleted` is the schema column; `Status` is not a DedupRecords column.)

- [ ] **Step 3.6 — Run it, verify PASS + full server suite.** `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/adr047-reconcile.test.ts && npm run test:server` → both PASS. Fix any existing TS test that asserted the uncapped count or the old dedup filter.

- [ ] **Step 3.7 — Commit (TS repo).**
```bash
git -C JAVDB_AutoSpider_Web add server/routes/history.ts server/routes/stats.ts server/__tests__/adr047-reconcile.test.ts
git -C JAVDB_AutoSpider_Web -c user.name=Ted -c user.email=ted@wu.engineer commit -m "fix(server): cap total_estimate at 10000 + dedup filter IsDeleted=1 to match Python (ADR-047 D1b/D1c)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: cross-backend sanity + scope note

- [ ] **Step 4.1 — Confirm the reconciled fields now agree (manual reasoning + the per-task tests).** For each fixed field, the two backends now issue equivalent queries: `write_mode` NULL→`pending` (both); `total_estimate` `MIN(COUNT(*),10000)` (both); `/summary.total_torrents` ← `TorrentHistory` (both); `/summary.avg_duration_seconds` ← `CommittedAt` formula (both); `total_dedup_freed_bytes` ← `IsDeleted=1` (both). Record this in the task report.

- [ ] **Step 4.2 — Confirm the deliberately-divergent fields are untouched (ADR-047 D2/D3).** `proxy_bans_last_7d` (Python log-scan / TS 0), `/capabilities` `storage_backend`/`deployment`/`git_sha`, revocation enforcement scope, and the `plain:` dev hatch remain as-is — they are deployment-intrinsic or owned by ADR-029. No code change; just verify the diff did not touch them.

- [ ] **Step 4.3 — Full Python unit suite (no regressions).** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit tests/smoke -q`. Expect green except any pre-existing unrelated failures (a stale Rust `.so` test and a flaky `movie_sleep_mgr` test are known-pre-existing per ADR-046 Phase 1 notes — confirm any failure is one of those, not introduced here).

> **Guard note:** Phase 1 only *reconciles* the values. The mechanical guard that stops them re-diverging (golden cases for the count statements + a `response-values.golden.json` Contract-Values fixture) is **Phase 2 — IMP-ADR047-02**. Do not add the guard here.

## Out of Scope (Phase 2 or later)

- The Contract-Values fixture + golden extension (IMP-ADR047-02).
- Any change to `proxy_bans`, `/capabilities` env fields, auth revocation scope, the `plain:` hatch (ADR-047 D2/D3).
- ADR-018 Phase 3 ("eliminate" the builders).

## Self-Review

- **Spec coverage:** ADR-047 D1a→Task 1; D1b→Task 3.3/3.4; D1c→Task 2 (`total_torrents`, `avg_duration`) + Task 3.5 (`total_dedup` filter); D4 dead-field→Task 1.4; D2/D3 untouched→Task 4.2. All Phase-1 decisions mapped.
- **Cross-repo:** Tasks 1–2 commit in the Python repo; Task 3 commits in the `JAVDB_AutoSpider_Web` repo (separate branch + PR). Test commands differ per repo (documented in the header).
- **No placeholders:** every code step shows exact before/after from the current source.
