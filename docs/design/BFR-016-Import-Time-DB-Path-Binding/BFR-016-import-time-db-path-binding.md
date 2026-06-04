# BFR-016: Import-time binding of `*_DB_PATH` constants defeats pytest DB isolation

**Status**: Fixed
**Date**: 2026-06-04
**Severity**: High
**Affected**: `javdb/ops/reconcile/persistence.py`, `javdb/ops/sentinel/persistence.py`, `javdb/ops/diagnosis/persistence.py`, `javdb/spider/detail/runner.py`, `javdb/storage/repos/preference_repo.py`, `javdb/storage/repos/metadata_repo.py`, `javdb/migrations/tools/backfill_movie_metadata.py`, `javdb/migrations/tools/absolutize_javdb_urls_in_history.py`
**Related**: [BFR-011](../BFR-011-OpsIncident-GetDb-Logical-Name/BFR-011-opsincident-get-db-logical-name.md) (same `get_db` path-confusion family — the logical-name variant), [IMP-ADR037-02](../ADR-037-Pipeline-Test-Harness/IMP-ADR037-02-scenario-library-record-seams.md) (whose harness `record_queued` seam works *around* this bug; this BFR is the root-cause fix), `apps/cli/ops/events.py` (the correct call-time-resolution reference pattern)

---

## Symptom

During tests, the acquisition-outcome reconciler, the drift sentinel, and the
diagnosis incident writer silently wrote to (and read from) a **non-test**
database instead of the per-test temp DB that `tests/conftest.py`'s autouse
`_isolate_sqlite` fixture provisions.

The concrete probe that surfaced it (while writing the ADR-037 Phase-2 plan):
after an in-process pipeline harness run that staged two queued torrents,
`PipelineHarness.acquisition_outcomes()` returned `[]` — the queued rows were
invisible because `record_queued()` had written them to the real
`reports/operations.db` (or the chdir'd `reports/operations.db`), not to the
isolated temp DB the harness reads back.

The failure is invisible in normal operation because `record_queued()` (and the
sentinel/diagnosis writers) wrap persistence in best-effort `try/except`, so a
write to the wrong file — or a `sqlite3.DatabaseError` on an unrelated stub
file — never propagates; the row simply never appears where the test looks.

## Root Cause

These modules bound a DB-path constant **at import time**:

```python
from javdb.storage.db import OPERATIONS_DB_PATH, get_db   # module level
...
def open_outcome_repo():
    with get_db(OPERATIONS_DB_PATH) as conn:              # uses the import-time value
        ...
```

`from javdb.storage.db import OPERATIONS_DB_PATH` copies the **value** of the
attribute into the importing module's namespace once, when that module is first
imported (test-collection time). The autouse `_isolate_sqlite` fixture later
re-points the path by reassigning the *package attribute*
(`javdb.storage.db.OPERATIONS_DB_PATH = <temp>`), but the offender module's local
name still references the original object. So `get_db(OPERATIONS_DB_PATH)`
resolves the **stale** production path, bypassing the fixture entirely.

**Why the design was wrong, not just what broke:** `from module import CONST`
is value-binding, not a live reference. Any code that (a) binds a mutable
module-level constant at import and (b) feeds it to a routing function at call
time has silently frozen the value at the earliest possible moment. It works in
production only because the `*_DB_PATH` constants never change there — which is
exactly what makes the flaw invisible until a test (or the ADR-037 harness)
re-points them. The same pattern also appeared as a default-argument capture in
the repos (`self._db_path = db_path or HISTORY_DB_PATH`), where the import-time
constant is frozen into the instance at construction.

This is a **recurring class**, not a one-off: it is the import-time-binding
sibling of [BFR-011](../BFR-011-OpsIncident-GetDb-Logical-Name/BFR-011-opsincident-get-db-logical-name.md)'s
logical-name `get_db("reports")` bug (both are `get_db`-argument-resolution
faults in the same `ops/*/persistence.py` files), and the ADR-037 harness and
`apps/cli/ops/events.py` had already independently worked around it by resolving
the path through the package at call time.

## Fix

Resolve the path **at call time** by importing the package, not the constant,
and reading the attribute when the connection is opened — the pattern already
used by `apps/cli/ops/events.py` and the ADR-037 harness:

```python
from javdb.storage import db as _db
from javdb.storage.db import get_db
...
    with get_db(_db.OPERATIONS_DB_PATH) as conn:   # live read of the package attr
        ...
```

Applied to all eight call-time consumers that fed an import-bound constant into
`get_db()`:

- `javdb/ops/reconcile/persistence.py` — `open_outcome_repo()`
- `javdb/ops/sentinel/persistence.py` — `open_fill_repo()`, `open_incident_repo()`
- `javdb/ops/diagnosis/persistence.py` — `persist_incident()`
- `javdb/spider/detail/runner.py` — `_load_content_filter_rules()`
- `javdb/storage/repos/preference_repo.py` — `PreferenceRepo.__init__` default
- `javdb/storage/repos/metadata_repo.py` — `MetadataRepo.__init__` default
- `javdb/migrations/tools/backfill_movie_metadata.py`
- `javdb/migrations/tools/absolutize_javdb_urls_in_history.py`

**Deliberately not changed:**
`javdb/migrations/tools/restore_moviehistory_supporting_actors_from_csv.py`
imports `HISTORY_DB_PATH` only to supply an argparse `default=` and help string
(display, not a `get_db()` argument) — the low-risk category. Function-level
imports elsewhere (e.g. `javdb/storage/repos/history_repo.py`,
`javdb/storage/repos/operations_repo.py`, `javdb/integrations/...`) are **not**
affected: an import statement *inside* a function re-executes on every call and
therefore already reads the live attribute.

Tests:

- Added `test_record_queued_honours_monkeypatched_operations_db_path`
  (`tests/unit/test_reconcile_service.py`): with no injected repo, it monkeypatches
  `javdb.storage.db.OPERATIONS_DB_PATH` to a temp DB, calls `record_queued()`, and
  asserts the queued row is readable through `get_db(<temp path>)`. Verified it
  **fails before** the fix (row lands elsewhere → `[]`) and **passes after**.
- Updated tests that asserted against the old import-time binding to resolve the
  path at call time the same way the code now does:
  `tests/unit/test_reconcile_persistence.py` (`_db.OPERATIONS_DB_PATH`) and
  `tests/unit/test_ops_incident_repo.py` (`persistence._db.REPORTS_DB_PATH`).

## Side Effects

None in production: the `*_DB_PATH` constants are import-time-stable in a real
run, so call-time resolution yields the identical value. The change only alters
behavior when the package attribute is re-pointed after import — i.e. under
pytest isolation and the ADR-037 harness, where it is now correct.

## Follow-Up

- [x] Root-cause fix applied to all eight `get_db()` consumers (above).
- [x] Regression test added and verified red-before / green-after.
- [ ] Optional guardrail: a lightweight import-lint (or unit test) that flags
      `from javdb.storage.db import *_DB_PATH` at module scope when the name is
      later passed to `get_db()`, to stop the pattern from re-entering the tree.
      Not added now to avoid over-engineering; revisit if it recurs again.
