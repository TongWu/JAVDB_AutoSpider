# ADR-005 PR-4: Drop Audit Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all Audit Mode code, tables, and supporting infrastructure per ADR-005 D2/D9. After this PR, the codebase writes only via Pending Mode; historical audit rows are dropped.

**Architecture:** Delete audit DDL, helper functions, write paths, rollback replay, CLI tools, workflow, and tests. Simplify WriteMode to only `'pending'`. Add v14 migration to DROP the two audit tables. Do NOT delete `db.py` itself or `JAVDB_HISTORY_WRITE_MODE` (those belong to PR-5).

**Related:** [ADR-005](../../design/adr/ADR-005-db-py-retirement-and-repo-pattern.md) decisions D2, D9, D10

---

## File Map

### Files to DELETE entirely

| File | Lines | Reason |
|------|-------|--------|
| `.github/workflows/AuditArchive.yml` | 179 | Weekly cron for pruning audit rows — no tables left to prune |
| `apps/cli/db/audit_archive.py` | 577 | CLI tool invoked by above workflow |
| `apps/cli/db/cleanup_stale_session_audits.py` | 557 | One-off orphan audit cleanup tool |
| `tests/unit/test_audit_archive.py` | 311 | Tests for audit_archive.py |
| `tests/unit/test_cleanup_stale_session_audits.py` | 216 | Tests for cleanup_stale_session_audits.py |

### Files to MODIFY

| File | Scope | What changes |
|------|-------|-------------|
| `javdb/storage/db/db.py` | Major (~1,200 lines removed) | Remove audit DDL, helpers, write paths, rollback replay |
| `javdb/storage/db/db_session.py` | Small | Remove `'audit'` from `_ALLOWED_WRITE_MODES`; simplify `_resolve_write_mode()` |
| `javdb/storage/db/db_reports.py` | Medium | Remove audit cleanup in commit; remove audit UNION in find_sessions_by_run; change 'audit' defaults to 'pending' |
| `javdb/storage/db/db_history_write.py` | Small | Remove `db_upsert_history()` wrapper |
| `javdb/storage/rollback/core.py` | Small | Remove `restore_from_audit` field from `RollbackRequest` |
| `tests/unit/test_rollback.py` | Medium | Delete audit replay test classes; keep pending/operations tests |
| `tests/unit/test_db.py` | Small | Remove audit table assertions in schema test |
| `tests/unit/test_rollback_full_fidelity.py` | Small | Delete `TestAuditCapturesEveryLiveColumn` class |
| `CLAUDE.md` | Small | Remove audit env var docs, update WriteMode descriptions |

### Files that need NO changes (graceful degradation already in place)

| File | Reason |
|------|--------|
| `apps/cli/ops/check_bake_metrics.py` | `check_orphan_audit_rows()` already catches `OperationalError` for missing tables and returns PASS |
| `tests/unit/test_check_bake_metrics.py` | `test_orphan_audit_rows_passes_when_audit_tables_already_dropped` already covers this scenario |

---

## Task 1: Delete standalone files

**Files:**
- Delete: `.github/workflows/AuditArchive.yml`
- Delete: `apps/cli/db/audit_archive.py`
- Delete: `apps/cli/db/cleanup_stale_session_audits.py`
- Delete: `tests/unit/test_audit_archive.py`
- Delete: `tests/unit/test_cleanup_stale_session_audits.py`

- [ ] **Step 1: Verify no imports of the CLI tools from other modules**

Run:
```bash
grep -rn "audit_archive\|cleanup_stale_session_audits" javdb/ apps/ --include="*.py" | grep -v "__pycache__" | grep -v "test_audit_archive\|test_cleanup_stale"
```

Expected: Only hits in the files being deleted and possibly `apps/cli/db/__init__.py`. If `__init__.py` re-exports these, remove those entries too.

- [ ] **Step 2: Delete the 5 files**

