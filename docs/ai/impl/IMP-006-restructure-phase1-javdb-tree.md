# IMP-006: ADR-007 Phase 1 — Build the `javdb/` Tree

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the new `javdb/` top-level Python namespace, move every canonical module from `packages/python/javdb_*` and `packages/rust/javdb_rust_core` into it, redirect all in-repo imports + temporary compat shells to the new paths, and emit the `ADR-007-deletion-manifest.md` that Phase 3 will execute against. Phase 2 and Phase 3 are out of scope.

**Architecture:** `javdb/` is a PEP 420 namespace package (no `__init__.py` at the top). Each subpackage (`spider/`, `pipeline/`, `storage/`, `proxy/`, `integrations/`, `infra/`, `migrations/`) is a regular package with `__init__.py`. The Rust crate is co-located at `javdb/rust_core/` and installs via maturin as `javdb.rust_core`. All compat shells in `utils/`, `api/`, `migration/`, `legacy/`, `scripts/spider/`, `scripts/ingestion/` are kept alive but re-pointed to the new locations; their deletion is deferred to Phase 3.

**Tech Stack:** Python 3.11+, pytest, maturin 1.x (Rust extension build), `git mv` for renames, sed for bulk import rewrites, ripgrep for verification, Markdown for READMEs and manifest.

**Source spec:** [ADR-007](../adr/archive/ADR-007-monorepo-restructure-2026-05.md) (English version is canonical).

---

## Pre-flight: branch, baseline snapshot, sanity greps

### Task 0: Branch + baseline

**Files:**
- No file changes; only branch setup and reconnaissance.

- [ ] **Step 1: Create the working branch**

```bash
git checkout main
git pull origin main
git checkout -b refactor/phase1-javdb-tree
```

- [ ] **Step 2: Snapshot baseline pytest result**

```bash
pytest tests/ -x --tb=no -q 2>&1 | tail -5 | tee /tmp/phase1-baseline.txt
```

Expected: all tests pass on `main`. Save the exit code in `/tmp/phase1-baseline.txt` for post-task comparison.

- [ ] **Step 3: Count legacy-import baseline**

```bash
grep -rEn "from packages\.python\.javdb_" packages apps tests --include='*.py' | grep -v __pycache__ | wc -l > /tmp/phase1-canonical-baseline.txt
grep -rEn "from (utils|api|migration|legacy)\." packages apps tests --include='*.py' | grep -v __pycache__ | wc -l > /tmp/phase1-shell-baseline.txt
cat /tmp/phase1-canonical-baseline.txt /tmp/phase1-shell-baseline.txt
```

Expected: ~464 canonical imports, ~202 legacy-shell imports. These counters will drop to "0 legacy-canonical" and "0 legacy-shell" after Phase 1's import rewrite + shell redirect.

- [ ] **Step 4: Snapshot the Rust extension working state**

```bash
python3 -c "import javdb_rust_core; print('rust ok:', javdb_rust_core.__file__)"
```

Expected: prints a site-packages path. If this fails, the Rust extension was not installed — install it before continuing (`cd packages/rust/javdb_rust_core && maturin develop --release`).

---

## Task 1: Create the `javdb/` namespace skeleton

**Files:**
- Create: `javdb/` (directory)
- Create: `javdb/spider/__init__.py`, `javdb/pipeline/__init__.py`, `javdb/storage/__init__.py`, `javdb/proxy/__init__.py`, `javdb/integrations/__init__.py`, `javdb/infra/__init__.py`, `javdb/migrations/__init__.py` (empty marker files)
- **NO** `javdb/__init__.py` (PEP 420 namespace; required for `javdb.rust_core` extension to merge from site-packages)

- [ ] **Step 1: Create directory skeleton**

```bash
mkdir -p javdb/spider javdb/pipeline javdb/storage javdb/proxy javdb/integrations javdb/infra javdb/migrations
```

- [ ] **Step 2: Add empty `__init__.py` in each subpackage (but NOT at `javdb/` root)**

```bash
touch javdb/spider/__init__.py
touch javdb/pipeline/__init__.py
touch javdb/storage/__init__.py
touch javdb/proxy/__init__.py
touch javdb/integrations/__init__.py
touch javdb/infra/__init__.py
touch javdb/migrations/__init__.py
```

- [ ] **Step 3: Verify `javdb/` is a namespace package (no `__init__.py` at top level)**

```bash
ls javdb/__init__.py 2>&1 | grep -q "No such" && echo "OK: namespace package" || echo "FAIL: __init__.py exists at javdb/"
```

Expected: `OK: namespace package`.

- [ ] **Step 4: Verify subpackages import (even though empty)**

```bash
python3 -c "import javdb.spider, javdb.pipeline, javdb.storage, javdb.proxy, javdb.integrations, javdb.infra, javdb.migrations; print('namespace import ok')"
```

Expected: `namespace import ok`.

- [ ] **Step 5: Commit**

```bash
git add javdb/
git commit -m "feat(refactor): create empty javdb/ namespace package skeleton (Phase 1, ADR-007)"
```

---

## Task 2: Build `javdb/spider/` (move spider + merge core primitives + auth/login)

**Files:**
- Move: `packages/python/javdb_spider/{app,runtime,fetch,detail,services,compat,__init__.py,__main__.py,*.py}` → `javdb/spider/...`
- Move: `packages/python/javdb_core/parser.py` → `javdb/spider/parser.py`
- Move: `packages/python/javdb_core/contracts.py` → `javdb/spider/contracts.py`
- Move: `packages/python/javdb_core/url_helper.py` → `javdb/spider/url_helper.py`
- Move: `packages/python/javdb_core/filename_helper.py` → `javdb/spider/filename_helper.py`
- Move: `packages/python/javdb_core/magnet_extractor.py` → `javdb/spider/magnet_extractor.py`
- Move: `packages/python/javdb_platform/spider_gateway.py` → `javdb/spider/spider_gateway.py`
- Create: `javdb/spider/auth/__init__.py`
- Move: `packages/python/javdb_integrations/login.py` → `javdb/spider/auth/login.py`

> Note: `javdb_core/masking.py` does NOT come to `spider/` — it goes to `javdb/infra/` per ADR-007 (Task 6 below).

- [ ] **Step 1: Move the spider package wholesale**

```bash
git mv packages/python/javdb_spider/* javdb/spider/
git mv packages/python/javdb_spider/.* javdb/spider/ 2>/dev/null || true   # in case of dotfiles
rmdir packages/python/javdb_spider
```

Note: `javdb/spider/__init__.py` already exists (empty marker from Task 1). The `git mv` of `packages/python/javdb_spider/__init__.py` will overwrite the empty marker — this is correct and intended.

- [ ] **Step 2: Move core primitives in**

```bash
git mv packages/python/javdb_core/parser.py          javdb/spider/parser.py
git mv packages/python/javdb_core/contracts.py       javdb/spider/contracts.py
git mv packages/python/javdb_core/url_helper.py      javdb/spider/url_helper.py
git mv packages/python/javdb_core/filename_helper.py javdb/spider/filename_helper.py
git mv packages/python/javdb_core/magnet_extractor.py javdb/spider/magnet_extractor.py
```

- [ ] **Step 3: Move spider_gateway up**

```bash
git mv packages/python/javdb_platform/spider_gateway.py javdb/spider/spider_gateway.py
```

- [ ] **Step 4: Create auth/ subdir and move login**

```bash
mkdir -p javdb/spider/auth
touch javdb/spider/auth/__init__.py
git mv packages/python/javdb_integrations/login.py javdb/spider/auth/login.py
```

- [ ] **Step 5: Verify file layout**

```bash
ls javdb/spider/ | sort
ls javdb/spider/auth/
```

