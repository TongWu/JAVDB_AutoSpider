# ADR-045: FetchEngine Reusable Public API Hardening

**Status:** Proposed
**Date:** 2026-06-01
**Author:** Ted
**Related Implementation Plans:** [IMP-ADR045-01](IMP-ADR045-01-fetch-engine-public-api.md) (Phase 1 — `drain_remaining()` + thin `run()`, migrate the three migration tools)

## Context

### The trigger — a banned-proxy failure that was never retried by another proxy

`Database Migration` run [#26718484218](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26718484218/job/78741197690)
ran `--backfill-metadata` over 10 hrefs (proxy on, shuffle). Eight succeeded;
two failed:

- `[meta-3/10]` `7yKYW1` → `fetch_failed: empty response`
- `[meta-7/10]` `B0nn6` → `fetch_failed: Proxy 'Hyderabad-ARM2' banned: CF bypass failed 9 consecutive times`

Crucially, `meta-8/9/10` **succeeded afterwards** on other proxies
(Sydney-ARM2, Singapore-ARM1/2) — the pool was **not** exhausted. Yet the two
failures were never put back to be consumed by those still-healthy proxies.

The root cause is structural, not a proxy bug:
`javdb/migrations/tools/backfill_movie_metadata.py` is a **single-threaded,
single-pass loop** (`run_backfill_metadata`, the `for i, href in enumerate(...)`
body). On any failure it does `failed += 1` and moves to the next href — there
is **no re-queue**. Within one `_process_href` call the underlying
`RequestHandler` does cycle through ~6 proxies (the `min(pool-1, 5)` switch
budget), but once that inner budget is spent the href is abandoned. The
`FetchEngine` re-queue machinery (`requeue_front`, `EngineTask.failed_proxies`,
`all_proxies_banned`) that would have handed `meta-7` to Sydney/Singapore is
**never reached**, because this tool does not use `FetchEngine` at all.

### FetchEngine is *already* a reusable public API — backfill just bypasses it

`javdb/spider/fetch/fetch_engine.py` opens with:

> Provides a single `FetchEngine` class that **external scripts (spider main,
> migration backfill, inventory alignment, …) can use** to process arbitrary
> detail-page URLs with the full spider infrastructure…

Two of the three migration tools already consume it:

- `migrate_v7_to_v8.py:482` (`--backfill-actors`) — `FetchEngine.simple(...)` + `for r in engine.results()`.
- `align_inventory_with_moviehistory.py:699` (`--align-inventory`) — advanced `FetchEngine(process_fn=...)` + `ctx.fetch()`.

So the comment in `backfill_movie_metadata.py:11-12` —
*"`FetchEngine` exposes no public result-draining API to reuse here"* — is
**stale and wrong**: `ParallelFetchBackend.results()` (`fetch_engine.py:1674`)
is exactly that public, result-draining iterator, and the sibling tool next
door uses it.

### The two genuine rough edges in the existing API

Hardening — not greenfield — is what is actually needed:

1. **Private state leak on the interrupt path.** Both `migrate_v7_to_v8.py` and
   `align_inventory_with_moviehistory.py` reach into the engine's **private**
   `engine._result_queue.get_nowait()` inside their `except KeyboardInterrupt`
   blocks to salvage already-fetched-but-unwritten results before the process
   dies. `shutdown()` (`fetch_engine.py:1696`) drains the *task* and *login*
   queues into `orphaned` but **not** the *result* queue, so there is no public
   way to recover those produced-but-unconsumed results.

2. **Per-caller lifecycle boilerplate.** Every caller hand-writes the same
   `start() → submit*/submit_task* → mark_done() → for r in results() →
   shutdown()` sequence.

### Relationship to ADR-043

`meta-7` failed because a proxy persistently failed the Cloudflare wall.
[ADR-043](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.md)
reduces *reuse* of such proxies (cross-runner CF auto-ban). This ADR is
complementary and orthogonal: it ensures that **when a fetch fails for any
reason, the work item is re-queued to a different proxy** instead of being
silently dropped. ADR-043 makes bad proxies rarer; ADR-045 makes failed work
recoverable.

## Decision

Harden `FetchEngine`'s public surface with two small additions, then route all
three migration tools through it. **No** structural rewrite (no `PROXY_POOL`
dependency injection, no `FetchEngine`/`ParallelFetchBackend` facade merge, no
renames) — those were explicitly considered and deferred.

### Design Decisions

**D1. Add public `drain_remaining() -> Iterator[EngineResult]`.** A non-blocking
drain of the result queue, intended to be called **after** `shutdown()` (workers
stopped ⇒ no new results race in). It yields exactly the results that workers
produced before stopping — replacing the private `_result_queue.get_nowait()`
loop verbatim. It does **not** touch the task/login queues; tasks that never ran
remain `orphaned` (already returned by `shutdown()`). Lives on
`ParallelFetchBackend`, surfaced on the `FetchEngine` facade.

**D2. Add a thin `run(tasks) -> Iterator[EngineResult]` — happy-path only.** A
generator that owns the lifecycle: `start()`, `submit_task()` for each task,
`mark_done()`, `yield from results()`, and `shutdown()` in a `finally`. It is
**not** the interrupt-salvage path: when `KeyboardInterrupt` lands in the
caller's loop body, Python raises `GeneratorExit` into `run()` and it cannot
yield the buffered results (yielding during `GeneratorExit` is a `RuntimeError`).
Therefore:

- **The three migration tools keep an explicit lifecycle + `drain_remaining()`**
  for robust salvage (interrupt caught in the caller, regardless of where it
  landed):

  ```python
  engine.start()
  for t in tasks: engine.submit_task(t)
  engine.mark_done()
  try:
      for r in engine.results(): apply(r)
  except KeyboardInterrupt:
      engine.shutdown()
      for r in engine.drain_remaining(): apply(r)   # public; was _result_queue.get_nowait()
  else:
      engine.shutdown()
  ```

- **`run()` serves** unit tests, future simple callers that do not need salvage,
  and as the documented canonical happy-path. It is ~8 lines; the small surface
  cost is accepted because it anchors the intended usage pattern.

**D3. Migrate `backfill_movie_metadata` onto `FetchEngine.simple`, auto-login.**
Replace the sequential loop with `FetchEngine.simple(parse_fn=..., use_cookie=True)`.
Login walls now raise `LoginRequired` internally and route to the
`LoginCoordinator` for an auto-login attempt (the `FetchEngine.simple` default,
matching `migrate_v7_to_v8`). Consequence: the distinct `login_required` count
is dropped — a login wall that can be cleared becomes `ok`; one that cannot
becomes a generic failure, **retriable on the next run** exactly as today
(`_load_hrefs_without_metadata` re-selects any href still lacking metadata). Net
behaviour is strictly more capable: login-gated movies get backfilled rather
than skipped.

**D4. DB writes stay in the main results loop, never in the worker.** `parse_fn`
returns the parsed `detail` (parallel, in worker threads); `MetadataRepo().upsert(href, detail)`
runs in the single-threaded results loop. This matches `migrate_v7_to_v8`'s
`_apply_backfill_result` and `align`'s `_apply_align_result`, keeps D1/SQLite
writes off worker threads, and lets `write_failed` be detected in the loop. The
"accept the page when `video_code` **or** `title` parsed" rule
(`backfill_movie_metadata.py:200`) moves into `parse_fn` (return `None` to let
the engine re-queue genuinely empty pages to another proxy).

**D5. Preserve backfill's D1-aware write path.** Keep `get_db(HISTORY_DB_PATH)` /
`MetadataRepo` (auto-routes by `STORAGE_BACKEND`). Do **not** copy
`migrate_v7_to_v8`'s `sqlite3.connect(...)` + `if not use_sqlite(): return 1`
guard — that tool is legacy SQLite-only, whereas metadata backfill already runs
on **D1** in CI (`backfill_movie_metadata.py:73-75`). D1 is the canonical source
of truth (per `CLAUDE.md`); the migrated tool must not regress to SQLite-only.

**D6. `--limit-per-worker` maps to the engine's real `per_worker_task_limit`.**
Today backfill manually pre-truncates `hrefs[:limit_per_worker * num_workers]`
because it was sequential (`backfill_movie_metadata.py:249-252`). With the engine,
pass `ParallelFetchBackend(per_worker_task_limit=limit_per_worker)` and submit
the full (optionally `--limit`-capped) list. The engine counts **successes** per
worker — more accurate than truncating submissions, and identical to how `align`
wires the same input (`align_inventory_with_moviehistory.py:705`). `--limit`
stays an absolute pre-submit cap (`hrefs[:limit]`); `--shuffle` is unchanged.

**D7. Blast radius — migration tools only.** `runner.py` (spider detail) uses a
`cancel_event` + `SystemExit(124)` and never touches `_result_queue`;
`index_parallel.py` uses a sliding submission **window** (submit N → consume →
submit more) that does not fit `run()`'s finite-list model. Both have different
lifecycle needs and **no** private-state leak, so they are left untouched
(surgical-change principle).

**D8. Fix the stale comment and stale `scripts.*` path references.** Correct the
`backfill_movie_metadata.py:11-12` claim, and update the `fetch_engine.py`
docstrings / `__all__` that still reference `scripts.spider.fetch.*` —
pre-[ADR-007](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md)
paths retired in Phase 3 (canonical is `javdb.spider.fetch.*`).

## Consequences

### Positive

- **The observed bug class disappears.** A failed fetch in backfill (banned
  proxy, empty response, CF wall) is re-queued to a different proxy via the
  engine's `failed_proxies` / `requeue_front` machinery. The `meta-7` case would
  have been retried on Sydney/Singapore instead of dropped.
- Backfill gains the full spider infrastructure for free: parallel-per-proxy
  workers, CF-bypass cascade, adaptive sleep/throttle, cross-runner proxy
  coordination, and auto-login.
- **Private `_result_queue` access is eliminated in all callers** —
  encapsulation restored; the engine can change its internal queues without
  breaking migration tools.
- One canonical fetch path for every migration / catch-up tool; the misleading
  "no public API" comment is gone.
- Engine-internal only — **not** part of the
  [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)
  dual-backend overlap surface (no D1 query / auth / API-shape change), so the
  web repo's TS backend needs no sync.

### Negative

- **Backfill behaviour change (D3):** auto-login now fires on login walls
  (consumes the shared login budget), and the distinct `login_required` count is
  dropped. Accepted: more movies are actually backfilled; unrecoverable login is
  still retriable next run (same end-state as today).
- Backfill loses strict sequential / deterministic ordering — it now runs
  parallel and out-of-order. Acceptable for an idempotent `upsert` catch-up job.
- `run()` (D2) is a thin convenience the three migrated tools will **not** use
  (they need the explicit salvage path). Small added surface, justified by tests
  + future callers + canonical-pattern documentation.
- Deferred items (PROXY_POOL injection, facade/backend merge, renames) leave
  some pre-existing API awkwardness in place; revisit only if a future caller
  needs decoupling from global `PROXY_POOL`.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR045-01](IMP-ADR045-01-fetch-engine-public-api.md) | `drain_remaining()` + thin `run()` on `ParallelFetchBackend` (+ `FetchEngine` facade) with unit tests; migrate `backfill_movie_metadata` → `FetchEngine.simple` (D3–D6); switch `migrate_v7_to_v8` + `align` interrupt handlers to `drain_remaining()` (D1); fix stale comment + `scripts.*` refs (D8); record the no-handbook-change decision (CLI surface unchanged) | `PROXY_POOL` dependency injection; `FetchEngine`/`ParallelFetchBackend` merge; public-method renames (the "large refactor" option, not taken) |

## References

- Trigger run: [`Database Migration #26718484218`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26718484218/job/78741197690)
- `javdb/spider/fetch/fetch_engine.py` — `ParallelFetchBackend` / `FetchEngine` / `WorkerContext` / `results()` / `shutdown()`
- `javdb/migrations/tools/backfill_movie_metadata.py` — sequential tool being migrated (D3–D6)
- `javdb/migrations/tools/migrate_v7_to_v8.py`, `javdb/migrations/tools/align_inventory_with_moviehistory.py` — existing `FetchEngine` callers with the private-queue leak (D1)
- [ADR-043 — CF Persistent-Failure Auto-Ban](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.md) — complementary: reduces bad-proxy *reuse*; this ADR makes failed *work* recoverable
- [ADR-007 — Monorepo Restructure](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md) — retired the `scripts.*` paths still referenced in stale docstrings (D8)

## Status Log

- 2026-06-01: Proposed
- 2026-06-01: Phase 1 implemented and locally verified ([IMP-ADR045-01](IMP-ADR045-01-fetch-engine-public-api.md)); the CLI surface stayed unchanged, so no handbook update was needed.