```bash
git rm .github/workflows/AuditArchive.yml
git rm apps/cli/db/audit_archive.py
git rm apps/cli/db/cleanup_stale_session_audits.py
git rm tests/unit/test_audit_archive.py
git rm tests/unit/test_cleanup_stale_session_audits.py
```

- [ ] **Step 3: Run tests to verify nothing breaks**

```bash
pytest tests/unit/ -x -q --timeout=30
```

Expected: All tests pass. No imports reference the deleted files.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(db): delete audit-archive CLI tools and workflow (ADR-005 PR-4)"
```

---

## Task 2: Remove audit DDL, constants, and helper functions from db.py

**Files:**
- Modify: `javdb/storage/db/db.py`

This task removes the audit table schema definitions, SQL INSERT templates, and all `_audit_*` helper functions. These are leaf functions — removing them first makes subsequent tasks (removing their call sites) cleaner.

- [ ] **Step 1: Remove audit table CREATE TABLE / INDEX constants**

In `javdb/storage/db/db.py`, find the block near lines 523-547 that defines `MovieHistoryAudit` and `TorrentHistoryAudit` CREATE TABLE statements and their indexes. Delete the entire block for both tables (the CREATE TABLE strings, CREATE INDEX strings).

Also remove the audit DDL recreation inside the migration function near lines 1220-1250 — the block that re-creates both audit tables with full schema for idempotency.

Also remove the ALTER TABLE statements near lines 1200-1203 that add `RunId`/`RunAttempt` columns to the audit tables.

- [ ] **Step 2: Remove audit SQL INSERT templates**

Delete these constants (near lines 2199-2217):
- `_MOVIE_AUDIT_SQL`
- `_MOVIE_AUDIT_FOR_HREF_SQL`
- `_TORRENT_AUDIT_SQL`
- `_TORRENT_AUDIT_FOR_TYPE_SQL`

- [ ] **Step 3: Remove all audit helper functions**

Delete these functions (near lines 2186-2370):
- `_audit_writes_disabled()` (~12 lines)
- `_audit_old_json()` (~8 lines)
- `_movie_audit_statement()` (~20 lines)
- `_movie_insert_audit_statement_for_href()` (~17 lines)
- `_torrent_audit_statement()` (~20 lines)
- `_torrent_insert_audit_statement_for_type()` (~20 lines)
- `_audit_record_movie_change()` (~20 lines)
- `_audit_record_torrent_change()` (~19 lines)

**Keep** `_execute_backend_batch()` — it is a generic batch execution helper used by non-audit code too.

- [ ] **Step 4: Remove JAVDB_AUDIT_WRITES_DISABLED env var handling**

Search for all references to `JAVDB_AUDIT_WRITES_DISABLED` in `db.py` and remove them. The `_audit_writes_disabled()` function already deleted above was the sole consumer; verify no other references remain:

```bash
grep -n "AUDIT_WRITES_DISABLED" javdb/storage/db/db.py
```

Expected: 0 hits.

- [ ] **Step 5: Verify the file still parses**

```bash
python3 -c "import javdb.storage.db.db"
```

Expected: ImportError or NameError for removed functions is acceptable at this stage (call sites removed in Task 3). No SyntaxError.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/db/db.py
git commit -m "refactor(db): remove audit DDL, SQL templates, and helper functions (ADR-005 PR-4)"
```

---

## Task 3: Remove audit write paths from db.py

**Files:**
- Modify: `javdb/storage/db/db.py`

This task removes the deprecated audit-mode write functions and strips audit INSERT calls from the shared upsert logic.

- [ ] **Step 1: Delete `db_upsert_history()` function**

Find `db_upsert_history()` (near line 2381, ~75 lines). This is the deprecated Phase 4 entry point with a `DeprecationWarning`. Delete the entire function.

- [ ] **Step 2: Strip audit calls from `_upsert_one_history_on_conn()`**

This function (near lines 2457-2669) interleaves audit INSERT statements with main table writes. For each of these patterns, **remove the audit statement construction and its append to the batch**, but **keep the main table INSERT/UPDATE**:

