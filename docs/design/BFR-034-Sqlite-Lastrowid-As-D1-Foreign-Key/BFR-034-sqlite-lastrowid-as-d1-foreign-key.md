# BFR-034: Cross-backend AUTOINCREMENT trusted for a dual-write foreign key (SQLite `lastrowid` used as the D1 `ReportTorrents` FK)

**Status**: Fixed
**Date**: 2026-08-12
**Severity**: High (silent, unrollbackable cross-session FK corruption on D1)
**Affected**: `javdb/storage/db/_db_reports.py`, `javdb/storage/dual_connection.py`
**Related**: [ADR-047](../_archive/ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md), [ADR-042](../_archive/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md), [BFR-021](../BFR-021-D1-Session-Write-Silent-Loss/BFR-021-d1-session-write-silent-loss.md), [BFR-033](../BFR-033-Rebase-Ours-Discards-Run-Results/BFR-033-rebase-ours-discards-run-results.md) (sibling in the same review batch), `javdb/migrations/d1/2026_05_08_sessionid_decouple.md` (the 2026-05-08 precedent), commit `cd507c88`

---

## Symptom

No incident report. The defect was found by review of the dual-write path and
confirmed against the code — which is itself the point: **this bug has no
symptom.** It produces no exception, no failed run, no drift-log entry, and no
FK violation. It writes wrong data that looks right.

`db_insert_report_rows` inserted a `ReportMovies` row, read the new id back from
the cursor, and used it as the child rows' foreign key:

```python
cur = conn.execute(
    """INSERT INTO ReportMovies
       (SessionId, Href, VideoCode, Page, Actor, Rate, CommentNumber)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
    (session_id, href, ...),
)
rm_id = cur.lastrowid
...
conn.execute(
    """INSERT INTO ReportTorrents
       (ReportMovieId, VideoCode, MagnetUri, ...)
       VALUES (?, ?, ?, ...)""",
    (rm_id, vc, magnet, ...),
)
```

Under `STORAGE_BACKEND=dual` that single `rm_id` was written to **both**
backends, but it was only ever the **SQLite** id.

## Root Cause

The design flaw is trusting two independent databases' `AUTOINCREMENT` counters to
stay aligned, and then using one backend's generated id as a foreign key on the
other.

`ReportMovies.Id` is `INTEGER PRIMARY KEY AUTOINCREMENT`
(`javdb/migrations/d1/2026_05_13_session_id_to_text_reports.sql:62`). In dual mode
the INSERT fans out to two real databases, each allocating its own id. The cursor
exposes only one of them:

```python
self.lastrowid = (
    sqlite_cur.lastrowid if sqlite_cur is not None else getattr(d1_cur, "lastrowid", None)
)
```

— `javdb/storage/dual_connection.py:381-383`

The D1 leg's own id arrives as `meta.last_row_id` (`javdb/storage/d1_client.py:181`)
and was simply discarded. So the moment the two counters diverge by *N*, every
`ReportTorrents` row written to D1 points at `Id - N` — a movie belonging to some
**earlier, different session**.

**And the FK is satisfied.** That stale id almost always exists on D1, because the
counters diverge by drifting *ahead*, meaning the referenced id is an older row
that is very much present. SQLite's own `PRAGMA foreign_keys` check passes on the
mirror (where the id is correct), and D1's passes too (where the id is wrong but
resolvable). Nothing anywhere raises.

Two secondary failures let this survive review for so long:

- **A docstring asserted the opposite.** `DualConnection._maybe_warn_id_drift`
  (`javdb/storage/dual_connection.py:1213-1214`) states that a constant
  SQLite↔D1 `lastrowid` offset "is the normal post-migration steady state and is
  harmless (FK resolution uses business keys)". That is true for the drift
  reconciler it was written about ([ADR-047](../_archive/ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md),
  which matches rows on `Href` / `MagnetUri`), and false for
  `db_insert_report_rows`, which resolves by raw id. A local truth was read as a
  global one, and the module downgraded a constant offset from *error* to *INFO*
  on the strength of it.
