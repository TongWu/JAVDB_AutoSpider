# IMP-ADR005-02: ADR-005 PR-5 — Delete db.py Facade

**Related:** [ADR-005](ADR-005-db-py-retirement-and-repo-pattern.md)
**Spec:** [2026-05-22-adr005-pr5-delete-db-py-design](../../superpowers/specs/2026-05-22-adr005-pr5-delete-db-py-design.md)
**Branch:** `adr005-pr5-delete-db-py`
**Status:** Implemented on 2026-05-22. `javdb/storage/db/db.py` is deleted; `javdb/storage/db/__init__.py` is the package public API; ADR-005 English/Chinese and IMP-ADR005-01 were updated to reference PR-5 completion.

## Overview

Delete `javdb/storage/db/db.py` (4,497 lines) by redistributing non-facade code into existing shell modules and updating all callers. Each task leaves the codebase in a passing state via temporary re-imports in db.py until the final deletion.

## Tasks

### Task 1: Move shared utilities to db_connection.py

**Goal:** Extract backend-agnostic utilities from db.py into db_connection.py so downstream tasks can import them.

**Files to modify:**
- `javdb/storage/db/db_connection.py` — add symbols
- `javdb/storage/db/db.py` — replace definitions with re-imports

**Symbols to move:**
- `_DB_OPERATIONAL_ERRORS` tuple (lines 80-88)
- `_DB_INTEGRITY_ERRORS` tuple (lines 83-88)
- `_execute_backend_batch(conn, statements)` function
- `_row_to_jsonable_dict(row)` function

**Also fix:**
- `db_connection.py` has `SCHEMA_VERSION = 13` — update to `14` (db.py already has 14 after PR-4)

**After move:** In db.py, replace the moved definitions with:

```python
from .db_connection import _DB_OPERATIONAL_ERRORS, _DB_INTEGRITY_ERRORS
from .db_connection import _execute_backend_batch, _row_to_jsonable_dict
```

**Verification:** `pytest tests/unit/ -x -q` passes

---

### Task 2: Move DDL, migrations, and init functions to db_migrations.py

**Goal:** Replace the 6 lazy-import facades in db_migrations.py with the real implementations from db.py.

**Files to modify:**
- `javdb/storage/db/db_migrations.py` — replace facades with real code
- `javdb/storage/db/db.py` — replace definitions with re-imports

**Symbols to move (DDL constants):**
- `_SCHEMA_VERSION_DDL`
- `_HISTORY_DDL`
- `_REPORTS_DDL`
- `_OPERATIONS_DDL`
- `_TABLES_SQL`

**Symbols to move (init functions):**
- `init_db(db_path=None)`
- `_do_init(db_path)`
- `_init_single_db(db_path, ddl_block, db_label)`
- `_init_single_legacy_db(db_path)`

**Symbols to move (migration functions — all private):**
- `_has_table(conn, table_name)`
- `_detect_version(conn)`
- `_migrate_v5_to_v6(conn)`
- `_ensure_moviehistory_actor_columns(conn)`
- `_moviehistory_actor_column_names(conn)`, `_moviehistory_actor_columns_all_present(conn)`, `_moviehistory_actor_columns_physical_order_ok(conn)`
- `_ensure_rollback_columns(conn)`
- `_materialize_report_session_status_default(conn)`
- `_normalize_moviehistory_actor_column_order(conn)`
- `moviehistory_actor_layout_ok(conn)` (semi-public)
- `_migrate_defaults_to_null(conn)`
- `_migrate_session_id_to_text(conn, db_label)` + helper regexes (`_DEFAULT_RE`, `_V12_*`)
- `_rebuild_table_with_new_ddl(conn, table, new_ddl, ...)`
- `_migrate_v14_drop_audit_tables(conn)`
- `_dedupe_session_keyed_stats_rows(conn)`
- `_backfill_torrent_sizes_after_split(new_conn, old_path)`
- `_moviehistory_actor_select_exprs_from_attached_old_db(conn, alias)`
- `_quote_ident(name)`, `_attached_table_info(conn, alias, table)`, `_attached_table_column_names(...)`, `_copy_attached_table_by_common_columns(...)`
- `_migrate_single_to_split(single_db_path)`

**Symbols to move (module-level state):**
- `_init_lock = threading.Lock()`

**Internal imports the moved code needs** (already available):
- `get_db`, `HISTORY_DB_PATH`, `REPORTS_DB_PATH`, `OPERATIONS_DB_PATH`, `DB_PATH`, `_open_sqlite_connection`, `_is_valid_sqlite`, `_backend_mode` — from `db_connection`
- `_execute_backend_batch`, `_DB_OPERATIONAL_ERRORS` — from `db_connection` (moved in Task 1)
- `generate_session_id` — from `db_session`

**After move:** In db.py, replace with re-imports:

```python
from .db_migrations import init_db, _init_single_db, _do_init, _init_single_legacy_db
from .db_migrations import _detect_version, _migrate_single_to_split, ...
from .db_migrations import _HISTORY_DDL, _REPORTS_DDL, _OPERATIONS_DDL, _TABLES_SQL
```

