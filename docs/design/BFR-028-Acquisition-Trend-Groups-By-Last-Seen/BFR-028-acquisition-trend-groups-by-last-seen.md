# BFR-028: Acquisition failure trend groups by `last_seen_at`, not the transition

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `apps/api/routers/library_query_builders.py`, `javdb/ops/reconcile/service.py`, `javdb/ops/reconcile/models.py`, `javdb/storage/repos/acquisition_outcome_repo.py`, `javdb/storage/db/_db_migrations.py`, `javdb/migrations/d1/2026_08_09_add_acquisition_state_changed_at.sql`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), issue #273

---

## Symptom

No incident was observed. Found by review while promoting `dev` → `main` on the public mirror (TongWu/JAVDB_AutoSpider#153), filed as issue #273 and confirmed against the code.

`GET /api/library/acquisition/trend` plots `stalled` and `failed` counts on the wrong day — up to two weeks earlier than the transition actually happened. With the default `period=7d`, a failure detected today falls outside the window entirely and never appears.

## Root Cause

`AcquisitionOutcome` had no column recording *when the state changed*, so the trend reached for the closest-looking one:

```python
"SELECT substr(last_seen_at, 1, 10) AS d, "
...
"WHERE state IN ('completed','stalled','failed') AND last_seen_at >= ? "
```

The comment above it asserted "`last_seen_at` is the transition date (refreshed every pass)". That holds only for rows the reconciler can still see in qB. For the two states the trend is most useful for, it is false:

```python
if obs is not None:
    ...
    rec.last_seen_at = now          # refreshed only while the torrent is observed
else:
    age = _age_days(rec.last_seen_at or rec.queued_at)
    if age >= 2 * options.stalled_after_days:
        new_state = "failed"        # last_seen_at deliberately NOT touched
    elif age >= options.stalled_after_days:
        new_state = "stalled"
```

An absent torrent is precisely one that cannot be observed, so `last_seen_at` freezes at the last *successful* observation and the transition happens `stalled_after_days` (default 7) or `2 ×` that (14) later. `last_seen_at` therefore dates the last sign of life, and the trend read it as the date of death.

The design flaw is one column carrying two meanings: liveness ("last seen alive") and transition ("when this became terminal"). They coincide for `completed`, which is why the bug stayed invisible — the state the trend renders correctly is the one where the two meanings happen to agree.

## Fix

Add the missing timestamp rather than overload the existing one. `last_seen_at` cannot simply be refreshed at transition time: the reconciler measures the `failed` threshold as age *since* `last_seen_at`, so writing `now` on the `stalled` transition would reset that clock and no row could ever reach `failed`.

D1 first, since it is the source of truth:

- `javdb/migrations/d1/2026_08_09_add_acquisition_state_changed_at.sql` — `ALTER TABLE ... ADD COLUMN state_changed_at TEXT`, a backfill seeding `COALESCE(landed_at, completed_at, last_seen_at, queued_at)`, and an index. The local SQLite DDL in `_db_migrations.py` appends the column last so its column order matches what D1 ends up with after the `ALTER`.
- `AcquisitionOutcomeRecord.state_changed_at` + the repo column list. `mark_state` and `mark_in_library` advance it **only when the target state differs from the stored one** — a caller names a target state, not necessarily a transition, and re-reporting the same state (the daily PikPak cleanup over a row the hourly pass already completed) must keep the original `completed_at` / `landed_at` / `state_changed_at`. The `upsert` assignment `COALESCE`s it so a pass that observes no change cannot blank a recorded transition.
- `service.run` stamps `rec.state_changed_at = now` **only when `new_state != previous_state`**. The guard matters: the absent branch re-derives `stalled` on every pass until the `2 ×` window opens, so an unguarded stamp would walk the transition date forward one day per run.
- The trend query builder groups and filters on `state_changed_at`.

## Side Effects

Historical rows keep imprecise placement. The backfill can only seed what was recorded, so `stalled` / `failed` rows that transitioned before this migration still sit on their old `last_seen_at` day — the same (wrong) position they had before. Only transitions made after the migration are exact; the trend converges as those rows age out.

`state_changed_at` is not exposed on `/acquisition/recent`; nothing in the API response shape changed.

## Follow-Up

- [x] **Applied to D1 on 2026-08-10** via `wrangler d1 execute javdb-operations --remote`: 3 queries (ALTER + backfill + index), 11 077 rows read, **7 316 rows written** — i.e. the backfill seeded `state_changed_at` on 7 316 existing `AcquisitionOutcome` rows.
- [ ] Re-align the local SQLite mirror: `python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`.
- [x] Dual-backend parity (ADR-017): `server/routes/library.ts` in `TongWu/JAVDB_AutoSpider_Web` mirrors this SQL byte-for-byte and was wrong the same way. Fixed on branch `claude/verify-fix-open-issues-n8srgf` there, together with its `server/__tests__/library-routes.test.ts` fixture DDL and the re-vendored query golden (`fe08ee6c2ccb71b6` → `b5c870bd2e86028e`).
- [ ] **Deploy order matters**: apply the D1 migration *before* deploying that Worker, or its trend query fails with `no such column: state_changed_at`.
