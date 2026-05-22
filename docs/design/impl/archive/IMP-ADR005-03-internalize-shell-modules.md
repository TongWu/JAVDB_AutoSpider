# IMP-ADR005-03: ADR-005 PR-6 — Internalize Shell Modules

**Related:** [ADR-005](../../adr/ADR-005-db-py-retirement-and-repo-pattern.md)
**Prereq:** PR-5 (IMP-ADR005-02) must be merged first
**Branch:** `adr005-pr6-internalize-shell-modules`
**Status:** Implemented on 2026-05-22

## Overview

Rename all 9 shell modules in `javdb/storage/db/` with underscore prefix (`_db_*.py`) to signal they are package-internal. Expand `__init__.py` to re-export all 65 externally-used symbols. Migrate all 254 external import statements across 58 files to use the package-level import path.

## Metrics

| Category | Count |
|----------|-------|
| Files to rename | 9 |
| Symbols to re-export | 65 (55 public + 10 private) |
| External import statements to rewrite | 254 |
| External files to modify | 58 |
| Internal cross-imports to update | 25 |
| conftest.py module imports | 8 |

## Tasks

### Task 1: Expand `__init__.py` with full re-exports

**Goal:** Make all 65 externally-used symbols available via `from javdb.storage.db import X` before migrating callers. Non-breaking — both old and new paths work simultaneously.

**File to modify:** `javdb/storage/db/__init__.py`

**Already re-exported** (3 modules, 31 symbols):
- `db_connection` — 16 symbols
- `db_migrations` — 5 symbols
- `db_session` — 11 symbols (including `_resolve_session_id` which was missed in audit — verify if externally used)

**New re-exports to add** (6 modules):

`db_history_read` (6 symbols):
- `db_load_history`, `db_load_history_snapshot`, `db_check_torrent_in_history`
- `db_get_all_history_records`, `db_batch_update_last_visited`, `db_batch_update_movie_actors`

Note: `db_batch_update_last_visited` lives in `db_history_read.py` (delegates to `db_history_write`). `db_batch_update_movie_actors` lives in `db_history_write.py`. Both are imported by external callers from their respective home modules — verify actual locations before adding.

`db_history_write` (6 symbols):
- `db_stage_history_write`, `db_commit_session_history`, `db_resume_finalizing_session`
- `db_batch_update_last_visited`, `db_batch_update_movie_actors`
- `_compute_indicators`

`db_reports` (6 symbols):
- `db_create_report_session`, `db_get_session_status`, `db_insert_report_rows`
- `db_find_stale_pending_sessions`, `db_get_latest_session_local`, `db_pending_session_stats`

`db_operations` (18 symbols):
- `db_replace_rclone_inventory`, `db_load_rclone_inventory`, `db_append_rclone_inventory`
- `db_clear_rclone_inventory`, `db_delete_rclone_inventory_paths`
- `db_open_rclone_staging`, `db_append_rclone_staging`, `db_swap_rclone_inventory`, `db_drop_rclone_staging`
- `db_save_dedup_records`, `db_load_dedup_records`, `db_append_dedup_record`
- `db_append_pikpak_history`
- `db_mark_records_deleted`, `db_mark_orphan_records`, `db_cleanup_deleted_records`
- `db_upsert_align_no_exact_match`, `db_delete_align_no_exact_match`

`db_stats` (9 symbols):
- `db_save_spider_stats`, `db_get_spider_stats`, `db_get_spider_stats_local`
- `db_save_uploader_stats`, `db_get_uploader_stats`, `db_get_uploader_stats_local`
- `db_save_pikpak_stats`, `db_get_pikpak_stats`, `db_get_pikpak_stats_local`

`db_rollback` (2 symbols):
- `db_rollback_session`, `_session_id_to_identifier_suffix`

**Also add:**
- `verify_d1_schema_versions` from `db_connection` (imported by `run_service.py`)
- `_HISTORY_DDL`, `_REPORTS_DDL` from `db_migrations` (if externally used — verify)
- `__all__` listing all public symbol names

**Verification:** `pytest tests/unit/ -x -q` passes; `python -c "from javdb.storage.db import db_rollback_session, db_save_spider_stats"` succeeds.

---

### Task 2: Migrate production and infrastructure callers

**Goal:** Rewrite all `from javdb.storage.db.db_X import Y` and `import javdb.storage.db.db_X as Z` in production code to use `from javdb.storage.db import Y`. Non-breaking.