- **The existing guard was not armed for this table.** `dual_connection.py` already
  has exactly the right defence — `APPLICATION_GENERATED_ID_PK_COLUMN`, whose
  `DualCursor._check_id_consistency` raises `DualWriteIdMismatchError` when a
  guarded table's two `lastrowid` values disagree. It listed `ReportSessions`,
  `PendingMovieHistoryWrites`, `PendingTorrentHistoryWrites`, `MovieHistory`,
  `TorrentHistory` — and not `ReportMovies`. So the one table whose id was being
  used verbatim as a cross-backend FK was the one table left unguarded.

This is a repeat of a known class of bug, not a novel one. The
`APPLICATION_GENERATED_ID_PK_COLUMN` block's own comments cite the 2026-05-08
incident (`javdb/migrations/d1/2026_05_08_sessionid_decouple.md`) caused by trusting
SQLite-side `AUTOINCREMENT`, and the "Batch C" entry records the identical fix
already applied to `MovieHistory` / `TorrentHistory` for the identical reason
(`TorrentHistory.MovieHistoryId` pointing at wrong rows). `ReportMovies` was
missed in that sweep.

## Aggravating Factor: the corruption escapes session rollback

Rollback resolves a session's torrents *through that session's own movies*:

```sql
DELETE FROM ReportTorrents
WHERE ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)
```

— `javdb/storage/db/_db_rollback.py:299-301`

On D1 the mis-pointed torrent rows carry an id belonging to a *different*
session's movie, so this `DELETE` does not match them. The parent `ReportMovies`
rows are then deleted by the `ROLLBACK_REPORTS_TABLES` loop immediately after,
leaving the torrent rows behind as **orphans pointing into a session that was
never theirs**. Rolling back the session that created them does not remove them;
rolling back the session they point at does not either (it deletes only *its* own
correctly-pointed children).

The result is permanent, permanently mis-attributed rows on D1 — the canonical
source of truth — that the project's own rollback tooling is structurally unable
to reach. `javdb/migrations/tools/cleanup_orphaned_session_rows.py:206-208` uses
the same `ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId IN ...)`
shape, so the orphan-cleanup tool cannot see them either.

Downstream, `apps/api/routers/stats_query_builders.py:81` counts the torrents
metric with `LEFT JOIN ReportTorrents rt ON rt.ReportMovieId = rm.Id`, so the
dashboard attributes the mis-pointed rows to the wrong day and the wrong session.

## Exposure

Counter divergence is not hypothetical in this system; three mechanisms produce it:

1. **D1 auto-commits per statement, SQLite does not.** When a dual transaction
   rolls back, the SQLite leg's INSERTs are undone while D1's are already
   committed — the module says so in several places
   (`dual_connection.py:1004`, `:1155`, `:1176`). Every such event offsets the
   counters permanently.
2. **The SQLite mirror is frozen under `d1` mode.** `reports/*.db` are never
   written by the pipeline when `STORAGE_BACKEND=d1` (the DB layer uses a
   `D1Connection`), so D1's counter advances daily while the mirror's does not.
   Any later switch to `dual` starts from an already-large offset.
3. **`sync_d1_to_sqlite` realigns only to `max(Id)`.**
   `apps/cli/db/sync_d1_to_sqlite.py:566-575` sets
   `UPDATE sqlite_sequence SET seq=? WHERE name=?` to the highest imported id.
   That prevents *future local* collisions; it does not and cannot keep the two
   counters in lockstep going forward.

The path is reachable in CI against production data: `TestIngestion.yml:100` pins
`STORAGE_BACKEND: dual` (deliberately, so a repo-level `vars.STORAGE_BACKEND`
drift cannot silently downgrade it) and runs against the production D1 secrets.

## Fix

Commit [`cd507c88`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/cd507c88)
— `fix(db): stop using SQLite lastrowid as the dual-mode ReportTorrents FK`.

