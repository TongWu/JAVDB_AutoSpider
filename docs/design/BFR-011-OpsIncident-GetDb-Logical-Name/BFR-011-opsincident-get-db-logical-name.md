# BFR-011: OpsIncident persistence calls get_db() with a logical name, silently falling back to JSONL

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/ops/diagnosis/persistence.py` (`persist_incident`), `apps/api/routers/diagnostics.py` (`_list_ops_incident_records`, `_get_ops_incident_record`)
**Related**: [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md) (AI operations diagnosis — owns the `OpsIncidents` table), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) (site-contract sentinel — its IMP plan carries the same broken call form), `CLAUDE.md` → "Database Access" (the example that propagated the wrong form)

---

## Symptom

`OpsIncidents` rows were never reaching the canonical D1 store through the
diagnosis write path. Every call to `persist_incident()` silently took the
JSONL fallback branch — incidents landed in `reports/ops/ops_incidents.jsonl`
with `persistence_status="d1_failed_jsonl_written"` instead of `"d1_written"`,
and the `/api/diag/ops-incidents` list/detail endpoints raised instead of
returning rows.

The failure was invisible in normal operation because:

- `persist_incident()` wraps the DB write in a broad `try/except Exception`
  whose only observable effect is a `WARNING`-level log line plus a fallback
  write — no error propagates to the caller.
- The pre-existing unit test that "covered" this path
  (`test_persist_incident_uses_reports_logical_db`) mocked `get_db` with a fake
  that accepted *any* string and returned a connection, so it asserted the
  broken contract (`seen == ["reports"]`) and never exercised the real
  `get_db`. It papered over the bug instead of catching it.

Reproduced empirically under `STORAGE_BACKEND=sqlite`:

```text
sqlite3.DatabaseError: Database file reports is not a valid SQLite file.
This usually means Git LFS did not pull the real file.
```

## Root Cause

`get_db()` takes a **filesystem path** (`HISTORY_DB_PATH`, `REPORTS_DB_PATH`,
`OPERATIONS_DB_PATH`), not a logical name. Its backend router
(`javdb/storage/db/_db_connection.py`) keys the D1 logical-name mapping
`_DB_PATH_TO_LOGICAL_NAME` by those paths (e.g. `reports/reports.db → reports`).
Passing the bare string `"reports"` is therefore wrong in both backends:

- **sqlite** — `_open_sqlite_connection("reports")` tries to open the `reports/`
  directory as a SQLite file and raises `sqlite3.DatabaseError`.
- **d1** — `_logical_name_for("reports")` finds no entry in
  `_DB_PATH_TO_LOGICAL_NAME` (which is keyed by *paths*, not names) and raises
  `ValueError`.

The diagnosis code at `javdb/ops/diagnosis/persistence.py:29` wrote
`with get_db("reports") as conn:`. The exception raised inside the context
manager was swallowed by `persist_incident()`'s broad `except`, so the function
always degraded to JSONL. The same broken form had been copied into two API
helpers in `apps/api/routers/diagnostics.py` (`_list_ops_incident_records`,
`_get_ops_incident_record`).

**Why the design was wrong, not just what broke:** the `get_db()` signature
overloads a single string parameter that *looks* interchangeable between "path"
and "logical name", and the D1 router accepts logical names internally — so the
string `"reports"` reads as plausible at the call site. The mistake is only
caught at runtime, deep inside the connection factory, and here it was further
masked by (a) a broad catch-and-fallback and (b) a mock-based test that never
called the real router. This is a **propagating documentation bug**: the
`CLAUDE.md` "Database Access" example itself showed `conn = get_db('history')`,
and four active IMP plans (ADR-033/035/036/037) reproduce the same form — so
the wrong call shape is what an engineer copies.

## Fix

Import `REPORTS_DB_PATH` and pass the **path** in all three call sites
(matching the already-correct `get_db(OPERATIONS_DB_PATH)` usage elsewhere in
`diagnostics.py`):

- `javdb/ops/diagnosis/persistence.py` — `get_db("reports")` →
  `get_db(REPORTS_DB_PATH)` (+ import).
- `apps/api/routers/diagnostics.py` — both `_list_ops_incident_records` and
  `_get_ops_incident_record` → `get_db(REPORTS_DB_PATH)` (+ import).

Tests (`tests/unit/test_ops_incident_repo.py`):

- Rewrote the bug-enshrining test into
  `test_persist_incident_calls_get_db_with_reports_path`, which now asserts
  `get_db` is called with `REPORTS_DB_PATH` (the path), not the logical name.
- Added `test_persist_incident_writes_to_reports_db_without_jsonl_fallback`,
  a real-backend regression test: it points `persist_incident` at a temp reports
  DB, exercises the **real** `get_db` under sqlite (no mock), and asserts the
  row lands in the reports DB with `persistence_status="d1_written"` and that the
  JSONL fallback file is *not* created. Verified this test fails on the
  pre-fix code (reproducing the `sqlite3.DatabaseError`) and passes after.

Documentation:

- Fixed the `CLAUDE.md` "Database Access" example to use
  `with get_db(HISTORY_DB_PATH) as conn:` and added a note that `get_db()` takes
  a path (not a logical name) and is a context manager.

## Side Effects

None functional. Incidents created after the fix now persist to the reports DB
as designed. Historical incidents written to `ops_incidents.jsonl` during the
broken window are **not** automatically backfilled — see Follow-Up.

## Follow-Up

- [ ] Backfill (optional): replay any `reports/ops/ops_incidents.jsonl` rows
      written with `persistence_status="d1_failed_jsonl_written"` into the
      reports/D1 `OpsIncidents` table, if those incidents are still useful.
- [x] Fixed the same broken `get_db("…")` / `get_db('…')` logical-name form in the
      active IMP plans so engineers don't copy it forward:
      `IMP-ADR035-01-piggyback-and-gate.md` (sentinel persistence — the form this
      BFR's reporter cited as the "reference"; the plan itself was broken),
      `IMP-ADR033-01-acquisition-outcome.md` (`get_db('operations')` — prose,
      table, Task-4 heading, and code block), and the one stale leftover in
      `IMP-ADR036-01-event-spine.md` (the rest of that doc was already corrected
      during its implementation). **Deliberately left** `IMP-ADR037-01-harness-core.md`:
      it is implemented (shipped `tests/harness/pipeline_harness.py` uses the
      correct form) and its plan listing already carries an explicit "get_db()
      takes a path, not a logical name" correction note plus a directive that the
      shipped file is canonical — editing a self-correcting superseded listing
      would be churn.
- [x] Added a guardrail: `get_db()` / `get_local_sqlite_db()` now reject bare
      logical names (`"history"`/`"reports"`/`"operations"`) via
      `_reject_logical_name()` with a clear error pointing at the `*_DB_PATH`
      constants — turning the old silent fallback into a fast failure. Covered by
      `tests/unit/test_db.py::TestGetDbLogicalNameGuard`.
- [ ] Separately (same `get_db` path-confusion family, distinct bug): the shipped
      `tests/harness/pipeline_harness.py` `events()` / `acquisition_outcomes()`
      read `PipelineEvent` / `AcquisitionOutcome` (which live in the reports /
      operations DBs) through `get_db()` with **no argument**, which defaults to
      the *history* DB — so those harness views silently return `[]`. The guard
      added above does NOT catch this (a no-arg default is a valid path, just the
      wrong one). Out of scope for this BFR; flagged for a separate fix.