**Pattern A** — symbol imports:

```python
# Before
from javdb.storage.db.db_operations import db_replace_rclone_inventory
# After
from javdb.storage.db import db_replace_rclone_inventory
```

**Pattern B** — module-as-namespace (function calls only, no monkeypatching):

```python
# Before
import javdb.storage.db.db_connection as _db_conn
_db_conn.get_db()
# After
from javdb.storage.db import get_db
get_db()
```

**Pattern C** — module imports that monkeypatch (infrastructure files):
These files need the actual module object for `setattr` — defer to Task 5 (rename phase).
- `javdb/migrations/tools/migrate_v6_to_v7_split.py` — patches `db_connection` and `db_migrations` module attributes
- `javdb/migrations/tools/csv_to_sqlite.py:756` — patches `db_connection`
- `javdb/migrations/tools/migrate_v5_to_v6.py:199` — patches `db_connection`

**Files to modify (26 files, ~80 import statements):**

`apps/` (6 files):
- `apps/cli/db/cleanup_stale_in_progress.py` — 3 imports (Pattern A+B)
- `apps/cli/db/commit_session.py` — 4 imports (Pattern A)
- `apps/cli/db/rollback.py` — 2 imports (Pattern A)
- `apps/cli/db/sync_d1_to_sqlite.py` — 1 import (Pattern A)
- `apps/cli/ops/profile_hot_paths.py` — 4 imports (Pattern A)
- `apps/cli/spider.py` — 1 import (Pattern A)

`apps/api/` (1 file):
- `apps/api/routers/sessions.py` — 1 import (Pattern B)

`javdb/` production (14 files):
- `javdb/infra/csv_writer.py` — 1 import (Pattern A)
- `javdb/integrations/notify/email.py` — 5 imports (Pattern A)
- `javdb/integrations/pikpak/bridge.py` — 6 imports (Pattern A)
- `javdb/integrations/qb/uploader.py` — 3 imports (Pattern A)
- `javdb/integrations/rclone/manager.py` — 6 imports (Pattern A+B)
- `javdb/spider/__main__.py` — 1 import (Pattern A)
- `javdb/spider/app/run_service.py` — 8 imports (Pattern A)
- `javdb/spider/detail/runner.py` — 1 import (Pattern A)
- `javdb/spider/services/dedup.py` — 2 imports (Pattern A)
- `javdb/storage/history_manager.py` — 2 imports (Pattern A)
- `javdb/storage/rollback/core.py` — 3 imports (Pattern A+B)
- `javdb/storage/rollback/session_helpers.py` — 1 import (Pattern A)
- `javdb/storage/sessions/commit.py` — 3 imports (Pattern A+B)

`javdb/storage/repos/` (3 files):
- `javdb/storage/repos/history_repo.py` — 8 imports (Pattern A)
- `javdb/storage/repos/operations_repo.py` — 21 imports (Pattern A)
- `javdb/storage/repos/stats_repo.py` — 9 imports (Pattern A)

`javdb/migrations/tools/` (6 files — Pattern A only, Pattern C deferred):
- `javdb/migrations/migrate_to_current.py` — 1 import
- `javdb/migrations/tools/absolutize_javdb_urls_in_history.py` — 1 import
- `javdb/migrations/tools/align_inventory_with_moviehistory.py` — 4 imports
- `javdb/migrations/tools/csv_to_sqlite.py` — 8 imports (Pattern A lines only; Pattern C line deferred)
- `javdb/migrations/tools/migrate_v5_to_v6.py` — 1 import (Pattern A; Pattern C deferred)
- `javdb/migrations/tools/migrate_v7_to_v8.py` — 5 imports
- `javdb/migrations/tools/restore_moviehistory_supporting_actors_from_csv.py` — 1 import

**Verification:** `pytest tests/unit/ -x -q` passes. `grep -rn "from javdb.storage.db.db_[a-z]" --include="*.py" apps/ javdb/` returns only Pattern C deferred sites + internal cross-imports.

---

### Task 3: Migrate test callers

**Goal:** Rewrite all test imports to use `from javdb.storage.db import Y`. For monkeypatch targets that need the module object, use string-based `monkeypatch.setattr("javdb.storage.db._db_X.func", mock)` or note for Task 5.

**Symbol imports** (Pattern A — straightforward rewrite):