Pattern to remove (appears ~4 times):
```python
audit_stmt = _movie_audit_statement(...)  # or _movie_insert_audit_statement_for_href, etc.
if audit_stmt:
    statements.append(audit_stmt)
```

The calls to `_execute_backend_batch(conn, statements)` stay — they now just batch the main table writes.

- [ ] **Step 3: Delete `db_upsert_history_batch()` function**

Find `db_upsert_history_batch()` (near line 2672, ~60 lines). Delete entirely — it's a batch wrapper for the audit write path.

- [ ] **Step 4: Simplify `_delete_torrents_with_audit()`**

This function (near line 2734, ~27 lines) currently:
1. If no session_id: direct DELETE
2. If session_id: fetch rows → build audit statements → DELETE

After audit removal, simplify to always do a direct DELETE regardless of session_id. Remove the audit row building. Keep the function name and signature for backward compatibility (callers still reference it).

- [ ] **Step 5: Verify the file still parses and no NameErrors on audit functions**

```bash
grep -n "_movie_audit_statement\|_torrent_audit_statement\|_movie_insert_audit\|_torrent_insert_audit\|_audit_record_\|_audit_old_json\|_audit_writes_disabled\|db_upsert_history_batch" javdb/storage/db/db.py
```

Expected: 0 hits — all references to deleted functions are gone.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/db/db.py
git commit -m "refactor(db): remove audit write paths and upsert functions (ADR-005 PR-4)"
```

---

## Task 4: Remove audit rollback replay from db.py

**Files:**
- Modify: `javdb/storage/db/db.py`

- [ ] **Step 1: Delete `_rollback_history()` function**

Find `_rollback_history()` (near line 4778, ~215 lines). This is the audit replay engine that walks audit table rows in reverse and replays them. Delete the entire function.

- [ ] **Step 2: Delete `_is_orphan_audit()` function**

Find `_is_orphan_audit()` (near line 4995, ~28 lines). Only used by `_rollback_history()`. Delete entirely.

- [ ] **Step 3: Delete the inline `_delete_audit_row()` function**

This is a nested function inside `_rollback_history()` (near line 4835). It was already deleted with Step 1, but verify no standalone version exists.

- [ ] **Step 4: Modify `db_rollback_session()` to remove audit dispatch**

In `db_rollback_session()` (near line 5025), find the three-way dispatch on write_mode:

```python
if write_mode == 'pending' and sess_status == 'finalizing':
    # ... pending finalizing path
elif write_mode == 'pending':
    # ... pending in-progress path
else:
    # ... audit_replay path ← DELETE THIS BRANCH
```

Remove the `else` branch that calls `_rollback_history()`. Replace with a log warning for unknown modes:

```python
else:
    logger.warning(
        "Session %s has unexpected write_mode=%r — "
        "audit replay retired by ADR-005; skipping history rollback",
        session_id, write_mode,
    )
    result['history'] = {'mode': 'skipped', 'reason': 'audit_retired'}
```

Also update the default write_mode fallback from `'audit'` to `'pending'`:
```python
# Before:
pre_write_mode = pre_state[0] if pre_state else 'audit'
# After:
pre_write_mode = pre_state[0] if pre_state else 'pending'
```

- [ ] **Step 5: Verify no references to `_rollback_history` remain**

```bash
grep -n "_rollback_history\|_is_orphan_audit\|_delete_audit_row" javdb/storage/db/db.py
```

Expected: 0 hits.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/db/db.py
git commit -m "refactor(db): remove audit rollback replay path (ADR-005 PR-4)"
```

---

## Task 5: Clean up supporting modules

**Files:**
- Modify: `javdb/storage/db/db_session.py`
- Modify: `javdb/storage/db/db_reports.py`
- Modify: `javdb/storage/db/db_history_write.py`
- Modify: `javdb/storage/rollback/core.py`