Expected listing should include: `__init__.py`, `__main__.py`, `app/`, `auth/`, `compat/`, `contracts.py`, `detail/`, `fetch/`, `filename_helper.py`, `magnet_extractor.py`, `parser.py`, `runtime/`, `services/`, `spider_gateway.py`, `url_helper.py`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(spider): move javdb_spider+core+spider_gateway+login into javdb/spider/ (Phase 1, ADR-007)"
```

---

## Task 3: Build `javdb/pipeline/` (rename ingestion + merge pipeline_service)

**Files:**
- Move: `packages/python/javdb_ingestion/*` → `javdb/pipeline/*`
- Move: `packages/python/javdb_platform/pipeline_service.py` → `javdb/pipeline/service.py`

- [ ] **Step 1: Move the ingestion package contents**

```bash
git mv packages/python/javdb_ingestion/adapters.py  javdb/pipeline/adapters.py
git mv packages/python/javdb_ingestion/engine.py    javdb/pipeline/engine.py
git mv packages/python/javdb_ingestion/models.py    javdb/pipeline/models.py
git mv packages/python/javdb_ingestion/planner.py   javdb/pipeline/planner.py
git mv packages/python/javdb_ingestion/policies.py  javdb/pipeline/policies.py
rm packages/python/javdb_ingestion/__init__.py   # the empty javdb/pipeline/__init__.py from Task 1 already exists
rmdir packages/python/javdb_ingestion
```

- [ ] **Step 2: Move pipeline_service**

```bash
git mv packages/python/javdb_platform/pipeline_service.py javdb/pipeline/service.py
```

- [ ] **Step 3: Verify**

```bash
ls javdb/pipeline/
```

Expected: `__init__.py`, `adapters.py`, `engine.py`, `models.py`, `planner.py`, `policies.py`, `service.py`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(pipeline): rename javdb_ingestion to javdb/pipeline/ + merge pipeline_service (Phase 1, ADR-007)"
```

---

## Task 4: Build `javdb/storage/` (DB layer + sessions/rollback/d1/dual/history)

**Files:**
- Create subdirs: `javdb/storage/db/`, `javdb/storage/repos/`, `javdb/storage/sessions/`, `javdb/storage/rollback/`
- Move under `javdb/storage/db/`: `db.py`, `db_connection.py`, `db_history_read.py`, `db_history_write.py`, `db_migrations.py`, `db_operations.py`, `db_reports.py`, `db_rollback.py`, `db_session.py`, `db_stats.py`
- Move under `javdb/storage/repos/`: contents of `packages/python/javdb_platform/db_layer/` (renamed `db_layer/` → `repos/`)
- Move under `javdb/storage/sessions/`: contents of `packages/python/javdb_platform/sessions/`
- Move under `javdb/storage/rollback/`: contents of `packages/python/javdb_platform/rollback/`
- Move under `javdb/storage/`: `d1_client.py`, `dual_connection.py`, `sqlite_datetime.py`, `history_manager.py`

- [ ] **Step 1: Create subdirectory skeleton**

```bash
mkdir -p javdb/storage/db javdb/storage/repos javdb/storage/sessions javdb/storage/rollback
touch javdb/storage/db/__init__.py
touch javdb/storage/repos/__init__.py
touch javdb/storage/sessions/__init__.py
touch javdb/storage/rollback/__init__.py
```

- [ ] **Step 2: Move db_*.py + db.py into javdb/storage/db/**

```bash
for f in db.py db_connection.py db_history_read.py db_history_write.py db_migrations.py db_operations.py db_reports.py db_rollback.py db_session.py db_stats.py; do
  git mv packages/python/javdb_platform/$f javdb/storage/db/$f
done
```

- [ ] **Step 3: Move db_layer → repos**

```bash
git mv packages/python/javdb_platform/db_layer/history_repo.py     javdb/storage/repos/history_repo.py
git mv packages/python/javdb_platform/db_layer/operations_repo.py  javdb/storage/repos/operations_repo.py
git mv packages/python/javdb_platform/db_layer/sessions_repo.py    javdb/storage/repos/sessions_repo.py
git mv packages/python/javdb_platform/db_layer/system_state_repo.py javdb/storage/repos/system_state_repo.py
# The existing __init__.py in db_layer (if any) is dropped; the empty marker in javdb/storage/repos/__init__.py from Task 1 remains.
rm -f packages/python/javdb_platform/db_layer/__init__.py
rmdir packages/python/javdb_platform/db_layer
```

- [ ] **Step 4: Move sessions/ and rollback/ subdirs**

```bash
git mv packages/python/javdb_platform/sessions/commit.py  javdb/storage/sessions/commit.py
rm -f packages/python/javdb_platform/sessions/__init__.py
rmdir packages/python/javdb_platform/sessions

git mv packages/python/javdb_platform/rollback/core.py    javdb/storage/rollback/core.py
rm -f packages/python/javdb_platform/rollback/__init__.py
rmdir packages/python/javdb_platform/rollback
```

- [ ] **Step 5: Move the remaining storage-related top-level files**

```bash
git mv packages/python/javdb_platform/d1_client.py        javdb/storage/d1_client.py
git mv packages/python/javdb_platform/dual_connection.py  javdb/storage/dual_connection.py
git mv packages/python/javdb_platform/sqlite_datetime.py  javdb/storage/sqlite_datetime.py
git mv packages/python/javdb_platform/history_manager.py  javdb/storage/history_manager.py
```

- [ ] **Step 6: Verify**

```bash
ls javdb/storage/ && echo "--- db ---" && ls javdb/storage/db/ && echo "--- repos ---" && ls javdb/storage/repos/
```

Expected: top-level shows `__init__.py`, `d1_client.py`, `db/`, `dual_connection.py`, `history_manager.py`, `repos/`, `rollback/`, `sessions/`, `sqlite_datetime.py`. `db/` has 10 files + `__init__.py`. `repos/` has 4 files + `__init__.py`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(storage): explode javdb_platform DB code into javdb/storage/ (Phase 1, ADR-007)"
```

---

## Task 5: Build `javdb/proxy/` (proxy management + Worker DO clients)

**Files:**
- Move under `javdb/proxy/`: `proxy_ban_manager.py` → `ban_manager.py`, `proxy_policy.py` → `policy.py`, `proxy_pool.py` → `pool.py`
- Create + populate `javdb/proxy/recommend/`: `recommend_proxy_client.py` → `client.py`, `recommend_proxy_policy.py` → `policy.py`
- Create + populate `javdb/proxy/coordinator/`: `do_client_base.py`, `proxy_coordinator_client.py`, `login_state_client.py`, `movie_claim_client.py`, `runner_registry_client.py`, `work_distributor_client.py`

- [ ] **Step 1: Create subdir skeleton**

```bash
mkdir -p javdb/proxy/recommend javdb/proxy/coordinator
touch javdb/proxy/recommend/__init__.py
touch javdb/proxy/coordinator/__init__.py
```

- [ ] **Step 2: Move proxy top-level files (with prefix drop)**

```bash
git mv packages/python/javdb_platform/proxy_ban_manager.py javdb/proxy/ban_manager.py
git mv packages/python/javdb_platform/proxy_policy.py      javdb/proxy/policy.py
git mv packages/python/javdb_platform/proxy_pool.py        javdb/proxy/pool.py
```

- [ ] **Step 3: Move recommend/ files (drop `recommend_proxy_` prefix)**

```bash
git mv packages/python/javdb_platform/recommend_proxy_client.py javdb/proxy/recommend/client.py
git mv packages/python/javdb_platform/recommend_proxy_policy.py javdb/proxy/recommend/policy.py
```

- [ ] **Step 4: Move Worker DO client files into coordinator/**

```bash
git mv packages/python/javdb_platform/do_client_base.py          javdb/proxy/coordinator/do_client_base.py
git mv packages/python/javdb_platform/proxy_coordinator_client.py javdb/proxy/coordinator/proxy_coordinator_client.py
git mv packages/python/javdb_platform/login_state_client.py      javdb/proxy/coordinator/login_state_client.py
git mv packages/python/javdb_platform/movie_claim_client.py      javdb/proxy/coordinator/movie_claim_client.py
git mv packages/python/javdb_platform/runner_registry_client.py  javdb/proxy/coordinator/runner_registry_client.py
git mv packages/python/javdb_platform/work_distributor_client.py javdb/proxy/coordinator/work_distributor_client.py
```

- [ ] **Step 5: Verify**

```bash
ls javdb/proxy/ && echo "--- recommend ---" && ls javdb/proxy/recommend/ && echo "--- coordinator ---" && ls javdb/proxy/coordinator/
```

Expected: top-level has `__init__.py`, `ban_manager.py`, `coordinator/`, `policy.py`, `pool.py`, `recommend/`. `recommend/` has 3 files. `coordinator/` has 7 files.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(proxy): explode javdb_platform proxy_*/recommend_*/coordinator clients into javdb/proxy/ (Phase 1, ADR-007)"
```

---

## Task 6: Build `javdb/infra/` (cross-cutting infrastructure)

**Files (rename + relocate per ADR-007 file map):**
- `config_helper.py` → `javdb/infra/config.py`
- `config_generator.py` → `javdb/infra/config_generator.py`
- `csv_writer.py` → `javdb/infra/csv_writer.py`
- `git_helper.py` → `javdb/infra/git_helper.py`
- `logging_config.py` → `javdb/infra/logging.py`
- `path_helper.py` → `javdb/infra/paths.py`
- `request_handler.py` → `javdb/infra/request.py`
- `qb_config.py` → `javdb/integrations/qb/config.py` (handled in Task 7; mentioned here for context)
- From `javdb_core/masking.py` → `javdb/infra/masking.py`
- From `javdb_integrations/fetch_page.py` → `javdb/infra/fetch_page.py`
- From `javdb_integrations/health_check.py` → `javdb/infra/health_check.py`

- [ ] **Step 1: Move (and rename) the seven `javdb_platform` infra files**

```bash
git mv packages/python/javdb_platform/config_helper.py    javdb/infra/config.py
git mv packages/python/javdb_platform/config_generator.py javdb/infra/config_generator.py
git mv packages/python/javdb_platform/csv_writer.py       javdb/infra/csv_writer.py
git mv packages/python/javdb_platform/git_helper.py       javdb/infra/git_helper.py
git mv packages/python/javdb_platform/logging_config.py   javdb/infra/logging.py
git mv packages/python/javdb_platform/path_helper.py      javdb/infra/paths.py
git mv packages/python/javdb_platform/request_handler.py  javdb/infra/request.py
```

- [ ] **Step 2: Move `masking.py` from javdb_core (the last surviving file in javdb_core/)**

```bash
git mv packages/python/javdb_core/masking.py javdb/infra/masking.py
rm -f packages/python/javdb_core/__init__.py
rmdir packages/python/javdb_core
```

- [ ] **Step 3: Move fetch_page and health_check from integrations**

```bash
git mv packages/python/javdb_integrations/fetch_page.py   javdb/infra/fetch_page.py
git mv packages/python/javdb_integrations/health_check.py javdb/infra/health_check.py
```

- [ ] **Step 4: Verify**

```bash
ls javdb/infra/
```

Expected 11 entries: `__init__.py`, `config.py`, `config_generator.py`, `csv_writer.py`, `fetch_page.py`, `git_helper.py`, `health_check.py`, `logging.py`, `masking.py`, `paths.py`, `request.py`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(infra): consolidate cross-cutting helpers into javdb/infra/ (Phase 1, ADR-007)"
```

