# IMP-ADR047-02: ADR-047 Phase 2 — Targeted Guard Extension (cross-repo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Related:** [ADR-047](ADR-047-dual-backend-drift-reconciliation.md) — this is **Phase 2 (Guard)**, depends on Phase 1 (IMP-ADR047-01) having reconciled the values first. Extends [ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md)'s Contract Golden.

**Goal:** Stop the Phase-1-reconciled surface from silently re-diverging — by extending ADR-018's SQL Contract Golden to the history `total_estimate` count statements, and pinning the `/summary` static queries + the `write_mode` default with symmetric per-backend unit tests.

**Architecture / design-feedback-loop note:** ADR-047 D5b proposed a *new* `response-values.golden.json` "Contract-Values fixture". While authoring this plan the drifted surface was found to be **almost entirely SQL** (the count statements and the `/summary` queries), which fits ADR-018's existing SQL-golden mechanism directly; the only non-SQL value is the `write_mode` NULL default. Building a whole new fixture type + cross-repo vendor pipeline to guard **one value** is over-engineering. So Phase 2 realizes D5b's *intent* (guard the reconciled surface) as: **(a)** extend the existing SQL golden to the count statements (the cross-repo byte-level guard, where it earns its keep — these wrap the already-guarded WHERE builders), and **(b)** pin the handful of static `/summary` queries and the `write_mode` default with **symmetric unit tests on each backend** (ADR-018 D3's accepted approach for static queries). No new golden artifact. ADR-047 D5b is amended accordingly (see Task 4.4).

**Tech Stack:** Python 3 + `pytest`; TypeScript + `vitest`. No new infrastructure — reuses ADR-018's `dump_query_contract.py` / vendored-golden / CI freshness+conformance pipeline.

**Repo context & test commands:** identical to IMP-ADR047-01 (Python repo: `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest ... -q`, branch `adr047-dual-backend-drift`; TS repo `JAVDB_AutoSpider_Web/`: `npm run test:server`, branch `adr047-dual-backend-drift`, separate PR). Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

| Repo | Path | Modify/Create | Responsibility |
| --- | --- | --- | --- |
| Python | `javdb/storage/repos/history_repo.py` | Modify | Extract behavior-preserving `build_movie_count` / `build_torrent_count` importable builders (the capped count SQL) |
| Python | `apps/cli/ops/dump_query_contract.py` | Modify | Register the 2 count builders in `_BUILDERS`; include their cases in `main()` |
| Python | `apps/cli/ops/query_contract_cases.py` | Modify | Add `MOVIE_COUNT_CASES` / `TORRENT_COUNT_CASES` |
| Python | `docs/api/contract/query-builders.golden.json` | Regenerate | New count cases appear (content-hash `version` changes) |
| Python | `tests/unit/test_adr047_summary_reconcile.py` | Modify | Already pins `/summary` (from Phase 1) — keep as the Python side of the symmetric guard |
| TS | `JAVDB_AutoSpider_Web/server/routes/history.ts` | Modify | Extract `buildMovieCount` / `buildTorrentCount` pure builders |
| TS | `JAVDB_AutoSpider_Web/server/__tests__/query-contract.test.ts` | Modify | Add the 2 count builders to the `RUN` map |
| TS | `JAVDB_AutoSpider_Web/server/__tests__/fixtures/query-builders.golden.json` | Re-vendor | Pull the regenerated golden |
| TS | `JAVDB_AutoSpider_Web/server/__tests__/adr047-reconcile.test.ts` | Modify | Add the TS side of the `/summary` + `write_mode` symmetric guard |

---

## Task 1 (Python repo): extract count builders + add golden cases

**Files:** `javdb/storage/repos/history_repo.py`, `apps/cli/ops/query_contract_cases.py`, `apps/cli/ops/dump_query_contract.py`, regenerate `docs/api/contract/query-builders.golden.json`.

- [x] **Step 1.1 — Read the current count construction.** Open `javdb/storage/repos/history_repo.py` around `search_movies` (count at ~line 505) and `search_torrents` (count at ~line 584). Note exactly how the count SQL composes its WHERE (it reuses the same filter WHERE as the data query — `MIN(COUNT(*), 10000) AS cnt FROM MovieHistory m {where_clause}`). The extraction MUST preserve route behavior and the final golden token sequence.

- [x] **Step 1.2 — Extract importable count builders.** In `history_repo.py`, add two module-level builders next to the existing `_build_movie_filters` / `_build_torrent_filters` (the ADR-018 builders). They wrap the filter builder and add the capped-count prefix:

```python
def build_movie_count(**kwargs) -> tuple[str, list]:
    """ADR-047: capped total_estimate count for MovieHistory (golden-pinned)."""
    where_clause, params = _build_movie_filters(**kwargs)
    return f"SELECT MIN(COUNT(*), 10000) AS cnt FROM MovieHistory m {where_clause}", params


def build_torrent_count(**kwargs) -> tuple[str, list]:
    """ADR-047: capped total_estimate count for TorrentHistory (golden-pinned)."""
    where_clause, params = _build_torrent_filters(**kwargs)
    return (
        "SELECT MIN(COUNT(*), 10000) AS cnt "
        "FROM TorrentHistory t JOIN MovieHistory m ON m.Id = t.MovieHistoryId "
        f"{where_clause}",
        params,
    )
```
Then **replace the inline count_sql in `search_movies` / `search_torrents` to call these builders** (behavior-preserving at the route level; the harmless `AS cnt` alias was added during implementation so Python and TS share the exact normalized golden token sequence). Verify by running the existing history search tests: `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_history_search.py -q` → still green.

- [x] **Step 1.3 — Add count cases.** In `apps/cli/ops/query_contract_cases.py`, after the filter case lists, add (reuse the same filter kwargs so the WHERE coverage carries over):
```python
MOVIE_COUNT_CASES = [("movie_count", name, kw) for (_b, name, kw) in MOVIE_FILTER_CASES]
TORRENT_COUNT_CASES = [("torrent_count", name, kw) for (_b, name, kw) in TORRENT_FILTER_CASES]
```

- [x] **Step 1.4 — Register the builders + include the cases.** In `apps/cli/ops/dump_query_contract.py`:
  - Import the two new builders and add to `_BUILDERS`:
```python
    "movie_count": _build_movie_count_for_contract,
    "torrent_count": _build_torrent_count_for_contract,
```
  where the two `_for_contract` shims simply call `build_movie_count` / `build_torrent_count` (import them from `javdb.storage.repos.history_repo`). If their signatures already return `(sql, bindings)`, register them directly without a shim.
  - In `main()`, add `*MOVIE_COUNT_CASES, *TORRENT_COUNT_CASES` to the case concatenation tuple, and import them from `query_contract_cases`.

- [x] **Step 1.5 — Regenerate the golden.** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_query_contract` → prints `wrote ... (N cases)` with N increased by the count cases; `docs/api/contract/query-builders.golden.json` now has `movie_count` / `torrent_count` entries and a new `version` hash.

- [x] **Step 1.6 — Run the golden pytest.** `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_query_contract_golden.py -q` → PASS. (`test_golden_covers_all_builders` asserts `seen == set(_BUILDERS)`, so the new builders MUST have cases — they do.)

- [x] **Step 1.7 — Commit (Python repo).**
```bash
git add javdb/storage/repos/history_repo.py apps/cli/ops/query_contract_cases.py apps/cli/ops/dump_query_contract.py docs/api/contract/query-builders.golden.json
git -c user.name=Ted -c user.email=ted@wu.engineer commit -m "feat(contract): pin history total_estimate count statements in the query golden (ADR-047 D5a)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 (TS repo): matching count builders + conformance + re-vendor

**Repo:** `JAVDB_AutoSpider_Web/`. **Files:** `server/routes/history.ts`, `server/__tests__/query-contract.test.ts`, re-vendor `server/__tests__/fixtures/query-builders.golden.json`.

- [x] **Step 2.1 — Extract TS count builders.** In `server/routes/history.ts`, add pure builders mirroring the Python ones (wrap the existing `buildMovieWhere` / `buildTorrentWhere`):
```typescript
export function buildMovieCount(input: MovieFilterInput): { sql: string; bindings: (string | number)[] } {
  const { where, bindings } = buildMovieWhere(input);
  return { sql: `SELECT MIN(COUNT(*), 10000) AS cnt FROM MovieHistory m ${where}`, bindings };
}
export function buildTorrentCount(input: TorrentFilterInput): { sql: string; bindings: (string | number)[] } {
  const { where, bindings } = buildTorrentWhere(input);
  return {
    sql: `SELECT MIN(COUNT(*), 10000) AS cnt FROM TorrentHistory t JOIN MovieHistory m ON m.Id = t.MovieHistoryId ${where}`,
    bindings,
  };
}
```
Use the EXACT input types the existing `buildMovieWhere`/`buildTorrentWhere` take (read their signatures). Then **route the handlers' `countSql` through these builders** so the route and the golden share one source. **Important:** the golden SQL (from Python) is normalized whitespace; the TS `AS cnt` alias and `MIN(COUNT(*), 10000)` must match the Python token sequence after normalization. The final Python builders from Task 1 include the same harmless `AS cnt` alias, so both backends expose an identical normalized count statement in the contract.

- [x] **Step 2.2 — Re-vendor the golden.** `cd JAVDB_AutoSpider_Web && QUERY_GOLDEN_PATH=../docs/api/contract/query-builders.golden.json npm run gen:query-golden` → updates `server/__tests__/fixtures/query-builders.golden.json` to include the count cases.

- [x] **Step 2.3 — Add the builders to the conformance `RUN` map.** In `server/__tests__/query-contract.test.ts`, import `buildMovieCount`, `buildTorrentCount` and add to `RUN`:
```typescript
  movie_count: (p) => buildMovieCount(p),
  torrent_count: (p) => buildTorrentCount(p),
```

- [x] **Step 2.4 — Run conformance.** `cd JAVDB_AutoSpider_Web && npm run test:server` → the new `movie_count:*` / `torrent_count:*` cases PASS (TS builders byte-match the Python golden after normalization). If they fail, reconcile the TS SQL tokens to the golden (that is the guard working).

- [x] **Step 2.5 — Commit (TS repo).**
```bash
git -C JAVDB_AutoSpider_Web add server/routes/history.ts server/__tests__/query-contract.test.ts server/__tests__/fixtures/query-builders.golden.json
git -C JAVDB_AutoSpider_Web -c user.name=Ted -c user.email=ted@wu.engineer commit -m "test(server): conform history count builders to the query golden (ADR-047 D5a)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 (both repos): symmetric unit tests for the `/summary` static queries + `write_mode`

These are static (no dynamic builder), so per ADR-018 D3 they are guarded by a symmetric unit test on EACH backend (not the cross-repo golden). Phase 1 already added the Python side; add/confirm the TS side so both are pinned.

- [x] **Step 3.1 — Python side (confirm).** `tests/unit/test_adr047_summary_reconcile.py` (from IMP-ADR047-01 Task 2) already asserts `/summary` counts `TorrentHistory` + computes `avg_duration` via `CommittedAt`; `tests/unit/test_adr047_write_mode.py` pins `write_mode` NULL→`pending`. Add one assertion to the summary test that the dedup query uses `IsDeleted=1` (mirrors TS):
```python
    assert any("IsDeleted=1" in s for s in calls), "dedup must filter IsDeleted=1"
```
Re-run: `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_adr047_summary_reconcile.py -q` → PASS (Phase 1 already made `/summary` use `IsDeleted=1`? No — Python ALREADY used `IsDeleted=1`; this just pins it).

- [x] **Step 3.2 — TS side.** Extend `server/__tests__/adr047-reconcile.test.ts` (created in IMP-ADR047-01 Task 3) with the source-level pins that mirror Python:
```typescript
  it("/summary counts TorrentHistory and computes avg_duration from CommittedAt", () => {
    const src = read("stats.ts");
    expect(src).toContain("FROM TorrentHistory");
    expect(src).toContain("CommittedAt");
  });
  it("session write_mode falls back to pending", () => {
    const src = read("sessions.ts");
    expect(src).toContain('WriteMode ?? "pending"');
    expect(src).not.toContain('WriteMode ?? "audit"');
  });
```
Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/adr047-reconcile.test.ts` → PASS.

- [x] **Step 3.3 — Commit (each repo).** Python: `git add tests/unit/test_adr047_summary_reconcile.py && git -c ... commit -m "test(api): pin /summary dedup filter to IsDeleted=1 (ADR-047)"`. TS: `git -C JAVDB_AutoSpider_Web add server/__tests__/adr047-reconcile.test.ts && git -C JAVDB_AutoSpider_Web -c ... commit -m "test(server): pin /summary + write_mode reconciliation (ADR-047)"` (both with the co-author trailer).

---

## Task 4: CI + drift simulation + mark Phase 2

- [x] **Step 4.1 — Confirm the publish/re-vendor pipeline covers the new builders.** The count builders live in `javdb/storage/repos/history_repo.py`, which is already in `publish-query-contract.yml`'s `paths:` (`javdb/storage/repos/**`). So a future change to `build_movie_count` on `main` regenerates the golden and dispatches the re-vendor to the TS repo automatically. Read `.github/workflows/publish-query-contract.yml` `paths:` and confirm; if the builders ended up in a new file outside `javdb/storage/repos/**` or `apps/cli/ops/`, add that path.

- [x] **Step 4.2 — Drift simulation (proves the guard).** (a) In the TS repo, temporarily change `buildMovieCount`'s `10000` to `9999`, run `npm run test:server` → the `movie_count:*` conformance cases go RED. Revert. (b) In the Python repo, temporarily change `build_movie_count`'s cap to `9999`, regenerate the golden, `git diff` shows the golden changed (the freshness gate would fail TS CI until re-vendored). Revert + regenerate. Record both confirmations.

- [x] **Step 4.3 — Full suites.** Python: `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit -q` (green modulo the two known pre-existing failures). TS: `cd JAVDB_AutoSpider_Web && npm run test:server` (green).

- [x] **Step 4.4 — Mark ADR-047 Phase 2 + record the D5b amendment.** In `ADR-047-dual-backend-drift-reconciliation.md` and `.zh.md`: update the Roadmap Phase 2 row to implemented, and add a Status Log entry noting Phase 2 landed AND that D5b's separate `response-values.golden.json` was, on implementation, realized as **count-statement golden cases + symmetric unit tests** (no new fixture type — the drifted surface was overwhelmingly SQL; one value did not justify a new artifact). The final docs commit includes both language files together.

## Out of Scope

- A standalone `response-values.golden.json` fixture (folded into golden cases + symmetric tests — see Architecture note / Task 4.4).
- Guarding the ~46 non-drifting static `prepare()` sites (ADR-047 D6 / ADR-018 D3 — low-leverage).
- ADR-018 Phase 3 ("eliminate" the builders).

## Self-Review

- **Spec coverage:** ADR-047 D5a (count statements → golden) = Tasks 1–2; D5b intent (guard `/summary` + `write_mode`) = Task 3 (symmetric tests) + the D5b amendment in Task 4.4; D6 (narrow) honored — only the count statements enter the cross-repo golden, the rest are per-backend tests.
- **Cross-repo:** Python golden regen (Tasks 1, 3-py, 4.4) vs TS conformance/vendor (Tasks 2, 3-ts) are clearly separated; the existing `repository_dispatch` pipeline ties them (Task 4.1).
- **Exactness caveat:** the count-builder *extraction* (Task 1.2 / 2.1) is behavior-preserving against the current inline SQL — the implementer reproduces the exact tokens (the golden + `AS cnt` alignment in Step 2.1 makes any mismatch a loud test failure, not silent drift).