**Verification:** `pytest tests/unit/ -x -q` passes

---

### Task 3: Move commit workflow to db_history_write.py

**Goal:** Replace the `db_commit_session_history` facade in db_history_write.py with the real commit logic from db.py.

**Files to modify:**
- `javdb/storage/db/db_history_write.py` — add commit logic
- `javdb/storage/db/db.py` — replace definitions with re-imports

**Symbols to move (commit workflow):**
- `db_commit_session_history(session_id, **kwargs)` — main entry point
- `_commit_one_movie(conn, session_id, href, ...)` — per-href commit
- `_commit_session_bulk(conn, session_id, ...)` — bulk commit path
- `_upsert_one_history_on_conn(conn, movie_data, ...)` — row-level upsert
- `_delete_torrents_with_audit(conn, href, session_id)` — cleanup helper
- `_update_movie_indicators(conn, href, ...)` — post-upsert indicator update
- `_pending_distinct_hrefs(conn, session_id)` — query helper
- `_d1_retry_pending_cleanup(conn, session_id)` — D1 retry
- `db_resume_finalizing_session(session_id, **kwargs)` — resume wrapper

**Symbols to move (batch updates):**
- `db_batch_update_last_visited(conn, href_dates)` — batch DateTimeVisited update
- `db_batch_update_movie_actors(conn, href_actor_map, ...)` — batch actor update

**Symbols to move (utilities used by commit):**
- `_compute_indicators(category)` — indicator calculation
- `_href_lookup_variants(href)` — href normalization
- `_bulk_run(conn, sql, params_list)` — batch execution
- `_chunked(seq, size)` — sequence chunking
- `_PENDING_HREF_LOCKS`, `_PENDING_HREF_LOCKS_LOCK` — per-href mutexes
- `_ALLOWED_STATUSES` tuple

**Internal imports the moved code needs:**
- `get_db`, `HISTORY_DB_PATH`, `REPORTS_DB_PATH` — from `db_connection`
- `_execute_backend_batch`, `_row_to_jsonable_dict`, `_DB_OPERATIONAL_ERRORS`, `_DB_INTEGRITY_ERRORS` — from `db_connection` (Task 1)
- `get_active_session_id`, `get_active_run_identity`, `generate_session_id` — from `db_session`
- `db_begin_finalize_session`, `db_finish_commit_session`, `db_get_session_status` — from `db_reports`
- `_merge_movie_overlay_rows`, `_pending_movie_overlay_impl`, etc. — from `db_history_read`
- `category_to_indicators`, `indicators_to_category` — from `javdb.spider.contracts`
- `HistoryRepo`, `_load_history_joined`, `_batch_update_movie_actors`, `_has_meaningful_actor_data` — from `javdb.storage.repos.history_repo`

**Circular dependency resolution:**
- `history_repo.py:267` imports `db_batch_update_movie_actors` from db.py → update to import from `db_history_write`
- `db_history_read.py:370` imports `db_batch_update_last_visited` from db.py → update to import from `db_history_write`

**After move:** In db.py, replace with re-imports from db_history_write.

**Verification:** `pytest tests/unit/ -x -q` passes

---

### Task 4: Move rollback orchestration to db_rollback.py

**Goal:** Replace the 2 facades in db_rollback.py with real rollback logic from db.py.

**Files to modify:**
- `javdb/storage/db/db_rollback.py` — add rollback orchestration
- `javdb/storage/db/db.py` — replace with re-imports

**Symbols to move (rollback core):**
- `db_rollback_session(session_id, **kwargs)` — main entry point
- `_rollback_pending_in_progress(conn, session_id)` — pending-mode rollback
- `_rollback_reports(conn, session_id)` — reports cleanup
- `_rollback_operations(conn, session_id, ...)` — operations cleanup

**Symbols to move (dedup rollback helpers):**
- `_session_id_to_identifier_suffix(session_id)`
- `_dedup_rollback_table(session_id)`
- `_dedup_rollback_table_exists(conn, session_id)`
- `_ensure_dedup_rollback_table(conn, session_id)`
- `_snapshot_dedup_rows_for_rollback(conn, session_id, hrefs)`
- `_same_session_id(a, b)`
- `_restore_dedup_records_from_rollback(conn, session_id)`
- `_DEDUP_RECORD_COLUMNS` tuple

**Internal imports the moved code needs:**
- `get_db`, `HISTORY_DB_PATH`, `REPORTS_DB_PATH`, `OPERATIONS_DB_PATH` — from `db_connection`
- `_execute_backend_batch`, `_DB_OPERATIONAL_ERRORS` — from `db_connection`
- `db_get_session_status`, `db_mark_session_failed` — from `db_reports`
- `get_active_session_id` — from `db_session`

**After move:** In db.py, replace with re-imports.

**Verification:** `pytest tests/unit/ -x -q` passes

---