```python
# Before
from javdb.storage.db.db_connection import get_db
# After
from javdb.storage.db import get_db
```

**Module imports for monkeypatching** (Pattern D):
Several test files import `javdb.storage.db.db_X as mod` then call `monkeypatch.setattr(mod, 'func', mock)`. These MUST patch the actual module namespace. Strategy: leave as `import javdb.storage.db.db_X as mod` for now — Task 5 renames to `_db_X`.

**Files to modify (23 files, ~80 import statements):**

`tests/unit/` (19 files):
- `test_batch_c_movie_history_id.py` — 4 Pattern A
- `test_cleanup_stale_in_progress.py` — 2 Pattern A
- `test_commit_session_bulk.py` — 3 Pattern A + 1 Pattern D
- `test_d1_dual.py` — 8 Pattern A
- `test_db.py` — 9 Pattern A + 1 Pattern D
- `test_db_history_write_fallback.py` — 1 Pattern A
- `test_db_write_kill_switch.py` — 2 Pattern A
- `test_dedup_checker.py` — 1 Pattern A
- `test_email_notification_p0.py` — 1 Pattern A
- `test_history_manager.py` — 3 Pattern A + 2 Pattern D
- `test_pending_torrent_overlay_merge.py` — 3 Pattern A
- `test_rclone_manager.py` — 12 Pattern A + 5 Pattern D
- `test_rollback.py` — 4 Pattern A
- `test_rollback_cli.py` — 2 Pattern A
- `test_rollback_full_fidelity.py` — 3 Pattern A
- `test_rollback_pending_mode.py` — 6 Pattern A + 1 Pattern D
- `test_spider_self_check.py` — 3 Pattern A
- `test_sync_d1_to_sqlite.py` — 3 Pattern A
- `test_system_state_repo.py` — 1 Pattern A
- `test_session_id.py` — 0 Pattern A + 1 Pattern D
- `test_adr005_pr3a_repo_callers.py` — 0 Pattern A + 12 Pattern D
- `test_pikpak_bridge.py` — 0 Pattern A + 1 Pattern D

`tests/integration/` (4 files):
- `test_align_inventory_with_moviehistory.py` — 2 Pattern A
- `test_onboarding_endpoints.py` — 1 Pattern A
- `test_sessions_endpoints.py` — 5 Pattern A
- `test_system_state_endpoints.py` — 1 Pattern A

**Verification:** `pytest tests/unit/ -x -q` passes.

---

### Task 4: Atomic rename — 9 modules to `_db_*.py`

**Goal:** Rename all 9 shell modules with underscore prefix. Update ALL remaining references in one atomic commit.

**Renames:**

| Old filename | New filename |
|-------------|-------------|
| `db_connection.py` | `_db_connection.py` |
| `db_session.py` | `_db_session.py` |
| `db_history_read.py` | `_db_history_read.py` |
| `db_history_write.py` | `_db_history_write.py` |
| `db_reports.py` | `_db_reports.py` |
| `db_operations.py` | `_db_operations.py` |
| `db_stats.py` | `_db_stats.py` |
| `db_rollback.py` | `_db_rollback.py` |
| `db_migrations.py` | `_db_migrations.py` |

**4a — Update `__init__.py` relative imports:**
Change all `from .db_X import` to `from ._db_X import`.

**4b — Update internal cross-imports (25 statements in 7 files):**

`_db_migrations.py` — 1 top-level relative import:
- `from .db_connection import (` → `from ._db_connection import (`

`_db_history_read.py` — 2 lazy imports:
- `from javdb.storage.db.db_connection import (` → `from javdb.storage.db._db_connection import (`
- `from javdb.storage.db.db_history_write import db_batch_update_last_visited` → `from javdb.storage.db._db_history_write import ...`

`_db_history_write.py` — 14 lazy imports:
- All `from javdb.storage.db.db_connection import` → `from javdb.storage.db._db_connection import`
- All `from javdb.storage.db.db_session import` → `from javdb.storage.db._db_session import`
- All `from javdb.storage.db.db_history_read import` → `from javdb.storage.db._db_history_read import`
- All `from javdb.storage.db.db_reports import` → `from javdb.storage.db._db_reports import`

`_db_operations.py` — 2 imports (1 top-level, 1 lazy):
- `from javdb.storage.db.db_session import (` → `from javdb.storage.db._db_session import (`
- `from javdb.storage.db.db_connection import (` → `from javdb.storage.db._db_connection import (`

