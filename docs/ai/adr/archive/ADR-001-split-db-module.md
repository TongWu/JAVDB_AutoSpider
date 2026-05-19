# ADR-001: Split the Monolithic db.py Module

**Status**: Completed — Phases 1+2 shipped (the 9 modules listed below all exist under `javdb/storage/db/`). Phase 3 ("delete `db.py` façade") was never started; superseded by [ADR-005](../ADR-005-db-py-retirement-and-repo-pattern.md), which is now the canonical retirement path.
**Date**: 2026-05-15
**Deciders**: Architecture restructure team
**Related Implementation Plans**: None — this ADR predates the IMP file convention. Execution was tracked through PR descriptions only.

---

## Context

`packages/python/javdb_platform/db.py` is a monolithic module of **6,370 lines and 138 functions** that conflates 8 distinct responsibilities:

1. Connection management (SQLite/D1/Dual backend routing)
2. Session state management (Session ID, Run ID, Write Mode)
3. History record writes (MovieHistory/TorrentHistory upsert + audit)
4. History record reads (queries and snapshots)
5. Report session management (CRUD for ReportSessions)
6. Statistics management (SpiderStats, UploaderStats, PikpakStats)
7. Operations-table management (RcloneInventory, DedupRecords, PikpakHistory)
8. Rollback logic (Pending Mode + Audit Mode)
9. Migration helpers (v5→v6, v6→v7, column migrations)

### Problems

- **Lack of locality**: understanding a single operation (e.g. "save a history record") requires jumping around 6,370 lines.
- **Shallow module**: the interface complexity is close to the implementation complexity — it offers insufficient leverage.
- **Hard to test**: most tests are integration tests; unit tests are scarce.
- **42 import sites**: every other module depends on this giant module.

---

## Decision

We will split `db.py` into **9 modules partitioned by functional responsibility**:

1. **`db_connection.py`** — connection pool, backend routing, WAL setup
2. **`db_session.py`** — Session ID, Run ID, Write Mode state management
3. **`db_history_write.py`** — MovieHistory/TorrentHistory writes (stage + commit)
4. **`db_history_read.py`** — MovieHistory/TorrentHistory reads
5. **`db_reports.py`** — ReportSessions CRUD
6. **`db_stats.py`** — SpiderStats, UploaderStats, PikpakStats
7. **`db_operations.py`** — RcloneInventory, DedupRecords, PikpakHistory
8. **`db_rollback.py`** — rollback coordinator
9. **`db_migrations.py`** — migration helpers

### Key Design Decisions

#### 1. Split by Function vs Split by Database File

**Choice**: split by function (Option B).

**Rationale**:
- Better aligns with the "depth" principle — each module's interface is smaller and has a single responsibility.
- Read/write separation (`db_history_read.py` vs `db_history_write.py`) makes testing easier.
- Rollback logic stays independent (`db_rollback.py`), avoiding coupling with other modules.

**Tradeoffs**:
- More modules (9 vs 6).
- Need to handle circular dependencies (resolved via lazy loading).

#### 2. Keep db.py as a Facade vs Force Import Updates

**Choice**: force import updates (Option 2).

**Rationale**:
- Callers know exactly which module they depend on.
- Avoids the implicit dependencies caused by `from db import *`.
- More maintainable in the long run.

**Tradeoffs**:
- Breaking change — 42 files need to be modified.
- Requires phased migration (Phase 1 → Phase 2 → Phase 3).

#### 3. Where to Put Rollback Logic

**Choice**: a standalone `db_rollback.py` coordinator (Option 1).

**Rationale**:
- Each module exposes a public `rollback_*_for_session()` API.
- `db_rollback.py` orchestrates cross-table rollback operations.
- Clear responsibility boundaries.

**Tradeoffs**:
- Adds one extra module.
- Each module must implement the rollback interface.

#### 4. Handling Global State

**Choice**: a gradual strategy (A → B → C).

