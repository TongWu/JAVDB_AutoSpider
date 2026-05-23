# ADR-005 PR-6: Internalize Shell Modules

**Parent:** [ADR-005](../../design/adr/ADR-005-db-py-retirement-and-repo-pattern.md)
**Plan:** [IMP-039](../../design/impl/IMP-039-adr005-pr6-internalize-shell-modules.md)
**Scope:** Rename 9 shell modules to `_db_*.py`, expand `__init__.py` as public API, migrate 254 import statements across 58 files.

## Current State

After PR-5 deleted `db.py`, the 9 shell modules are the real implementation:
- `db_connection.py`, `db_session.py`, `db_migrations.py` — already re-exported via `__init__.py`
- `db_history_read.py`, `db_history_write.py`, `db_reports.py`, `db_operations.py`, `db_stats.py`, `db_rollback.py` — NOT re-exported, all 58 external files import them directly

## Design Decisions

1. **All 9 modules renamed** with underscore prefix (`_db_*.py`) — signals internal implementation
2. **`__init__.py` re-exports all 65 externally-used symbols** — organized by functional area
3. **`__all__` defined** — explicit public API surface
4. **Tests use `from javdb.storage.db import X`** for function calls
5. **conftest.py imports `_db_*.py` directly** — monkeypatching module-level state requires actual module namespace
6. **Test monkeypatch imports** also use `_db_*.py` directly where needed

## Symbol Inventory

| Source Module | Public | Private (externally imported) | Total |
|---------------|--------|-------------------------------|-------|
| `_db_connection` | 7 | 6 (`_DB_OPERATIONAL_ERRORS`, etc.) | 16 |
| `_db_session` | 8 | 3 (`_SESSION_ID_SENTINEL`, etc.) | 11 |
| `_db_migrations` | 2 | 5 (`_init_single_db`, etc.) | 7 |
| `_db_history_read` | 6 | 0 | 6 |
| `_db_history_write` | 5 | 1 (`_compute_indicators`) | 6 |
| `_db_reports` | 6 | 0 | 6 |
| `_db_operations` | 18 | 0 | 18 |
| `_db_stats` | 9 | 0 | 9 |
| `_db_rollback` | 1 | 1 (`_session_id_to_identifier_suffix`) | 2 |

## Execution Phases

1. **Prepare** (non-breaking): expand `__init__.py` with all re-exports + `__all__`
2. **Migrate** (non-breaking): rewrite all 254 external import statements to use package path
3. **Rename** (atomic): `git mv` 9 files, update internal cross-imports, conftest, monkeypatch imports
4. **Docs**: update ADR-005, verify

## Risk Mitigation

- Each phase leaves codebase in a green state
- Phase 1-2 are non-breaking (both old and new paths work)
- Phase 3 is one atomic commit (all renames + fixups together)
- `grep` verification after each commit confirms no stale references