- [ ] **Step 1: Simplify `db_session.py` WriteMode handling**

In `javdb/storage/db/db_session.py`:

1. Change `_ALLOWED_WRITE_MODES` (near line 36) from `{'audit', 'pending'}` to `{'pending'}`.

2. In `_resolve_write_mode()` (near lines 165-195): if the env var or override specifies `'audit'`, log a warning and return `'pending'` instead of accepting it:

```python
if resolved == 'audit':
    logger.warning(
        "WriteMode 'audit' requested but audit mode retired (ADR-005); "
        "falling back to 'pending'"
    )
    resolved = 'pending'
```

- [ ] **Step 2: Clean up `db_reports.py`**

In `javdb/storage/db/db_reports.py`:

1. In `db_mark_session_committed()` (near lines 174-197): remove the DELETE FROM `MovieHistoryAudit` / `TorrentHistoryAudit` block and the `pruned` counter / log line.

2. In `db_find_sessions_by_run()` (near lines 767-784): remove the loop that UNIONs `MovieHistoryAudit` / `TorrentHistoryAudit` to find orphaned sessions.

3. In `db_get_session_status()` (near line 251): change `COALESCE(WriteMode,'audit')` to `COALESCE(WriteMode,'pending')`.

4. In `db_get_session_status()` (near line 262): change the legacy fallback return from `("audit", row["Status"])` to `("pending", row["Status"])`.

5. In `db_find_stale_pending_sessions()` (near lines 362, 376): change legacy fallback from `"audit"` to `"pending"` in the list comprehension.

- [ ] **Step 3: Remove `db_upsert_history` wrapper from `db_history_write.py`**

In `javdb/storage/db/db_history_write.py` (near lines 204-207): delete the `db_upsert_history()` wrapper function that delegates to `db.py`.

- [ ] **Step 4: Remove `restore_from_audit` from `rollback/core.py`**

In `javdb/storage/rollback/core.py`:

1. Remove the `restore_from_audit: bool = True` field from the `RollbackRequest` dataclass (near line 77).

2. Update any docstring that mentions `restore_from_audit`.

3. If any code in this file reads `request.restore_from_audit`, update or remove that reference.

- [ ] **Step 5: Verify no remaining 'audit' string literals in modified files (excluding comments/docs)**

```bash
grep -n "'audit'" javdb/storage/db/db_session.py javdb/storage/db/db_reports.py javdb/storage/db/db_history_write.py javdb/storage/rollback/core.py | grep -v "#\|docstring\|comment\|log\|warn"
```

Expected: Only log/warning messages referencing 'audit' for backward compatibility. No active code paths.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/db/db_session.py javdb/storage/db/db_reports.py javdb/storage/db/db_history_write.py javdb/storage/rollback/core.py
git commit -m "refactor(db): clean up audit references in session, reports, and rollback modules (ADR-005 PR-4)"
```

---

## Task 6: Update tests

**Files:**
- Modify: `tests/unit/test_rollback.py`
- Modify: `tests/unit/test_db.py`
- Modify: `tests/unit/test_rollback_full_fidelity.py`

- [ ] **Step 1: Delete audit replay test classes from `test_rollback.py`**

In `tests/unit/test_rollback.py`, delete these test classes entirely:

- `TestRollbackHistoryAudit` (near lines 633-787): Tests INSERT/UPDATE/DELETE audit replay
- `TestOrphanPruning` (near lines 925-971): Tests orphan audit row detection and pruning
- `TestRollbackIdempotency` (near lines 977-1032): Tests audit row draining
- `TestAuditRetentionOnCommit` (near lines 900-919): Tests commit-time audit pruning

**Keep** all other test classes — they test pending-mode rollback, session lifecycle, CLI arg parsing, operations rollback, and rclone staging.

- [ ] **Step 2: Remove audit table assertions from `test_db.py`**

In `tests/unit/test_db.py`, find `test_split_db_init` (near lines 328-337). Remove the block that asserts `MovieHistoryAudit` and `TorrentHistoryAudit` exist in the schema:

```python
# DELETE this block:
conn = sqlite3.connect(history_path)
try:
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert 'MovieHistoryAudit' in tables
    assert 'TorrentHistoryAudit' in tables