**Rationale**:
- Phase 1: keep global state (minimal change).
- Phase 2: introduce a `SessionContext` object (encapsulate state).
- Phase 3: parameter passing (eliminate global state entirely).

**Tradeoffs**:
- Requires multiple refactors.
- But each refactor is a verifiable small step.

#### 5. Testing Strategy

**Choice**: use `unittest.mock.patch` (Option A).

**Rationale**:
- Standard library, no extra dependencies.
- Can mock `get_db()` and `get_active_session_id()`.
- Unit tests run fast (0.09–0.11 seconds).

**Tradeoffs**:
- Mock code is somewhat verbose.
- Mock lifecycle has to be handled carefully.

---

## Implementation Strategy

### Phase 1: Create new modules, keep db.py as a facade

1. Create the 9 new modules.
2. Turn `db.py` into a re-export layer (~100 lines).
3. Run tests to confirm behaviour is unchanged.
4. **At this point the 42 import sites do not need to change.**

### Phase 2: Migrate import sites incrementally

Migrate package by package:
- `javdb_spider/` first (most central).
- `javdb_integrations/` next.
- `javdb_migrations/` last.

After each package migration, run that package's tests.

### Phase 3: Delete the db.py facade

- Confirm all import sites have been migrated.
- Delete `db.py`.
- Run the full test suite.

---

## Prototype Validation

We built two prototype modules to validate the design:

### 1. `db_stats.py` (370 lines, 9 functions)

**What was validated**:
- Lazy loading avoids circular dependencies ✅
- Imports from `db_connection.py` (with fallback to `db.py`) ✅
- Unit test strategy (11 tests, all passing) ✅

**Key findings**:
- The lazy-loading + fallback pattern works.
- `unittest.mock.patch` can isolate dependencies.
- `_ensure_imports()` must be mocked to avoid import errors.

### 2. `db_connection.py` (310 lines, 8 functions)

**What was validated**:
- Serves as the base module that other modules depend on ✅
- Connection pool, backend routing, WAL setup ✅
- `db_stats.py` successfully imports from `db_connection.py` ✅

**Key findings**:
- The base module should have zero dependencies (except `config_helper` and `logging_config`).
- Thread-local storage (`threading.local()`) is used for the connection pool.
- Backend routing logic is clear (sqlite/d1/dual).

---

## Lessons Learned

### 1. Lazy loading is the key to avoiding circular dependencies

```python
# db_stats.py
_get_db = None

def _ensure_imports():
    global _get_db
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import get_db
            _get_db = get_db
        except ImportError:
            # Fallback to db.py during Phase 1
            from packages.python.javdb_platform.db import get_db
            _get_db = get_db
```

### 2. Prototype first to avoid large-scale rework

Building the `db_stats.py` prototype (the simplest module) validated:
- The split design is feasible.
- The testing strategy works.
- The lazy-loading mechanism is correct.

Had we built all 9 modules upfront, the cost of reworking after discovering an issue would have been very high.

### 3. Mocks in tests need careful handling

**Problem**: directly setting `db_stats._get_db = mock_get_db` does not take effect because `_ensure_imports()` overwrites it.

**Solution**: use `@patch('..._ensure_imports')` to prevent re-imports.

### 4. Module boundaries should be based on responsibility, not physical structure

**Wrong**: split by database file (`db_history.py` contains all history.db operations).

**Right**: split by function (`db_history_read.py` vs `db_history_write.py`).

Rationale: read/write separation makes testing easier and responsibilities clearer.

### 5. Breaking changes need phased migration

Forcing updates on 42 import sites is a breaking change, but phased migration reduces risk:
- Phase 1: create new modules, keep the facade (zero breakage).
- Phase 2: migrate package by package (incremental validation).
- Phase 3: delete the facade (final cleanup).

---

## Consequences

### Positive