1. **Supply `ReportMovies.Id` explicitly.** `db_insert_report_rows` now calls
   `generate_integer_id()` (52-bit application snowflake, always `< 2**53` so it
   survives D1's JSON transport) and passes it in the INSERT's column list, then
   reuses that same value as `ReportTorrents.ReportMovieId`. Both backends now
   store the *same* id, so the FK means the same thing on both. `cur.lastrowid` is
   no longer read at all. This mirrors the established Batch C approach already
   used for `MovieHistory` / `TorrentHistory`.
2. **Arm the guard.** `ReportMovies: "Id"` added to
   `APPLICATION_GENERATED_ID_PK_COLUMN` (`javdb/storage/dual_connection.py:298-306`),
   turning the invariant from *assumed* into *enforced*: a future caller that
   inserts into `ReportMovies` without an explicit `Id` under dual mode now aborts
   with `DualWriteIdMismatchError` instead of silently corrupting D1.
3. **Regression tests** in `tests/unit/test_report_rows_dual_movie_id.py`, which
   simulate the D1 leg with a second real SQLite database whose `ReportMovies`
   counter is *deliberately ahead* of the mirror's — the exact condition the old
   code could not survive:
   `test_torrent_fk_matches_movie_id_on_both_backends`,
   `test_movie_id_is_identical_on_both_backends`,
   `test_multiple_rows_do_not_share_a_movie_id`,
   `test_reportmovies_is_registered_as_application_generated_id`,
   `test_insert_supplies_explicit_id_column`.

## Side Effects

- **New `ReportMovies.Id` values are 52-bit snowflakes, not small sequential
  integers.** Pre-existing rows keep their old small ids; the column stays
  `INTEGER PRIMARY KEY AUTOINCREMENT` and no schema change or migration is needed.
  Anything that assumed `ReportMovies.Id` is dense, small, or monotonic in
  insertion order would be affected — nothing in the repo does; readers join on
  it or select by it.
- **`sqlite_sequence` for `ReportMovies` will jump** to the snowflake magnitude on
  the local mirror after the first insert, exactly as it already does for
  `MovieHistory` / `TorrentHistory`. Harmless: no code depends on the counter value.
- **Existing mis-pointed rows on D1 are not repaired by this fix.** It stops new
  corruption; it does not find or correct historical damage. See Follow-Up.
- No change to `db_insert_report_rows`'s signature, return value, or behaviour
  under `sqlite` / `d1` backends. No API response shape changed, so no
  TypeScript-backend counterpart is required under the
  [ADR-055](../ADR-055-Dual-Backend-Contract-Single-Source/ADR-055-dual-backend-contract-single-source.md)
  sync rule.

## Follow-Up

- [x] Supply `ReportMovies.Id` explicitly and reuse it as the FK on both legs
- [x] Register `ReportMovies` in `APPLICATION_GENERATED_ID_PK_COLUMN`
- [x] Pin the invariant with dual-backend regression tests
- [x] **`javdb/migrations/tools/csv_to_sqlite.py` had the identical pattern** —
      `report_movie_id = cur.lastrowid` after an `INSERT INTO ReportMovies`, feeding
      the same `ReportTorrents.ReportMovieId`, plus a `SELECT Id ... WHERE Href=?`
      read-back for `MovieHistory` (equally unsafe, since `DualConnection` routes
      reads to D1). Both now use `generate_integer_id()` with the id supplied in
      the INSERT column list. Landed in the same review batch, commit
      [`5bf26062`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/5bf26062),
      with regression tests in `tests/unit/test_csv_to_sqlite_dual_ids.py`.
- [x] **`apps/api/routers/test_mode.py` seeded guarded tables without explicit
      ids** — `return int(cur.lastrowid)` after `INSERT INTO MovieHistory` and
      `INSERT INTO TorrentHistory`, both of which are in
      `APPLICATION_GENERATED_ID_PK_COLUMN`. `_insert_movie` / `_insert_torrent` now
      generate the id and return it. Landed in the same review batch, same commit
      (`5bf26062`).
      (These take a plain `sqlite3.Connection`, so the guard could not have fired —
      which is precisely why the pattern needed removing rather than relying on it;
      the fixtures now also carry production-shaped ids instead of 1/2/3.)
- [x] **`apps/cli/ops/profile_hot_paths.py` seeded the guarded history tables
      through the backend router** — `_seed_history` read the new
      `MovieHistory.Id` from `cur.lastrowid` and used it as
      `TorrentHistory.MovieHistoryId`, writing through `get_db(<tempfile>)`,
      which routes on `STORAGE_BACKEND` alone (the *path* argument plays no
      part in the decision). The write never actually reached D1:
      `_logical_name_for()` rejects the unmapped tempfile path with a
      `ValueError`, which the benchmark driver swallowed as `!! db_load_history
      FAILED`, so under `d1` / `dual` the profiler was merely broken rather
      than corrupting — but it was one mapping entry away from writing to the
      canonical database. `bench_db_load_history` now runs inside the
      documented `_STORAGE_BACKEND_INIT_OVERRIDE=sqlite` escape hatch (so
      `init_db`, the seeder and `db_load_history` are all provably local, and
      the benchmark works under all three backends instead of two), and the
      seeder mints both ids with `generate_integer_id()`. Tests in
      `tests/unit/test_profile_hot_paths_seed_ids.py`.
- [x] **Correct the `_maybe_warn_id_drift` docstring** at
      `javdb/storage/dual_connection.py:1213-1214`. "Harmless (FK resolution uses
      business keys)" is only true of the reconciler; as written it invites the next
      author to make this same mistake. Scope the claim to business-key resolution
      explicitly.
      **Done (2026-08-12):** the docstring now scopes "harmless" to code that
      never reuses `lastrowid` across backends, names this BFR, and points at
      the guard map and the `test_lastrowid_call_sites.py` inventory.