`_db_reports.py` — 2 lazy imports:
- `from javdb.storage.db.db_connection import (` → `from javdb.storage.db._db_connection import (`
- `from javdb.storage.db.db_session import (` → `from javdb.storage.db._db_session import (`

`_db_rollback.py` — 3 lazy imports:
- `from javdb.storage.db.db_connection import (` → `from javdb.storage.db._db_connection import (`
- `from javdb.storage.db.db_reports import (` → `from javdb.storage.db._db_reports import (`
- `from javdb.storage.db.db_history_write import` → `from javdb.storage.db._db_history_write import`

`_db_stats.py` — 1 lazy import:
- `from javdb.storage.db.db_connection import (` → `from javdb.storage.db._db_connection import (`

**4c — Update `tests/conftest.py` (8 module imports):**

```python
# Before
import javdb.storage.db.db_connection as _db_conn_mod
import javdb.storage.db.db_history_read as _db_history_read_mod
# ...

# After
import javdb.storage.db._db_connection as _db_conn_mod
import javdb.storage.db._db_history_read as _db_history_read_mod
# ...
```

**4d — Update test files with Pattern D monkeypatch imports (~23 statements across ~10 files):**
All `import javdb.storage.db.db_X as mod` → `import javdb.storage.db._db_X as mod`

Files:
- `test_adr005_pr3a_repo_callers.py` — 12 imports
- `test_rclone_manager.py` — 5 imports
- `test_history_manager.py` — 2 imports
- `test_commit_session_bulk.py` — 1 import
- `test_db.py` — 1 import
- `test_rollback_pending_mode.py` — 1 import
- `test_session_id.py` — 1 import
- `test_pikpak_bridge.py` — 1 import

**4e — Update infrastructure files (Pattern C from Task 2, 3 files):**
- `javdb/migrations/tools/migrate_v6_to_v7_split.py` — 2 module imports → `_db_connection`, plus `from javdb.storage.db import db_migrations as _db_mig` → `import javdb.storage.db._db_migrations as _db_mig`
- `javdb/migrations/tools/csv_to_sqlite.py:756` — `import javdb.storage.db.db_connection as _db_conn` → `import javdb.storage.db._db_connection as _db_conn`
- `javdb/migrations/tools/migrate_v5_to_v6.py:199` — same pattern

**Verification:**
1. `grep -rn "javdb\.storage\.db\.db_[a-z]" --include="*.py" | grep -v __pycache__ | grep -v "_db_"` returns zero results
2. `pytest` full suite passes
3. `python -c "from javdb.storage.db import get_db, init_db, db_rollback_session"` succeeds

---

### Task 5: ADR-005 update and final verification

**Goal:** Update documentation to reflect PR-6 completion.

**Files to modify:**
- `docs/design/adr/ADR-005-db-py-retirement-and-repo-pattern.md` — update PR-6 progress
- `docs/design/adr/ADR-005-db-py-retirement-and-repo-pattern.zh.md` — same, Chinese
- `docs/design/impl/archive/IMP-ADR005-03-internalize-shell-modules.md` — mark status as Implemented

**Final verification checklist:**
1. `grep -rn "from javdb\.storage\.db\.db_[a-z]" --include="*.py" | grep -v __pycache__` → zero results
2. `grep -rn "import javdb\.storage\.db\.db_[a-z]" --include="*.py" | grep -v __pycache__` → zero results
3. `pytest` — full suite green
4. `python -c "from javdb.storage.db import get_db, init_db, generate_session_id, db_rollback_session, db_save_spider_stats, db_replace_rclone_inventory"` → success
5. `ls javdb/storage/db/_db_*.py | wc -l` → 9
6. `ls javdb/storage/db/db_*.py 2>/dev/null | wc -l` → 0

---

## Execution Notes

- **Branch:** Create `adr005-pr6-internalize-shell-modules` from `main` after PR-5 merges
- **Commit strategy:** One commit per task (5 commits)
- **No behavioral changes:** Pure import path rewrites + file renames
- **Test gate:** Unit tests must pass after every commit; full suite after Task 4
- **conftest.py is the sole exception:** It imports `_db_*.py` directly because monkeypatching module-level state requires access to the actual module namespace
- **Test monkeypatching:** Test files that use `monkeypatch.setattr(module, 'func', mock)` also import `_db_*.py` directly — this is unavoidable and acceptable