---

## Task 7: Build `javdb/integrations/` (split into qb/pikpak/rclone/notify)

**Files:**
- Create subdirs: `javdb/integrations/{qb,pikpak,rclone,notify}/`
- `qb_client.py` → `javdb/integrations/qb/client.py`
- `qb_file_filter.py` → `javdb/integrations/qb/file_filter.py`
- `qb_uploader.py` → `javdb/integrations/qb/uploader.py`
- `javdb_platform/qb_config.py` → `javdb/integrations/qb/config.py`
- `pikpak_bridge.py` → `javdb/integrations/pikpak/bridge.py`
- `rclone_helper.py` → `javdb/integrations/rclone/helper.py`
- `rclone_manager.py` → `javdb/integrations/rclone/manager.py`
- `email_notification.py` → `javdb/integrations/notify/email.py`

- [ ] **Step 1: Create subdir skeleton**

```bash
mkdir -p javdb/integrations/qb javdb/integrations/pikpak javdb/integrations/rclone javdb/integrations/notify
touch javdb/integrations/qb/__init__.py
touch javdb/integrations/pikpak/__init__.py
touch javdb/integrations/rclone/__init__.py
touch javdb/integrations/notify/__init__.py
```

- [ ] **Step 2: Move qBittorrent files**

```bash
git mv packages/python/javdb_integrations/qb_client.py       javdb/integrations/qb/client.py
git mv packages/python/javdb_integrations/qb_file_filter.py  javdb/integrations/qb/file_filter.py
git mv packages/python/javdb_integrations/qb_uploader.py     javdb/integrations/qb/uploader.py
git mv packages/python/javdb_platform/qb_config.py           javdb/integrations/qb/config.py
```

- [ ] **Step 3: Move PikPak, Rclone, Notify**

```bash
git mv packages/python/javdb_integrations/pikpak_bridge.py     javdb/integrations/pikpak/bridge.py
git mv packages/python/javdb_integrations/rclone_helper.py     javdb/integrations/rclone/helper.py
git mv packages/python/javdb_integrations/rclone_manager.py    javdb/integrations/rclone/manager.py
git mv packages/python/javdb_integrations/email_notification.py javdb/integrations/notify/email.py
```

- [ ] **Step 4: Remove empty javdb_integrations dir**

```bash
rm -f packages/python/javdb_integrations/__init__.py
rmdir packages/python/javdb_integrations
```

- [ ] **Step 5: Verify**

```bash
ls javdb/integrations/qb/ && echo "---" && ls javdb/integrations/pikpak/ && echo "---" && ls javdb/integrations/rclone/ && echo "---" && ls javdb/integrations/notify/
```