finally:
    conn.close()
```

- [ ] **Step 3: Delete `TestAuditCapturesEveryLiveColumn` from `test_rollback_full_fidelity.py`**

In `tests/unit/test_rollback_full_fidelity.py`, delete the `TestAuditCapturesEveryLiveColumn` class (near line 302+). This tests that audit OldRowJson is a superset of live columns — no longer relevant.

Also remove or update the `_audited_torrent_update()` and `_audited_movie_update()` helper functions if they are only used by the deleted class.

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/unit/ -x -q --timeout=60
```

Expected: All remaining tests pass. Failures indicate code that still references deleted audit functions — fix those.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_rollback.py tests/unit/test_db.py tests/unit/test_rollback_full_fidelity.py
git commit -m "test(db): remove audit-mode tests, keep pending-mode coverage (ADR-005 PR-4)"
```

---

## Task 7: Add v14 migration, update CLAUDE.md, final verification

**Files:**
- Modify: `javdb/storage/db/db.py` (add migration)
- Modify: `CLAUDE.md`

- [x] **Step 1: Add v14 migration to drop audit tables**

In `javdb/storage/db/db.py`, find the migration chain (the `_initialize_history_db` or equivalent function that runs schema migrations in sequence). Add a v14 migration step:

```python
def _migrate_v14_drop_audit_tables(conn):
    """v14: drop MovieHistoryAudit / TorrentHistoryAudit per ADR-005."""
    conn.execute("DROP TABLE IF EXISTS MovieHistoryAudit")
    conn.execute("DROP TABLE IF EXISTS TorrentHistoryAudit")
```

Wire it into the migration dispatcher so it runs after v13. Update the schema version constant if applicable.

- [x] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, make these changes:

1. Remove `JAVDB_AUDIT_WRITES_DISABLED` from the Environment Variables section.

2. In the Session-Based Rollback System section:
   - Remove the "Audit Mode (current actual default, retiring per ADR-005)" subsection entirely.
   - Update text to state Pending Mode is the only write mode.

3. In the Domain Language section:
   - Update **Write Mode** to: `pending` (only; audit retired by ADR-005 PR-4)
   - Remove **Orphan Audit** entry.

4. In the GitHub Actions Workflows section:
   - Remove the `AuditArchive.yml` entry.

5. Remove any remaining references to `JAVDB_AUDIT_WRITES_DISABLED`.

- [x] **Step 3: Run the full test suite one final time**

```bash
pytest tests/unit/ -v --timeout=60
```

Expected: All tests pass.

- [x] **Step 4: Verify no remaining audit table references in production code**

```bash
grep -rn "MovieHistoryAudit\|TorrentHistoryAudit" javdb/ apps/ --include="*.py" | grep -v "__pycache__\|test_check_bake"
```

Expected: 0 hits in production code. The only remaining references should be in `apps/cli/ops/check_bake_metrics.py` (which handles missing tables gracefully) and its test file.

- [x] **Step 5: Verify no remaining `_audit_` function references**

```bash
grep -rn "_audit_writes_disabled\|_audit_old_json\|_movie_audit_statement\|_torrent_audit_statement\|_rollback_history\|_is_orphan_audit\|db_upsert_history_batch\|AUDIT_WRITES_DISABLED" javdb/ apps/ --include="*.py" | grep -v "__pycache__\|test_\|\.pyc"
```

Expected: 0 hits. (`align_inventory_with_moviehistory.py` hit is known PR-5 scope.)

- [x] **Step 6: Commit**

```bash
git add javdb/storage/db/db.py CLAUDE.md
git commit -m "feat(db): add v14 migration to drop audit tables, update docs (ADR-005 PR-4)"
```
