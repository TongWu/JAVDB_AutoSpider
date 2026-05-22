# ADR-005 PR-5: Delete db.py Facade

**Parent:** [ADR-005](../../design/adr/ADR-005-db-py-retirement-and-repo-pattern.md)
**Scope:** Delete `javdb/storage/db/db.py` (4,497 lines). Redistribute non-facade code into existing shell modules. Update all callers. Shell module restructuring deferred to a future PR.

## Current State

After PR-1 through PR-4:
- Shell modules (`db_connection.py`, `db_session.py`, `db_history_read.py`, `db_history_write.py`, `db_reports.py`, `db_operations.py`, `db_stats.py`, `db_rollback.py`, `db_migrations.py`) contain real SQL implementations
- Repo classes (`HistoryRepo`, `OperationsRepo`, `StatsRepo`, `SessionsRepo`, `SystemStateRepo`) provide typed wrappers
- `db_connection.py` already owns: path constants, backend detection, connection management
- `db_session.py` already owns: session ID generation, integer ID generation, write mode, run identity
- `db.py` remains as a monolith containing: DDL constants, init/migration functions, commit workflow, rollback orchestration, and ~50 facade functions that just forward calls

## What Stays in db.py (Nothing — File Deleted)

Every symbol in db.py falls into one of two categories:
1. **Facade** — thin forwarding function → delete, update callers
2. **Real logic** — relocate to the appropriate shell module

## Redistribution Plan

### db_migrations.py ← DDL + Init + Migrations

Move from db.py:
- DDL constants: `_SCHEMA_VERSION_DDL`, `_HISTORY_DDL`, `_REPORTS_DDL`, `_OPERATIONS_DDL`, `_TABLES_SQL`
- `SCHEMA_VERSION = 14`
- Init functions: `init_db()`, `_do_init()`, `_init_single_db()`, `_init_single_legacy_db()`
- All `_migrate_*` functions (v5→v6, defaults→null, session_id→text, v14 drop audit, single→split, etc.)
- All `_ensure_*` functions (rollback columns, actor columns, etc.)
- Helpers: `_detect_version()`, `_has_table()`, `_rebuild_table_with_new_ddl()`, `_quote_ident()`, `_attached_table_*()`, `_backfill_torrent_sizes_after_split()`, `moviehistory_actor_layout_ok()`, `_normalize_moviehistory_actor_column_order()`, `_materialize_report_session_status_default()`, `_dedupe_session_keyed_stats_rows()`

Current `db_migrations.py` (175 lines) has lazy-import facades for these — replace facades with real implementations.

Update `db_connection.py` to set `SCHEMA_VERSION = 14` (currently stale at 13).

### db_history_write.py ← Commit Workflow

Move from db.py:
- `db_commit_session_history()` — main commit entry point
- `_commit_one_movie()`, `_commit_session_bulk()` — commit logic
- `_upsert_one_history_on_conn()` — per-row upsert
- `_delete_torrents_with_audit()`, `_update_movie_indicators()` — commit helpers
- `_pending_distinct_hrefs()`, `_d1_retry_pending_cleanup()` — D1 helpers
- `db_resume_finalizing_session()` — idempotent resume wrapper
- Per-href locking: `_PENDING_HREF_LOCKS`, `_PENDING_HREF_LOCKS_LOCK`
- Utilities: `_compute_indicators()`, `_href_lookup_variants()`, `_bulk_run()`, `_chunked()`
- `db_batch_update_last_visited()`, `db_batch_update_movie_actors()` — batch update orchestration

Current `db_history_write.py` (195 lines) has a facade for `db_commit_session_history` — replace with real implementation.

### db_rollback.py ← Rollback Orchestration

Move from db.py:
- `db_rollback_session()` — main rollback entry point
- `_rollback_pending_in_progress()` — pending-mode rollback
- `_rollback_reports()` — reports DB cleanup
- `_rollback_operations()` — operations DB cleanup
- Dedup rollback helpers: `_session_id_to_identifier_suffix()`, `_dedup_rollback_table*()`, `_snapshot_dedup_rows_for_rollback()`, `_same_session_id()`, `_restore_dedup_records_from_rollback()`

Current `db_rollback.py` (53 lines) has facades — replace with real implementations.

### db_connection.py ← Shared Utilities