Expected: `qb/` has 5 entries (4 .py + `__init__.py`); `pikpak/` has 2 (`__init__.py`, `bridge.py`); `rclone/` has 3 (`__init__.py`, `helper.py`, `manager.py`); `notify/` has 2 (`__init__.py`, `email.py`).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(integrations): split javdb_integrations into qb/pikpak/rclone/notify subdirs (Phase 1, ADR-007)"
```

---

## Task 8: Build `javdb/migrations/`

**Files:**
- Move: `packages/python/javdb_migrations/migrate_to_current.py` → `javdb/migrations/migrate_to_current.py`
- Move: `packages/python/javdb_migrations/tools/` → `javdb/migrations/tools/`
- Move: SQL files (e.g., `0042_system_state_table.sql`) into `javdb/migrations/`

- [ ] **Step 1: Move the migrations package wholesale**

```bash
git mv packages/python/javdb_migrations/migrate_to_current.py javdb/migrations/migrate_to_current.py
git mv packages/python/javdb_migrations/tools javdb/migrations/tools
# Move any SQL files
for f in packages/python/javdb_migrations/*.sql; do
  [ -e "$f" ] && git mv "$f" "javdb/migrations/$(basename $f)"
done
rm -f packages/python/javdb_migrations/__init__.py
rmdir packages/python/javdb_migrations
```

- [ ] **Step 2: Verify**

```bash
ls javdb/migrations/
```

Expected: `__init__.py`, `0042_system_state_table.sql`, `migrate_to_current.py`, `tools/`.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor(migrations): move javdb_migrations to javdb/migrations/ (Phase 1, ADR-007)"
```

---

## Task 9: Distribute the Rust adapter shims into their consumers

The `bridges/rust_adapters/` directory disappears; each adapter merges into the consumer file it serves. Each adapter is a thin "import the Rust function, fall back to Python if unavailable" shim — merging means inlining its `try/except ImportError` block at the top of the consumer file.

**Files:**
- `bridges/rust_adapters/csv_adapter.py` → inline into `javdb/infra/csv_writer.py`
- `bridges/rust_adapters/request_adapter.py` → inline into `javdb/infra/request.py`
- `bridges/rust_adapters/dedup_adapter.py` → inline into `javdb/spider/services/dedup.py`
- `bridges/rust_adapters/parser_adapter.py` → inline into `javdb/spider/parser.py`
- `bridges/rust_adapters/history_adapter.py` → inline into `javdb/storage/history_manager.py`

- [ ] **Step 1: Read each adapter file to extract its `try/except` import block**

```bash
for f in packages/python/javdb_platform/bridges/rust_adapters/*.py; do
  echo "=== $f ==="
  cat "$f"
done
```

- [ ] **Step 2: For each adapter, inline its imports and helper functions into the target consumer file**

For each pair (adapter, target):
1. Open the adapter and copy the `try: from javdb_rust_core import ... except ImportError: ...` block and any helper functions.
2. Open the target consumer file and locate its existing import block (top of file).
3. Insert the adapter's block immediately after the consumer's existing imports.
4. If the consumer already has `from javdb_rust_core import ...` or similar (legacy compat), replace it with the adapter's block instead.
5. Adjust any internal references in the adapter (e.g., it might import from another module in `bridges/`; rewrite to use the new location).

Example for `csv_adapter.py` → `javdb/infra/csv_writer.py`:

```python
# At the top of javdb/infra/csv_writer.py, after the existing imports:
try:
    from javdb_rust_core import merge_row_data as _rs_merge_row_data
    _RUST_CSV_AVAILABLE = True
except ImportError:
    _rs_merge_row_data = None
    _RUST_CSV_AVAILABLE = False
```

Repeat the same pattern for each adapter, sourcing from the file content read in Step 1.

- [ ] **Step 3: Delete the bridges/ directory after all adapters are merged**

```bash
rm -f packages/python/javdb_platform/bridges/rust_adapters/__init__.py
rm packages/python/javdb_platform/bridges/rust_adapters/*.py
rmdir packages/python/javdb_platform/bridges/rust_adapters
rm -f packages/python/javdb_platform/bridges/__init__.py
rmdir packages/python/javdb_platform/bridges
```

- [ ] **Step 4: Verify no `bridges` reference remains anywhere in code**

```bash
grep -rEn "javdb_platform\.bridges|from bridges|import bridges" packages javdb apps tests --include='*.py' | grep -v __pycache__
```

Expected: empty (no remaining `bridges` imports).

- [ ] **Step 5: Verify the affected consumer files still parse**

```bash
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['javdb/infra/csv_writer.py', 'javdb/infra/request.py', 'javdb/spider/services/dedup.py', 'javdb/spider/parser.py', 'javdb/storage/history_manager.py']]; print('all parse OK')"
```

Expected: `all parse OK`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(rust-bridges): inline rust_adapters into consumer files; drop bridges/ (Phase 1, ADR-007)"
```

---

## Task 10: Migrate the Rust crate to `javdb/rust_core/` (single-block task)

**Files:**
- Move: `packages/rust/javdb_rust_core/*` → `javdb/rust_core/*`
- Modify: `javdb/rust_core/pyproject.toml` (add `[tool.maturin] module-name = "javdb.rust_core"`)
- Modify: 15+ Python files that say `from javdb_rust_core import ...` → `from javdb.rust_core import ...`
- Modify: 14+ CI/workflow/docker locations that reference `packages/rust/javdb_rust_core`

- [ ] **Step 1: Move the Rust crate source**

```bash
mkdir -p javdb/rust_core
# Move everything (including hidden files like .cargo if present)
git mv packages/rust/javdb_rust_core/* javdb/rust_core/ 2>/dev/null || true
git mv packages/rust/javdb_rust_core/.[!.]* javdb/rust_core/ 2>/dev/null || true
rmdir packages/rust/javdb_rust_core
rmdir packages/rust
```

> **Important:** `javdb/rust_core/` must NOT contain any `__init__.py` or `.py` file at its root. Python sees this as a directory under `javdb/` but cannot import it as a package because (a) maturin's installed `.so` in site-packages wins, and (b) the source dir has no Python files. Confirm with: `ls javdb/rust_core/*.py 2>&1 | grep -q "No matches" && echo OK`.

- [ ] **Step 2: Update `pyproject.toml` to install as `javdb.rust_core`**

Open `javdb/rust_core/pyproject.toml`. The existing `[tool.maturin]` block looks like:

```toml
[tool.maturin]
features = ["pyo3/extension-module"]
```

Change to:

```toml
[tool.maturin]
features = ["pyo3/extension-module"]
module-name = "javdb.rust_core"
```

- [ ] **Step 3: Rebuild the Rust extension and verify it installs under `javdb.rust_core`**

```bash
cd javdb/rust_core
maturin develop --release
cd -
python3 -c "import javdb.rust_core; print('rust ok:', javdb.rust_core.__file__)"
```

Expected: prints a site-packages path ending in `javdb/rust_core.cpython-*.so` or similar.

- [ ] **Step 4: Verify the old `javdb_rust_core` import name is gone (it should NOT work; we are about to rewrite all callers)**

```bash
python3 -c "import javdb_rust_core" 2>&1 | grep -q "ModuleNotFoundError" && echo "OK: old name no longer importable" || echo "Old name still works (maybe site-packages still has stale install). Run: pip uninstall javdb_rust_core -y"
```

If the old name still works, uninstall it: `pip uninstall javdb_rust_core -y`.

- [ ] **Step 5: Rewrite all 15+ Python `from javdb_rust_core import` to `from javdb.rust_core import`**

```bash
# Survey first:
grep -rln "from javdb_rust_core" javdb apps tests 2>/dev/null
grep -rln "import javdb_rust_core" javdb apps tests 2>/dev/null

# Now rewrite (in-place sed):
grep -rl "from javdb_rust_core" javdb apps tests 2>/dev/null | xargs sed -i.bak 's/from javdb_rust_core /from javdb.rust_core /g'
grep -rl "import javdb_rust_core" javdb apps tests 2>/dev/null | xargs sed -i.bak 's/import javdb_rust_core/from javdb import rust_core/g'

# Clean up .bak files
find javdb apps tests -name "*.bak" -delete
```

> **macOS note:** `sed -i.bak` is the portable form. On Linux you can use `sed -i ''` instead. The `.bak` cleanup line removes the backup files sed leaves behind on macOS.

- [ ] **Step 6: Verify the rewrite**

```bash
grep -rEn "(from|import) javdb_rust_core" javdb apps tests 2>/dev/null | grep -v __pycache__
```

Expected: empty. Anything that prints needs manual fixing.

- [ ] **Step 7: Update CI workflow path filters (Rust crate path references)**

Files to edit (search for `packages/rust/javdb_rust_core` and replace with `javdb/rust_core`):

| File | Lines to update |
|---|---|
| `.github/workflows/build-rust-extension.yml` | L18 |
| `.github/workflows/docker-build.yml` | L16-17, L26-27 |
| `.github/workflows/docker-test.yml` | L18-19, L29-30, L88 |
| `.github/workflows/unit-tests.yml` | L29-30, L45-46, L117-118, L123, L127 |
| `.github/workflows/TestIngestion.yml` | L47-50 (commented lines — update for consistency) |
| `.github/actions/install-rust-wheel/action.yml` | L45, L65-66, L76 |

Batch replace:

```bash
grep -rl "packages/rust/javdb_rust_core" .github/ docker/ | xargs sed -i.bak 's|packages/rust/javdb_rust_core|javdb/rust_core|g'
find .github docker -name "*.bak" -delete
```

- [ ] **Step 8: Update `docker/Dockerfile` and `docker/Dockerfile.api` Rust path references**

The COPY directives referencing the Rust crate:

```
docker/Dockerfile:20: COPY packages/rust/javdb_rust_core/ /build/rust_core/
docker/Dockerfile.api:14: COPY packages/rust/javdb_rust_core/ /build/rust_core/
```

The Step 7 sed already handled these. Verify:

```bash
grep -n "rust_core" docker/Dockerfile docker/Dockerfile.api
```

Expected: COPY now reads `COPY javdb/rust_core/ /build/rust_core/`.

- [ ] **Step 9: Verify nothing in the repo still references the old Rust crate path**

```bash
grep -rE "packages/rust/javdb_rust_core" . --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=target --exclude-dir=__pycache__ --exclude-dir=.bak
```

Expected: empty.

- [ ] **Step 10: Run a quick pytest on a Rust-dependent test to confirm**

```bash
pytest tests/unit/test_parser.py -v --tb=short 2>&1 | tail -20
```

Expected: passes (the parser uses the Rust extension via the inlined adapter).

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor(rust): move crate to javdb/rust_core/, install as javdb.rust_core, update all imports + CI/Docker paths (Phase 1, ADR-007)"
```

---

## Task 11: Mass internal import rewrite (sed-based)

Update every `from packages.python.javdb_*` import inside `javdb/` and `apps/` to use the new `javdb.*` paths. This task does NOT touch tests, docs, workflows, or compat shells.

**Rewrite mapping table** (apply each `sed` to all `.py` files under `javdb/` and `apps/`):

| Old prefix | New prefix |
|---|---|
| `from packages.python.javdb_spider` | `from javdb.spider` |
| `from packages.python.javdb_ingestion` | `from javdb.pipeline` |
| `from packages.python.javdb_core.masking` | `from javdb.infra.masking` |
| `from packages.python.javdb_core` | `from javdb.spider` (covers parser, contracts, url_helper, filename_helper, magnet_extractor) |
| `from packages.python.javdb_migrations` | `from javdb.migrations` |
| `from packages.python.javdb_integrations.login` | `from javdb.spider.auth.login` |
| `from packages.python.javdb_integrations.fetch_page` | `from javdb.infra.fetch_page` |
| `from packages.python.javdb_integrations.health_check` | `from javdb.infra.health_check` |
| `from packages.python.javdb_integrations.qb_client` | `from javdb.integrations.qb.client` |
| `from packages.python.javdb_integrations.qb_file_filter` | `from javdb.integrations.qb.file_filter` |
| `from packages.python.javdb_integrations.qb_uploader` | `from javdb.integrations.qb.uploader` |
| `from packages.python.javdb_integrations.pikpak_bridge` | `from javdb.integrations.pikpak.bridge` |
| `from packages.python.javdb_integrations.rclone_helper` | `from javdb.integrations.rclone.helper` |
| `from packages.python.javdb_integrations.rclone_manager` | `from javdb.integrations.rclone.manager` |
| `from packages.python.javdb_integrations.email_notification` | `from javdb.integrations.notify.email` |
| `from packages.python.javdb_platform.db_layer` | `from javdb.storage.repos` |
| `from packages.python.javdb_platform.sessions` | `from javdb.storage.sessions` |
| `from packages.python.javdb_platform.rollback` | `from javdb.storage.rollback` |
| `from packages.python.javdb_platform.d1_client` | `from javdb.storage.d1_client` |
| `from packages.python.javdb_platform.dual_connection` | `from javdb.storage.dual_connection` |
| `from packages.python.javdb_platform.sqlite_datetime` | `from javdb.storage.sqlite_datetime` |
| `from packages.python.javdb_platform.history_manager` | `from javdb.storage.history_manager` |
| `from packages.python.javdb_platform.db` | `from javdb.storage.db.db` |
| `from packages.python.javdb_platform.db_connection` | `from javdb.storage.db.db_connection` |
| `from packages.python.javdb_platform.db_history_read` | `from javdb.storage.db.db_history_read` |
| `from packages.python.javdb_platform.db_history_write` | `from javdb.storage.db.db_history_write` |
| `from packages.python.javdb_platform.db_migrations` | `from javdb.storage.db.db_migrations` |
| `from packages.python.javdb_platform.db_operations` | `from javdb.storage.db.db_operations` |
| `from packages.python.javdb_platform.db_reports` | `from javdb.storage.db.db_reports` |
| `from packages.python.javdb_platform.db_rollback` | `from javdb.storage.db.db_rollback` |
| `from packages.python.javdb_platform.db_session` | `from javdb.storage.db.db_session` |
| `from packages.python.javdb_platform.db_stats` | `from javdb.storage.db.db_stats` |
| `from packages.python.javdb_platform.proxy_ban_manager` | `from javdb.proxy.ban_manager` |
| `from packages.python.javdb_platform.proxy_policy` | `from javdb.proxy.policy` |
| `from packages.python.javdb_platform.proxy_pool` | `from javdb.proxy.pool` |
| `from packages.python.javdb_platform.recommend_proxy_client` | `from javdb.proxy.recommend.client` |
| `from packages.python.javdb_platform.recommend_proxy_policy` | `from javdb.proxy.recommend.policy` |
| `from packages.python.javdb_platform.do_client_base` | `from javdb.proxy.coordinator.do_client_base` |
| `from packages.python.javdb_platform.proxy_coordinator_client` | `from javdb.proxy.coordinator.proxy_coordinator_client` |
| `from packages.python.javdb_platform.login_state_client` | `from javdb.proxy.coordinator.login_state_client` |
| `from packages.python.javdb_platform.movie_claim_client` | `from javdb.proxy.coordinator.movie_claim_client` |
| `from packages.python.javdb_platform.runner_registry_client` | `from javdb.proxy.coordinator.runner_registry_client` |
| `from packages.python.javdb_platform.work_distributor_client` | `from javdb.proxy.coordinator.work_distributor_client` |
| `from packages.python.javdb_platform.config_helper` | `from javdb.infra.config` |
| `from packages.python.javdb_platform.config_generator` | `from javdb.infra.config_generator` |
| `from packages.python.javdb_platform.csv_writer` | `from javdb.infra.csv_writer` |
| `from packages.python.javdb_platform.git_helper` | `from javdb.infra.git_helper` |
| `from packages.python.javdb_platform.logging_config` | `from javdb.infra.logging` |
| `from packages.python.javdb_platform.path_helper` | `from javdb.infra.paths` |
| `from packages.python.javdb_platform.request_handler` | `from javdb.infra.request` |
| `from packages.python.javdb_platform.qb_config` | `from javdb.integrations.qb.config` |
| `from packages.python.javdb_platform.pipeline_service` | `from javdb.pipeline.service` |
| `from packages.python.javdb_platform.spider_gateway` | `from javdb.spider.spider_gateway` |
| `from packages.python.javdb_platform.bridges` | (already inlined in Task 9; if any remain, manual fix) |

Apply the same rewrites for `import packages.python.javdb_*` forms (rare but possible).

- [ ] **Step 1: Build a sed script file**

```bash
cat > /tmp/phase1-rewrite.sed <<'EOF'
s|from packages\.python\.javdb_spider|from javdb.spider|g
s|from packages\.python\.javdb_ingestion|from javdb.pipeline|g
s|from packages\.python\.javdb_core\.masking|from javdb.infra.masking|g
s|from packages\.python\.javdb_core|from javdb.spider|g
s|from packages\.python\.javdb_migrations|from javdb.migrations|g
s|from packages\.python\.javdb_integrations\.login|from javdb.spider.auth.login|g
s|from packages\.python\.javdb_integrations\.fetch_page|from javdb.infra.fetch_page|g
s|from packages\.python\.javdb_integrations\.health_check|from javdb.infra.health_check|g
s|from packages\.python\.javdb_integrations\.qb_client|from javdb.integrations.qb.client|g
s|from packages\.python\.javdb_integrations\.qb_file_filter|from javdb.integrations.qb.file_filter|g
s|from packages\.python\.javdb_integrations\.qb_uploader|from javdb.integrations.qb.uploader|g
s|from packages\.python\.javdb_integrations\.pikpak_bridge|from javdb.integrations.pikpak.bridge|g
s|from packages\.python\.javdb_integrations\.rclone_helper|from javdb.integrations.rclone.helper|g
s|from packages\.python\.javdb_integrations\.rclone_manager|from javdb.integrations.rclone.manager|g
s|from packages\.python\.javdb_integrations\.email_notification|from javdb.integrations.notify.email|g
s|from packages\.python\.javdb_platform\.db_layer|from javdb.storage.repos|g
s|from packages\.python\.javdb_platform\.sessions|from javdb.storage.sessions|g
s|from packages\.python\.javdb_platform\.rollback|from javdb.storage.rollback|g
s|from packages\.python\.javdb_platform\.d1_client|from javdb.storage.d1_client|g
s|from packages\.python\.javdb_platform\.dual_connection|from javdb.storage.dual_connection|g
s|from packages\.python\.javdb_platform\.sqlite_datetime|from javdb.storage.sqlite_datetime|g
s|from packages\.python\.javdb_platform\.history_manager|from javdb.storage.history_manager|g
s|from packages\.python\.javdb_platform\.db_connection|from javdb.storage.db.db_connection|g
s|from packages\.python\.javdb_platform\.db_history_read|from javdb.storage.db.db_history_read|g
s|from packages\.python\.javdb_platform\.db_history_write|from javdb.storage.db.db_history_write|g
s|from packages\.python\.javdb_platform\.db_migrations|from javdb.storage.db.db_migrations|g
s|from packages\.python\.javdb_platform\.db_operations|from javdb.storage.db.db_operations|g
s|from packages\.python\.javdb_platform\.db_reports|from javdb.storage.db.db_reports|g
s|from packages\.python\.javdb_platform\.db_rollback|from javdb.storage.db.db_rollback|g
s|from packages\.python\.javdb_platform\.db_session|from javdb.storage.db.db_session|g
s|from packages\.python\.javdb_platform\.db_stats|from javdb.storage.db.db_stats|g
s|from packages\.python\.javdb_platform\.db |from javdb.storage.db.db |g
s|from packages\.python\.javdb_platform\.db$|from javdb.storage.db.db|g
s|from packages\.python\.javdb_platform\.proxy_ban_manager|from javdb.proxy.ban_manager|g
s|from packages\.python\.javdb_platform\.proxy_policy|from javdb.proxy.policy|g
s|from packages\.python\.javdb_platform\.proxy_pool|from javdb.proxy.pool|g
s|from packages\.python\.javdb_platform\.recommend_proxy_client|from javdb.proxy.recommend.client|g
s|from packages\.python\.javdb_platform\.recommend_proxy_policy|from javdb.proxy.recommend.policy|g
s|from packages\.python\.javdb_platform\.do_client_base|from javdb.proxy.coordinator.do_client_base|g
s|from packages\.python\.javdb_platform\.proxy_coordinator_client|from javdb.proxy.coordinator.proxy_coordinator_client|g
s|from packages\.python\.javdb_platform\.login_state_client|from javdb.proxy.coordinator.login_state_client|g
s|from packages\.python\.javdb_platform\.movie_claim_client|from javdb.proxy.coordinator.movie_claim_client|g
s|from packages\.python\.javdb_platform\.runner_registry_client|from javdb.proxy.coordinator.runner_registry_client|g
s|from packages\.python\.javdb_platform\.work_distributor_client|from javdb.proxy.coordinator.work_distributor_client|g
s|from packages\.python\.javdb_platform\.config_helper|from javdb.infra.config|g
s|from packages\.python\.javdb_platform\.config_generator|from javdb.infra.config_generator|g
s|from packages\.python\.javdb_platform\.csv_writer|from javdb.infra.csv_writer|g
s|from packages\.python\.javdb_platform\.git_helper|from javdb.infra.git_helper|g
s|from packages\.python\.javdb_platform\.logging_config|from javdb.infra.logging|g
s|from packages\.python\.javdb_platform\.path_helper|from javdb.infra.paths|g
s|from packages\.python\.javdb_platform\.request_handler|from javdb.infra.request|g
s|from packages\.python\.javdb_platform\.qb_config|from javdb.integrations.qb.config|g
s|from packages\.python\.javdb_platform\.pipeline_service|from javdb.pipeline.service|g
s|from packages\.python\.javdb_platform\.spider_gateway|from javdb.spider.spider_gateway|g
EOF
```

- [ ] **Step 2: Apply the sed script to javdb/ and apps/**

```bash
find javdb apps -name "*.py" -not -path "*__pycache__*" | xargs sed -i.bak -f /tmp/phase1-rewrite.sed
find javdb apps -name "*.bak" -delete
```

- [ ] **Step 3: Verify NO old-style imports remain inside javdb/ or apps/**

```bash
grep -rEn "from packages\.python\.javdb_" javdb apps --include='*.py' | grep -v __pycache__
```

Expected: empty.

- [ ] **Step 4: Verify all the new javdb.* import statements at least parse**

```bash
python3 -c "
import ast, glob
errs = []
for f in glob.glob('javdb/**/*.py', recursive=True) + glob.glob('apps/**/*.py', recursive=True):
    if '__pycache__' in f: continue
    try: ast.parse(open(f).read())
    except SyntaxError as e: errs.append((f, e))