- [ ] **Assess and repair historical damage on D1.** The audit tooling has
      shipped: `python3 -m apps.cli.db.audit_report_torrent_fks` reports the three
      detection signals (`ORPHANED_TORRENT` and `VIDEO_CODE_MISMATCH`,
      deterministic; `UNEXPLAINED_DUPLICATE_SLOT`, heuristic) against whatever
      `STORAGE_BACKEND` resolves to, with repair gated behind
      `--apply --force --delete-orphans` (orphan deletion only — re-attachment is
      report-only, because this schema cannot prove a mis-pointed row's intended
      parent). **The remaining work is operational:** run it with the `d1`
      environment (D1 is canonical), triage the report, and apply the remediation
      — gated deletion for orphans, manual repair for the mismatch list. Note that
      neither `apps.cli.db.rollback` nor `cleanup_orphaned_session_rows.py` can
      reach these rows — both resolve children via
      `ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)`.
- [x] **Sweep for the remaining class of bug.** Audit every `cur.lastrowid` read on
      a path that can run under `STORAGE_BACKEND=dual`, not just the ones found
      here. The 2026-05-08 incident, Batch C, and this BFR are three instances of
      one pattern; a lint rule or a contract test enumerating `lastrowid` call
      sites would end the recurrence rather than fixing it a fourth time.
      **Done (2026-08-12):** swept all 20 `lastrowid` reads then present across 9
      files under `javdb/` `apps/` `scripts/` (AST-based, so comments and
      docstrings about `lastrowid` don't mask real reads) — 1 fixed (the profiler
      seeder above), which leaves the pinned inventory at **19 reads across 8
      files**:
      6 reachable under `dual` but with the value discarded by every in-tree
      caller (`_db_stats` ×3, `_db_operations` ×2, `pipeline_event_repo` ×1),
      4 provably single-backend (`_db_migrations`' v5→v6 step runs only under
      `init_db`'s sqlite override; `reconcile_d1_drift` ×2 and `d1_port` ×1 read
      D1's *own* cursor and use it for D1 rows), 2 deliberately resolved to the
      D1 leg (`content_filter_repo._canonical_lastrowid`), and 7 in the
      `DualCursor` guard machinery itself. The recurrence is now closed by a
      contract test: `tests/unit/test_lastrowid_call_sites.py` pins the
      inventory with a classification per file, so a new read fails CI until
      its author classifies it, and asserts the four files holding the fixed
      writers never read a rowid back again.