Move from db.py:
- `_execute_backend_batch()` — D1 batch execution
- `_row_to_jsonable_dict()` — Row→dict conversion
- Error type tuples: `_DB_OPERATIONAL_ERRORS`, `_DB_INTEGRITY_ERRORS`
- Update `SCHEMA_VERSION` from 13 → 14

### __init__.py ← Package Public API

Transform empty `__init__.py` into a thin re-export layer:
```python
from .db_connection import get_db, get_local_sqlite_db, close_db, current_backend
from .db_connection import HISTORY_DB_PATH, REPORTS_DB_PATH, OPERATIONS_DB_PATH
from .db_migrations import init_db, SCHEMA_VERSION
from .db_session import (set_active_session_id, get_active_session_id,
                         set_active_run_identity, get_active_run_identity,
                         set_active_write_mode, get_active_write_mode,
                         generate_session_id)
```

This enables `from javdb.storage.db import get_db` without knowing which shell module owns it.

## Caller Migration

62 import sites (excluding worktrees) need updating:

| Category | Count | Strategy |
|---|---|---|
| Shell module back-imports | 10 lines in 4 files | Replace facades with real code (above) |
| Repo back-imports | 1 line | Inline the logic or import from new location |
| Production code (`import db as _db`) | 4 files | Update to import specific symbols from shell modules |
| Migration tools | 5 files, 14 lines | Update to import from `db_connection`/`db_migrations`/`db_session` |
| Test files | 10 files, ~30 lines | Update to import from specific shell modules or repos |

### Key Import Rewrites

| Old Import | New Import |
|---|---|
| `from javdb.storage.db.db import get_db` | `from javdb.storage.db.db_connection import get_db` |
| `from javdb.storage.db.db import init_db` | `from javdb.storage.db.db_migrations import init_db` |
| `from javdb.storage.db.db import HISTORY_DB_PATH` | `from javdb.storage.db.db_connection import HISTORY_DB_PATH` |
| `from javdb.storage.db.db import _generate_session_id` | `from javdb.storage.db.db_session import generate_session_id` |
| `from javdb.storage.db.db import db_rollback_session` | `from javdb.storage.db.db_rollback import db_rollback_session` |
| `from javdb.storage.db.db import db_commit_session_history` | `from javdb.storage.db.db_history_write import db_commit_session_history` |
| `from javdb.storage.db.db import db_replace_rclone_inventory` | `from javdb.storage.db.db_operations import db_replace_rclone_inventory` |
| `from javdb.storage.db import db as _db` | Import specific symbols from shell modules |

### Circular Dependency: history_repo ↔ db.py

`history_repo.py:267` imports `db_batch_update_movie_actors` from db.py, which in turn calls `_batch_update_movie_actors` defined in history_repo. Resolution: move `db_batch_update_movie_actors` orchestration into `db_history_write.py` (breaking the cycle since db_history_write doesn't import from history_repo).

## Deleted Symbols (Facades Only)

~50 facade functions that just forward to Repos or shell modules. Callers already migrated in PR-2/PR-3 to use Repos directly. Any remaining callers are updated in this PR.

Examples: `db_load_history()`, `db_check_torrent_in_history()`, `db_save_spider_stats()`, `db_get_session_status()`, `db_replace_rclone_inventory()`, etc.

## Test Impact

- `test_db_repo_forwarding.py` — Tests facade forwarding; DELETE entire file
- `test_db.py` — Tests `_generate_session_id` from db.py; update imports to `db_session`
- `test_d1_dual.py` — Uses `import db as _db`; update to specific imports
- `test_rclone_manager.py` — Uses facade functions; update to OperationsRepo or shell modules
- `test_system_state_repo.py` — Uses `_init_single_db`, `_OPERATIONS_DDL`; update to db_migrations
- `test_batch_c_movie_history_id.py` — Uses various db.py symbols; update imports
- Integration tests — Update `init_db` imports

## Risk Mitigation

1. **Incremental commits** — one commit per relocation target (migrations, commit, rollback, connection), then one commit for caller migration, then delete
2. **Test suite gate** — full `pytest` run after each commit
3. **No behavioral changes** — pure code relocation + import rewrites; no logic changes
4. **`__init__.py` re-exports** — provides a stable package-level API surface

## Success Criteria

- `javdb/storage/db/db.py` deleted
- All 2530+ unit tests pass
- All integration tests pass
- No import references to `javdb.storage.db.db` remain (except in git history)
- `SCHEMA_VERSION` consistent at 14 across `db_connection.py` and `db_migrations.py`