print(f'{len(errs)} parse errors')
for f,e in errs[:10]: print(f, e)
"
```

Expected: `0 parse errors`.

- [ ] **Step 5: Smoke-test the package imports without running any tests**

```bash
python3 -c "
import javdb.spider, javdb.spider.parser, javdb.spider.contracts
import javdb.pipeline, javdb.pipeline.engine
import javdb.storage, javdb.storage.repos.history_repo
import javdb.proxy, javdb.proxy.pool, javdb.proxy.coordinator.runner_registry_client
import javdb.integrations.qb.uploader
import javdb.infra.logging, javdb.infra.config
import javdb.migrations.migrate_to_current
print('all top-level imports OK')
"
```

Expected: `all top-level imports OK`. If a circular import surfaces here, fix it with `TYPE_CHECKING` or a local import; do not proceed to Task 12 until this passes.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(imports): rewrite all internal javdb/ + apps/ imports to use javdb.* (Phase 1, ADR-007)"
```

---

## Task 12: Redirect legacy compat shells to the new `javdb.*` paths

The compat wrappers in `utils/`, `api/`, `migration/`, `scripts/spider/`, `scripts/ingestion/` keep their original module paths but their `alias_module(__name__, "packages.python...")` targets are rewritten to point at the new `javdb.*` locations. Tests that still import via legacy paths (~202 occurrences) will continue to work transparently.