1. **Improved locality**: each module has a single responsibility and the relevant code is co-located.
2. **Improved testability**: unit tests can isolate dependencies and run quickly.
3. **Improved maintainability**: new developers can grasp module boundaries more easily.
4. **Improved depth**: each module exposes a simple interface and hides a complex implementation.

### Negative

1. **More modules**: from 1 module to 9.
2. **Longer import paths**: `from db import get_db` → `from db_connection import get_db`.
3. **Migration cost**: 42 files need to be edited.
4. **Learning curve**: new developers need to learn the module boundaries.

### Risks

1. **Circular dependencies**: if inter-module dependencies are designed poorly, cycles may appear.
   - **Mitigation**: use the lazy-loading + fallback mechanism.
2. **Test coverage regression**: tests may be missed during the split.
   - **Mitigation**: add unit tests for every new module.
3. **Performance regression**: lazy loading may add overhead on the first call.
   - **Mitigation**: lazy loading runs only on the first call; subsequent calls have no overhead.

---

## Related Decisions

- **ADR-002** (future): parameter-passing strategy for Session state management.
- **ADR-003** (future): migration plan from Audit Mode to Pending Mode.

---

## References

- [CONTEXT.md](../../../../CONTEXT.md) — domain terminology glossary
- [CLAUDE.md](../../../../CLAUDE.md) — project overview
- [docs/en/ops/d1-rollback.md](../../../en/ops/d1-rollback.md) — storage backend architecture
- [A Philosophy of Software Design](https://web.stanford.edu/~ouster/cgi-bin/book.php) — deep-module theory

---

## Appendix: Module Dependency Graph

```
db_connection.py (base module, zero dependencies)
    ↓
db_session.py (depends on db_connection)
    ↓
db_history_write.py (depends on db_connection + db_session)
db_history_read.py (depends on db_connection)
db_reports.py (depends on db_connection + db_session)
db_stats.py (depends on db_connection)
db_operations.py (depends on db_connection)
    ↓
db_rollback.py (coordinator, depends on all of the above)
    ↓
db_migrations.py (depends on db_connection, used during initialization)
```

---

## Appendix: Test Coverage

| Module | Lines | Functions | Tests | Coverage |
|------|------|--------|--------|--------|
| `db_connection.py` | 310 | 8 | TBD | - |
| `db_stats.py` | 370 | 9 | 11 | 100% |
| Other 7 modules | TBD | - | TBD | - |

---

## Appendix: Prototype Code Samples

### Lazy-loading mechanism in db_stats.py

```python
# Lazy imports to avoid circular dependencies
_get_db = None
_get_local_sqlite_db = None
_REPORTS_DB_PATH = None

def _ensure_imports():
    """Lazy import to avoid circular dependency with db_connection."""
    global _get_db, _get_local_sqlite_db, _REPORTS_DB_PATH
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import (
                get_db,
                get_local_sqlite_db,
                REPORTS_DB_PATH,
            )
            _get_db = get_db
            _get_local_sqlite_db = get_local_sqlite_db
            _REPORTS_DB_PATH = REPORTS_DB_PATH
        except ImportError:
            # db_connection doesn't exist yet (e.g., during Phase 1)
            # Fall back to importing from db.py
            from packages.python.javdb_platform.db import (
                get_db,
                get_local_sqlite_db,
                REPORTS_DB_PATH,
            )
            _get_db = get_db
            _get_local_sqlite_db = get_local_sqlite_db
            _REPORTS_DB_PATH = REPORTS_DB_PATH
```

### Mocking strategy in tests

```python
@patch('packages.python.javdb_platform.db_stats._ensure_imports')
def test_uses_local_sqlite_connection(self, mock_ensure_imports):
    """Should use get_local_sqlite_db() instead of get_db()"""
    mock_get_local_db = MagicMock()
    # Pre-load the lazy imports to avoid import error
    db_stats._get_local_sqlite_db = mock_get_local_db
    db_stats._REPORTS_DB_PATH = '/fake/path'
    
    # ... rest of test
```