### Task 5: Create __init__.py package API and update all external callers

**Goal:** Set up `__init__.py` as the public API surface, then update all 62 external import sites to stop referencing `javdb.storage.db.db`.

**Files to modify:**
- `javdb/storage/db/__init__.py` — add re-exports
- All 62 import sites (listed below)

**__init__.py contents:**

```python
from .db_connection import (
    get_db, get_local_sqlite_db, close_db, current_backend,
    HISTORY_DB_PATH, REPORTS_DB_PATH, OPERATIONS_DB_PATH, DB_PATH,
    _DB_OPERATIONAL_ERRORS, _DB_INTEGRITY_ERRORS,
    _execute_backend_batch, _row_to_jsonable_dict,
)
from .db_migrations import init_db, SCHEMA_VERSION, _init_single_db, _OPERATIONS_DDL, _HISTORY_DDL, _REPORTS_DDL
from .db_session import (
    set_active_session_id, get_active_session_id,
    set_active_run_identity, get_active_run_identity,
    set_active_write_mode, get_active_write_mode,
    generate_session_id,
)
```

**Caller migration strategy:**
- `from javdb.storage.db.db import X` → `from javdb.storage.db.<module> import X` (prefer specific module)
- `from javdb.storage.db import db as _db` then `_db.func()` → rewrite to import specific symbols
- Facade function callers (e.g. `db_replace_rclone_inventory`) → import from shell module (`db_operations`) or Repo

**Production callers to update:**
1. `apps/cli/db/cleanup_stale_in_progress.py` — `import db as _db`
2. `apps/cli/db/sync_d1_to_sqlite.py` — `import db as db_mod`
3. `apps/cli/ops/profile_hot_paths.py` — `init_db`, `get_db`, `_compute_indicators`
4. `javdb/storage/rollback/core.py` — `import db as _db`
5. `javdb/storage/repos/history_repo.py:267` — `db_batch_update_movie_actors` (if not already fixed in Task 3)

**Migration tool callers to update:**
6. `javdb/migrations/tools/csv_to_sqlite.py` — `_generate_session_id`, `get_db` (8 lines)
7. `javdb/migrations/tools/align_inventory_with_moviehistory.py` — various (2 blocks)
8. `javdb/migrations/tools/migrate_v7_to_v8.py` — `moviehistory_actor_layout_ok`, `init_db`
9. `javdb/migrations/tools/absolutize_javdb_urls_in_history.py` — `HISTORY_DB_PATH`, `REPORTS_DB_PATH`
10. `javdb/migrations/tools/restore_moviehistory_supporting_actors_from_csv.py` — `HISTORY_DB_PATH`

**Test callers to update:**
11. `tests/unit/test_d1_dual.py` — 5 occurrences of `import db as _db`
12. `tests/unit/test_db.py` — `_generate_session_id`, `import db as dbmod`
13. `tests/unit/test_db_repo_forwarding.py` — DELETE entire file (tests facade forwarding)
14. `tests/unit/test_batch_c_movie_history_id.py` — various imports
15. `tests/unit/test_rclone_manager.py` — 11 import lines (facade functions)
16. `tests/unit/test_system_state_repo.py` — `_init_single_db`, `_OPERATIONS_DDL`
17. `tests/unit/test_email_notification_p0.py` — `import db as db_mod`
18. `tests/integration/test_onboarding_endpoints.py` — `init_db`
19. `tests/integration/test_sessions_endpoints.py` — `init_db`
20. `tests/integration/test_system_state_endpoints.py` — `init_db`
21. `tests/integration/test_session_id_migration.py` — `import db as db_mod`
22. `tests/integration/test_align_inventory_with_moviehistory.py` — various

**Verification:** `pytest tests/unit/ -x -q` passes (db.py still exists with re-imports)

---

### Task 6: Delete db.py and final cleanup

**Goal:** Remove db.py now that all code has been relocated and all callers updated.

**Files to delete:**
- `javdb/storage/db/db.py`
- `tests/unit/test_db_repo_forwarding.py` (if not already deleted in Task 5)

**Post-delete checks:**
1. `grep -rn "from javdb.storage.db.db import\|from javdb.storage.db import db\b" --include="*.py"` returns zero results (excluding git history and worktrees)
2. `pytest` full suite passes (unit + integration + smoke)
3. `python -c "from javdb.storage.db import get_db, init_db, generate_session_id"` succeeds

**Also:**
- ✅ Update ADR-005 progress section (both `.md` and `.zh.md`)
- ✅ Update IMP-ADR005-01 status to reference PR-5 completion

**Verification:** Full `pytest` passes, no import references to `javdb.storage.db.db` remain

---

## Execution Notes

- **Branch:** Create `adr005-pr5-delete-db-py` from `main` before starting
- **Commit strategy:** One commit per task (6 commits total)
- **No behavioral changes:** Pure code relocation + import rewrites
- **Test gate:** Unit tests must pass after every commit
- **Circular deps:** Tasks 3 and 4 break cycles by moving logic to the right module