**Strategy:** Use the same sed mapping as Task 11 but applied across `utils/`, `api/`, `migration/`, `legacy/`, `scripts/spider/`, `scripts/ingestion/`, `compat.py`, and root `pipeline.py`.

- [ ] **Step 1: Survey current alias targets**

```bash
grep -rEn 'alias_module|extend_package_path' utils api migration legacy scripts compat.py pipeline.py 2>/dev/null | head -30
```

These are the lines being rewritten. The `alias_module` second arg is the target module string; the rewrite changes that target only.

- [ ] **Step 2: Apply the same rewrite sed script to all legacy locations**

```bash
find utils api migration legacy scripts/spider scripts/ingestion -name "*.py" -not -path "*__pycache__*" | xargs sed -i.bak -f /tmp/phase1-rewrite.sed 2>/dev/null

# Also handle compat shells written with quoted strings (alias_module's second arg is a string literal):
cat > /tmp/phase1-rewrite-strings.sed <<'EOF'
s|"packages\.python\.javdb_spider|"javdb.spider|g
s|"packages\.python\.javdb_ingestion|"javdb.pipeline|g
s|"packages\.python\.javdb_core\.masking|"javdb.infra.masking|g
s|"packages\.python\.javdb_core|"javdb.spider|g
s|"packages\.python\.javdb_migrations|"javdb.migrations|g
s|"packages\.python\.javdb_integrations\.login|"javdb.spider.auth.login|g
s|"packages\.python\.javdb_integrations\.fetch_page|"javdb.infra.fetch_page|g
s|"packages\.python\.javdb_integrations\.health_check|"javdb.infra.health_check|g
s|"packages\.python\.javdb_integrations\.qb_client|"javdb.integrations.qb.client|g
s|"packages\.python\.javdb_integrations\.qb_file_filter|"javdb.integrations.qb.file_filter|g
s|"packages\.python\.javdb_integrations\.qb_uploader|"javdb.integrations.qb.uploader|g
s|"packages\.python\.javdb_integrations\.pikpak_bridge|"javdb.integrations.pikpak.bridge|g
s|"packages\.python\.javdb_integrations\.rclone_helper|"javdb.integrations.rclone.helper|g
s|"packages\.python\.javdb_integrations\.rclone_manager|"javdb.integrations.rclone.manager|g
s|"packages\.python\.javdb_integrations\.email_notification|"javdb.integrations.notify.email|g
s|"packages\.python\.javdb_platform\.db_layer|"javdb.storage.repos|g
s|"packages\.python\.javdb_platform\.sessions|"javdb.storage.sessions|g
s|"packages\.python\.javdb_platform\.rollback|"javdb.storage.rollback|g
s|"packages\.python\.javdb_platform\.d1_client|"javdb.storage.d1_client|g
s|"packages\.python\.javdb_platform\.dual_connection|"javdb.storage.dual_connection|g
s|"packages\.python\.javdb_platform\.sqlite_datetime|"javdb.storage.sqlite_datetime|g
s|"packages\.python\.javdb_platform\.history_manager|"javdb.storage.history_manager|g
s|"packages\.python\.javdb_platform\.db_connection|"javdb.storage.db.db_connection|g
s|"packages\.python\.javdb_platform\.db_history_read|"javdb.storage.db.db_history_read|g
s|"packages\.python\.javdb_platform\.db_history_write|"javdb.storage.db.db_history_write|g
s|"packages\.python\.javdb_platform\.db_migrations|"javdb.storage.db.db_migrations|g
s|"packages\.python\.javdb_platform\.db_operations|"javdb.storage.db.db_operations|g
s|"packages\.python\.javdb_platform\.db_reports|"javdb.storage.db.db_reports|g
s|"packages\.python\.javdb_platform\.db_rollback|"javdb.storage.db.db_rollback|g
s|"packages\.python\.javdb_platform\.db_session|"javdb.storage.db.db_session|g
s|"packages\.python\.javdb_platform\.db_stats|"javdb.storage.db.db_stats|g
s|"packages\.python\.javdb_platform\.db"|"javdb.storage.db.db"|g
s|"packages\.python\.javdb_platform\.proxy_ban_manager|"javdb.proxy.ban_manager|g
s|"packages\.python\.javdb_platform\.proxy_policy|"javdb.proxy.policy|g
s|"packages\.python\.javdb_platform\.proxy_pool|"javdb.proxy.pool|g
s|"packages\.python\.javdb_platform\.recommend_proxy_client|"javdb.proxy.recommend.client|g
s|"packages\.python\.javdb_platform\.recommend_proxy_policy|"javdb.proxy.recommend.policy|g
s|"packages\.python\.javdb_platform\.do_client_base|"javdb.proxy.coordinator.do_client_base|g
s|"packages\.python\.javdb_platform\.proxy_coordinator_client|"javdb.proxy.coordinator.proxy_coordinator_client|g
s|"packages\.python\.javdb_platform\.login_state_client|"javdb.proxy.coordinator.login_state_client|g
s|"packages\.python\.javdb_platform\.movie_claim_client|"javdb.proxy.coordinator.movie_claim_client|g
s|"packages\.python\.javdb_platform\.runner_registry_client|"javdb.proxy.coordinator.runner_registry_client|g
s|"packages\.python\.javdb_platform\.work_distributor_client|"javdb.proxy.coordinator.work_distributor_client|g
s|"packages\.python\.javdb_platform\.config_helper|"javdb.infra.config|g
s|"packages\.python\.javdb_platform\.config_generator|"javdb.infra.config_generator|g
s|"packages\.python\.javdb_platform\.csv_writer|"javdb.infra.csv_writer|g
s|"packages\.python\.javdb_platform\.git_helper|"javdb.infra.git_helper|g
s|"packages\.python\.javdb_platform\.logging_config|"javdb.infra.logging|g
s|"packages\.python\.javdb_platform\.path_helper|"javdb.infra.paths|g
s|"packages\.python\.javdb_platform\.request_handler|"javdb.infra.request|g
s|"packages\.python\.javdb_platform\.qb_config|"javdb.integrations.qb.config|g
s|"packages\.python\.javdb_platform\.pipeline_service|"javdb.pipeline.service|g
s|"packages\.python\.javdb_platform\.spider_gateway|"javdb.spider.spider_gateway|g
EOF

find utils api migration legacy scripts/spider scripts/ingestion -name "*.py" -not -path "*__pycache__*" | xargs sed -i.bak -f /tmp/phase1-rewrite-strings.sed 2>/dev/null

# Also handle extend_package_path() helpers that pass path parts (e.g., extend_package_path(__path__, "packages", "python", "javdb_spider"))
find utils api migration legacy scripts/spider scripts/ingestion -name "*.py" -not -path "*__pycache__*" | xargs sed -i.bak -E 's|"packages",[[:space:]]*"python",[[:space:]]*"javdb_spider"|"javdb", "spider"|g; s|"packages",[[:space:]]*"python",[[:space:]]*"javdb_ingestion"|"javdb", "pipeline"|g; s|"packages",[[:space:]]*"python",[[:space:]]*"javdb_migrations"|"javdb", "migrations"|g'

# Clean up .bak
find utils api migration legacy scripts -name "*.bak" -delete
```

- [ ] **Step 3: Verify all legacy shells now point at javdb.\***

```bash
grep -rEn "packages\.python\.javdb_" utils api migration legacy scripts/spider scripts/ingestion compat.py pipeline.py 2>/dev/null | grep -v __pycache__
```

Expected: empty. If any line still references `packages.python.javdb_*`, it must be hand-fixed.

- [ ] **Step 4: Import-smoke each legacy entry point**

```bash
python3 -c "from utils.parser import parse_index; print('utils.parser OK')"
python3 -c "from utils.history_manager import load_parsed_movies_history; print('utils.history_manager OK')"
python3 -c "from utils.infra.git_helper import git_commit_and_push; print('utils.infra.git_helper OK')"
python3 -c "from scripts.spider.fetch.fetch_engine import FetchEngine; print('scripts.spider OK')"
python3 -c "from api.parsers.index_parser import parse_index_page; print('api.parsers OK')"
python3 -c "from migration.tools.csv_to_sqlite import parse_csv_filename; print('migration.tools OK')"
```

Expected: each line prints `OK`. A failure means a legacy shell still has a stale `alias_module` target.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(compat): redirect legacy shells in utils/api/migration/scripts to forward to javdb.* (Phase 1, ADR-007)"
```

---

## Task 13: Delete the empty `packages/` directory

After Task 1–12 every file under `packages/` has been moved. The directory should be empty (apart from `__pycache__` artefacts).

- [ ] **Step 1: Confirm `packages/` is empty of source files**

```bash
find packages -type f -not -path "*__pycache__*" 2>/dev/null
```

Expected: empty output.

- [ ] **Step 2: Clean `__pycache__` and delete the directory**

```bash
find packages -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
rm -rf packages/
```

- [ ] **Step 3: Verify deletion**

```bash
ls packages 2>&1 | grep -q "No such" && echo "OK: packages/ removed"
```

Expected: `OK: packages/ removed`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove empty packages/ directory after migration to javdb/ (Phase 1, ADR-007)"
```

---

## Task 14: Write `README.md` per new directory (aggregate task)

A single task produces every directory README. Each README has this structure:

```markdown
# <package_name>

<one-sentence purpose, ≤30 words>

## Files

| File | Purpose |
|---|---|
| `foo.py` | <1–2 line description> |
| `bar.py` | <1–2 line description> |

## Subdirectories

- `subdir/` — <one-line purpose>

## Depends on

- Upstream callers: <packages that import this one>
- Downstream: <packages this one imports>
```

**Directories that need a README:**

- `javdb/spider/`
- `javdb/spider/auth/`
- `javdb/pipeline/`
- `javdb/storage/`
- `javdb/storage/db/`
- `javdb/storage/repos/`
- `javdb/storage/sessions/`
- `javdb/storage/rollback/`
- `javdb/proxy/`
- `javdb/proxy/recommend/`
- `javdb/proxy/coordinator/`
- `javdb/integrations/`
- `javdb/integrations/qb/`
- `javdb/integrations/pikpak/`
- `javdb/integrations/rclone/`
- `javdb/integrations/notify/`
- `javdb/infra/`
- `javdb/migrations/`
- `javdb/rust_core/` (1-line note that this is a Rust crate, refer to `Cargo.toml`)

- [ ] **Step 1: For each directory above, list its `.py` files and write a 1–2 line description per file**

```bash
for d in javdb/spider javdb/spider/auth javdb/pipeline javdb/storage javdb/storage/db javdb/storage/repos javdb/storage/sessions javdb/storage/rollback javdb/proxy javdb/proxy/recommend javdb/proxy/coordinator javdb/integrations javdb/integrations/qb javdb/integrations/pikpak javdb/integrations/rclone javdb/integrations/notify javdb/infra javdb/migrations javdb/rust_core; do
  echo "=== $d ==="
  ls $d/*.py 2>/dev/null
done
```

Use the output to populate each README. Pull descriptions from each file's existing module docstring (`head -10 <file>`); if no docstring exists, write a short description based on the file's name and the functions/classes it contains.

- [ ] **Step 2: Write each README**

Use the Write tool to create each `README.md`. Example for `javdb/spider/README.md`:

```markdown
# spider

JavDB scraping runtime: fetches index/detail pages, parses HTML, runs parallel/sequential extraction, returns canonical movie + torrent entries.

## Files

| File | Purpose |
|---|---|
| `parser.py` | HTML parser for index and detail pages; emits `MovieEntry` / `TorrentEntry` |
| `contracts.py` | Domain data types: `MovieEntry`, `TorrentEntry`, related TypedDicts |
| `url_helper.py` | JavDB URL parsing and normalisation |
| `filename_helper.py` | Local filename derivation (sanitisation, length limits) |
| `magnet_extractor.py` | Magnet link extraction from raw HTML |
| `spider_gateway.py` | High-level entrypoint used by `apps/cli/spider.py` and pipeline |
| `__main__.py` | `python -m javdb.spider` dispatch |

## Subdirectories

- `app/` — CLI entrypoint glue (`main.py`, `cli.py`, `run_service.py`)
- `runtime/` — config, state, sleep, report runtime helpers
- `fetch/` — index/backend/fallback/session/login coordinator
- `detail/` — parallel and sequential detail extraction modes
- `services/` — domain services (dedup)
- `auth/` — JavDB login session refresh
- `compat/` — explicit backwards-compatibility helpers (`csv_builder.py`)

## Depends on

- Upstream callers: `javdb.pipeline`, `apps.cli.spider`
- Downstream: `javdb.infra.*`, `javdb.rust_core`, `javdb.storage` (for history checks)
```

Apply the same template to every directory listed.

> **Heuristic for file descriptions:** Open each `.py` file. If it has a `"""docstring"""` at the top, use its first sentence verbatim. If not, infer from the principal `def`/`class` names. Each description must fit on one line.

- [ ] **Step 3: Commit**

```bash
git add javdb/**/README.md
git commit -m "docs(refactor): add README.md per javdb/ subpackage (Phase 1, ADR-007)"
```

---

## Task 15: Generate the deletion manifest

The manifest is the contract Phase 3 executes against. It enumerates, with line-level precision, every artefact Phase 3 must remove. Phase 1 produces it; Phase 3 ticks every checkbox.

**File:**
- Create: `docs/ai/adr/ADR-007-deletion-manifest.md`

- [ ] **Step 1: Run the precise discovery greps**

```bash
# Directories to remove
echo "=== Directories ==="
for d in utils api migration legacy scripts/spider scripts/ingestion packages; do
  [ -e "$d" ] && echo "- $d"
done

# Root files to remove
echo "=== Root files ==="
for f in compat.py pipeline.py; do
  [ -e "$f" ] && echo "- $f"
done

# scripts/<shell>.py files (those whose canonical is in apps/cli/)
echo "=== scripts/ shell files ==="
for f in scripts/_spider_legacy.py scripts/login.py scripts/pikpak_bridge.py scripts/qb_uploader.py scripts/qb_file_filter.py scripts/rclone_manager.py scripts/email_notification.py scripts/health_check.py scripts/fetch_page.py; do
  [ -e "$f" ] && echo "- $f"
done

# Dockerfile lines
echo "=== Dockerfile lines ==="
grep -nE "^COPY (api|legacy|migration|utils)/" docker/Dockerfile docker/Dockerfile.api

# Test imports (line-numbered)
echo "=== Test imports requiring rewrite ==="
grep -rEn "from (utils|api|migration|legacy)\.|from scripts\.(spider|ingestion)" tests --include='*.py' | grep -v __pycache__
```

- [ ] **Step 2: Write the manifest file**

Use the Write tool to create `docs/ai/adr/ADR-007-deletion-manifest.md`. Structure:

```markdown
# ADR-007 Deletion Manifest

> Generated by Phase 1 PR. Phase 3 must tick every checkbox and verify every grep gate returns empty.

This manifest is the contract for Phase 3. Every item listed here MUST be removed (for files/dirs) or rewritten (for test imports) in the Phase 3 PR. The verification grep gates at the bottom MUST return empty output.

---

## Directories to delete (recursive)

- [ ] `utils/`
- [ ] `api/`
- [ ] `migration/`
- [ ] `legacy/`
- [ ] `scripts/spider/`
- [ ] `scripts/ingestion/`

## Root files to delete

- [ ] `compat.py`
- [ ] `pipeline.py`

## scripts/ shell files to delete (canonical lives under apps/cli/)

- [ ] `scripts/_spider_legacy.py`
- [ ] `scripts/login.py`
- [ ] `scripts/pikpak_bridge.py`
- [ ] `scripts/qb_uploader.py`
- [ ] `scripts/qb_file_filter.py`
- [ ] `scripts/rclone_manager.py`
- [ ] `scripts/email_notification.py`
- [ ] `scripts/health_check.py`
- [ ] `scripts/fetch_page.py`

> Note: `scripts/audit_archive.py`, `scripts/aggregate_pending_health.py`, `scripts/pending_mode_alert_and_pause.py`, `scripts/sync_d1_to_sqlite.py`, `scripts/cleanup_stale_session_audits.py`, `scripts/dump_openapi.py`, `scripts/check_bake_metrics.py`, `scripts/profile_hot_paths.py`, `scripts/rclone_{cleanup_empty_dirs,flatten_by_size,group_jav,rename_jav,update_nfo_titles}.py` are MOVED to `apps/cli/` in Phase 2, not deleted here.

## Dockerfile lines to delete

- [ ] `docker/Dockerfile:54` — `COPY api/ ./api/`
- [ ] `docker/Dockerfile:55` — `COPY legacy/ ./legacy/`
- [ ] `docker/Dockerfile:56` — `COPY migration/ ./migration/`
- [ ] `docker/Dockerfile:57` — `COPY utils/ ./utils/`
- [ ] `docker/Dockerfile.api:45` — `COPY api/ ./api/`
- [ ] `docker/Dockerfile.api:46` — `COPY legacy/ ./legacy/`
- [ ] `docker/Dockerfile.api:47` — `COPY migration/ ./migration/`
- [ ] `docker/Dockerfile.api:48` — `COPY utils/ ./utils/`

## Test imports to rewrite

For each line below, change the legacy import to its canonical `javdb.*` or `apps.api.*` equivalent.

<!-- The following table is generated by Step 1's grep output. Paste each grep hit as a row. -->

| File | Line | Current import | New import |
|---|---|---|---|
| `tests/unit/test_parser.py` | 12 | `from utils.parser import extract_video_code, parse_index, parse_detail` | `from javdb.spider.parser import extract_video_code, parse_index, parse_detail` |
| `tests/unit/test_history_manager.py` | 13 | `from utils.history_manager import (...)` | `from javdb.storage.history_manager import (...)` |
| `tests/unit/test_history_manager.py` | 397, 412, 428, 441, 464, 479, 541, 556, 575, 592, 606, 623, 637, 648, 670, 687 | `from utils.history_manager import ...` | `from javdb.storage.history_manager import ...` |
| `tests/unit/test_rclone_helper.py` | 16 | `from utils.rclone_helper import (...)` | `from javdb.integrations.rclone.helper import (...)` |
| `tests/unit/test_git_helper.py` | 14, 243, 263, 289, 308, 338, 358, 382, 405, 438, 446, 453, 464, 516, 543, 572, 601, 616 | `from utils.infra.git_helper import ...` | `from javdb.infra.git_helper import ...` |
| `tests/unit/test_request_handler.py` | 14 | `from utils.infra.request_handler import (...)` | `from javdb.infra.request import (...)` |
| `tests/unit/test_config_generator.py` | 15 | `from utils.config_generator import (...)` | `from javdb.infra.config_generator import (...)` |
| `tests/unit/test_api_parsers.py` | 12, 19, 20, 329 | `from api.parsers.*`, `from api.models` | `from apps.api.parsers.*`, `from apps.api.models` |
| `tests/unit/test_video_code_search.py` | 14, 15 | `from api.parsers.*` | `from apps.api.parsers.*` |
| `tests/unit/test_db.py` | 1063, 1069, 1078, 1085, 1090, 1110 | `from migration.tools.csv_to_sqlite import ...` | `from javdb.migrations.tools.csv_to_sqlite import ...` |
| `tests/unit/test_login.py` | 581, 598, 606, 652, 671 | `from scripts.spider.fetch.* import ...` | `from javdb.spider.fetch.* import ...` |
| `tests/unit/test_engine.py` | 105, 132, 154, 180, 221, 222, 259, 292, 310, 335, 360, 367, 381, 393, 423, 498, 568, 603, 694, 892 | `from scripts.spider.fetch.* import ...` | `from javdb.spider.fetch.* import ...` |
| `tests/unit/test_dedup_checker.py` | 12, 417, 429 | `from scripts.spider.services.dedup import ...` | `from javdb.spider.services.dedup import ...` |
| `tests/smoke/test_spider_backends.py` | 13, 14, 15 | `from scripts.spider.fetch.* import ...` | `from javdb.spider.fetch.* import ...` |
| `tests/smoke/test_spider_detail_runner.py` | 15, 16, 258, 259, 351, 352 | `from scripts.ingestion.models`, `from scripts.spider.detail.runner`, `from scripts.spider.fetch.*` | `from javdb.pipeline.models`, `from javdb.spider.detail.runner`, `from javdb.spider.fetch.*` |

> **Note**: This table is regenerated by Step 1 grep above; paste the actual current output. The line numbers MAY shift slightly if any test file is edited between Phase 1 and Phase 3, so Phase 3 should re-run the grep before rewriting.

## Self-references to rewrite in `javdb/migrations/migrate_to_current.py`

- [ ] Lines 166, 167, 168: `from migration.tools.migrate_v6_to_v7_split`, `from migration.tools.align_inventory_with_moviehistory`, `from migration.tools.migrate_v7_to_v8` → `from javdb.migrations.tools.migrate_v6_to_v7_split`, etc.

## Verification grep commands (Phase 3 must produce empty output)

- [ ] `grep -rE "from (utils|api|migration|legacy)\." . --include='*.py' | grep -v __pycache__ | grep -v "docs/ai/architecture/audit-report-"`
- [ ] `grep -rE "from scripts\.(spider|ingestion|audit_archive|aggregate_pending_health|pending_mode_alert_and_pause|cleanup_stale_session_audits|sync_d1_to_sqlite|dump_openapi)" .`
- [ ] `grep -rE "packages\.python\.javdb_" . --include='*.py' | grep -v __pycache__`
- [ ] `grep -rEn '"utils\.|"scripts\.(spider|ingestion)\.' . --include='*.py' | grep -v __pycache__` (string-form module paths for dynamic loading)
- [ ] `ls utils/ api/ migration/ legacy/ scripts/spider/ scripts/ingestion/ 2>&1 | grep -c "No such"` returns `6`
- [ ] `python3 -c "import compat"` raises `ModuleNotFoundError`
- [ ] `python3 -c "import pipeline"` raises `ModuleNotFoundError`
- [ ] `docker build -f docker/Dockerfile .` (dry build) succeeds

---

**Generated:** `git log -1 --format='%H %ai' -- docs/ai/adr/ADR-007-deletion-manifest.md` (replace with real values after commit)
```

- [ ] **Step 3: Commit**

```bash
git add docs/ai/adr/ADR-007-deletion-manifest.md
git commit -m "docs(adr): add ADR-007 deletion manifest for Phase 3 execution (Phase 1, ADR-007)"
```

---

## Verification Gate (Phase 1)

All of the following must pass before opening the Phase 1 PR. Each command is machine-executable and its expected output is stated.

- [ ] **Gate 1: Full pytest passes**

```bash
pytest tests/ --tb=short 2>&1 | tail -3
```

Expected: ends with a line like `XXX passed, YY warnings in Zs` and exit code 0. Compare against `/tmp/phase1-baseline.txt` — counts should be ≥ baseline (no tests removed).

- [ ] **Gate 2: Every `apps/cli/*.py --help` succeeds**

```bash
for f in apps/cli/*.py; do
  name=$(basename "$f" .py)
  [ "$name" = "__init__" ] && continue
  [ "$name" = "_session_helpers" ] && continue   # internal helper
  python3 -m apps.cli.$name --help >/dev/null 2>&1 && echo "OK: $name" || echo "FAIL: $name"
done
```

Expected: every printed line starts with `OK:`. No `FAIL:` lines.

- [ ] **Gate 3: Top-level `javdb.*` packages all import cleanly**

```bash
python3 -c "
import javdb.spider, javdb.pipeline, javdb.storage, javdb.proxy, javdb.integrations, javdb.infra, javdb.migrations, javdb.rust_core
print('namespace OK')
"
```

Expected: `namespace OK`.

- [ ] **Gate 4: `javdb/` is a PEP 420 namespace package (no `__init__.py`)**

```bash
ls javdb/__init__.py 2>&1 | grep -q "No such" && echo "OK: namespace" || echo "FAIL: __init__.py exists"
```

Expected: `OK: namespace`.

- [ ] **Gate 5: Rust extension builds and is importable as `javdb.rust_core`**

```bash
cd javdb/rust_core && maturin develop --release 2>&1 | tail -2 && cd -
python3 -c "import javdb.rust_core; print('rust OK:', javdb.rust_core.__file__)"
```

Expected: maturin tail line shows `Successfully built wheel for javdb_rust_core` (or similar), and the Python import prints a `site-packages/javdb/rust_core.*.so` path.

- [ ] **Gate 6: No leftover `packages.python.javdb_*` import inside `javdb/` or `apps/`**

```bash
grep -rEn "from packages\.python\.javdb_|import packages\.python\.javdb_" javdb apps --include='*.py' | grep -v __pycache__
```

Expected: empty.

- [ ] **Gate 7: Every legacy compat shell still resolves**

```bash
for mod in utils.parser utils.history_manager utils.infra.git_helper utils.infra.request_handler utils.config_generator utils.rclone_helper api.parsers.index_parser api.models migration.tools.csv_to_sqlite scripts.spider.fetch.fetch_engine scripts.spider.services.dedup scripts.ingestion.models; do
  python3 -c "import $mod" 2>&1 | head -1 | grep -q "Error" && echo "FAIL: $mod" || echo "OK: $mod"
done
```

Expected: every line starts with `OK:`.

- [ ] **Gate 8: Deletion manifest exists and is non-empty**

```bash
[ -s docs/ai/adr/ADR-007-deletion-manifest.md ] && wc -l docs/ai/adr/ADR-007-deletion-manifest.md
```

Expected: prints a line count > 50.

- [ ] **Gate 9: `packages/` directory is gone**

```bash
ls packages 2>&1 | grep -q "No such" && echo "OK" || echo "FAIL: packages/ still exists"
```

Expected: `OK`.

If any gate fails, fix and re-run all gates before continuing to commit/PR.

---

## Final commit & PR

- [ ] **Step 1: Squash-ready commits review**

```bash
git log refactor/phase1-javdb-tree --oneline ^main
```

Expected: about 12–15 commits, each tied to a Task above.

- [ ] **Step 2: Push branch**

```bash
git push -u origin refactor/phase1-javdb-tree
```

- [ ] **Step 3: Open PR**

PR title: `refactor: Phase 1 — build javdb/ tree, redirect compat shells, generate deletion manifest (ADR-007)`

PR body MUST include:
- Link to ADR-007 (`docs/ai/adr/ADR-007-monorepo-restructure-2026-05.md`)
- Link to the deletion manifest (`docs/ai/adr/ADR-007-deletion-manifest.md`)
- Each verification Gate (1–9) with its command and the actual output captured during the run
- Statement: "Phase 2 depends on this PR. Phase 3 will execute the deletion manifest."

- [ ] **Step 4: After PR merges, proceed to Phase 2 plan**

See `docs/ai/impl/IMP-007-restructure-phase2-scripts-to-cli.md`.
