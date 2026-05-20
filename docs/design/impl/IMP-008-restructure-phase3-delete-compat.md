# IMP-008: ADR-007 Phase 3 — Delete Compat Shells (Precision Deletion Against Manifest)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute `docs/design/adr/ADR-007-deletion-manifest.md` — rewrite every legacy import inside `tests/` and `javdb/migrations/migrate_to_current.py`, delete every legacy top-level directory (`utils/`, `api/`, `migration/`, `legacy/`, and the residual compat subpackages under `scripts/`), delete the root `compat.py` + `pipeline.py`, clean the Dockerfile COPY directives, update all docs/wiki references, and supersede the two old architecture maps. Every checkbox in the manifest must be ticked.

**Architecture:** This phase is mechanical and verifiable: every step has a precise grep-form verification command and every output should converge on empty / `ModuleNotFoundError` / `OK`. The manifest is the contract; this plan is its execution.

**Tech Stack:** Python 3.11+, pytest, sed for bulk import rewrites, ripgrep + standard `grep` for verification, Markdown for docs updates, Docker for the dry build check.

**Source spec:** [ADR-007](../adr/archive/ADR-007-monorepo-restructure-2026-05.md) §"Phase 3" + [ADR-007-deletion-manifest.md](../adr/archive/ADR-007-deletion-manifest.md) (the canonical checklist).

**Prerequisites:** Phase 1 and Phase 2 PRs are merged. The deletion manifest exists. All in-repo non-test callers already use `javdb.*` or `apps.cli.*` paths.

**Plan A integration (landed on `main` before this phase starts):** A separate initiative — "Plan A" Phase 1 backend prerequisites for the new frontend repo `javdb-autospider-web` — merged in SHAs `33065718` + `014a1e34` (spec: `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md`). It added:

- 10 new HTTP endpoints exposed at `/api/{capabilities,onboarding/*,system/state,sessions,sessions/{id},sessions/{id}/rollback,sessions/{id}/commit}` (consumed by the FE repo via GHCR + `openapi.json`).
- 6 new integration tests + 2 new unit tests (`tests/integration/test_{capabilities,test_mode_reset,onboarding,system_state,sessions,openapi_response_shapes}_endpoints.py`, `tests/unit/test_{system_state_repo,rollback_core_library}.py`).
- A second Dockerfile at `docker/Dockerfile.api` for the FastAPI-only image consumed by `javdb-autospider-web`'s docker-compose.
- Two new GH workflows (`publish-api-image.yml`, `publish-openapi.yml`) — both already point at `javdb/**` paths filters after Phase 1's smoke fixes.
- Two new packages: `javdb/storage/rollback/core.py` (rollback library; thin adapter that imports `apps.cli.db.rollback`) and `javdb/storage/sessions/commit.py` (sessions library; calls DB functions directly).
- One migration file `javdb/migrations/0042_system_state_table.sql` (already swept by Phase 1 sed).

IMP-006 and IMP-007 already swept Plan A's static imports during their bulk passes. This phase must still **verify the integration end-to-end** at the deletion + Dockerfile-cleanup + docs-rewrite boundaries — see the call-outs inside Task 1, Task 5, and the new **Gate 13** below.

---

## Pre-flight: branch, baseline, manifest sync

### Task 0: Branch + baseline + manifest re-grep

**Files:**
- Read-only: `docs/design/adr/ADR-007-deletion-manifest.md` (consume; do not modify yet — that happens at the end as "tick checkboxes")
- No source changes in this task.

- [ ] **Step 1: Create the working branch**

```bash
git checkout main
git pull origin main   # must include Phase 1 and Phase 2 merges
git checkout -b refactor/phase3-delete-compat
```

- [ ] **Step 2: Snapshot baseline pytest**

```bash
pytest tests/ -x --tb=no -q 2>&1 | tail -3 | tee /tmp/phase3-baseline.txt
```

Expected: all tests pass on the post-Phase-2 main.

- [ ] **Step 3: Re-run the manifest's test-import grep to refresh line numbers**

The manifest table was generated at the end of Phase 1; if any test file has been edited since, line numbers may have shifted. Re-grep and use this output (NOT the manifest's stale numbers) as the rewrite target.

```bash
grep -rEn "from (utils|api|migration|legacy)\.|from scripts\.(spider|ingestion|_spider_legacy)" tests --include='*.py' | grep -v __pycache__ | tee /tmp/phase3-test-imports.txt
wc -l /tmp/phase3-test-imports.txt
```

Expected: ~200 occurrences (the count Phase 1 saw was 202). The exact hits will drive Task 1's sed.

> **Plan A note:** Plan A added ~10 new test files (`tests/integration/test_{capabilities,test_mode_reset,onboarding,system_state,sessions,openapi_response_shapes}_endpoints.py`, `tests/integration/conftest.py`, `tests/unit/test_{system_state_repo,rollback_core_library}.py`). Their imports were rewritten by IMP-006's bulk sed; the count above should still land near ~200 because those files use canonical `javdb.*` / `apps.api.*` paths already. If you see a much higher count (~220+) the extras are Plan A files the IMP-006 sweep missed — fold them into Task 1's sed batch.

- [ ] **Step 4: Capture pre-deletion state of dirs we are about to delete**

```bash
echo "=== Directories present (should still exist) ==="
for d in utils api migration legacy scripts/spider scripts/ingestion; do
  [ -e "$d" ] && echo "EXISTS: $d" || echo "MISSING: $d"
done
echo "=== Root files (should still exist) ==="
for f in compat.py pipeline.py; do
  [ -e "$f" ] && echo "EXISTS: $f" || echo "MISSING: $f"
done
```

If any line says `MISSING:`, the manifest is out of date — surface the discrepancy to the user before proceeding. Otherwise continue.

---

## Task 1: Rewrite all test legacy imports to canonical `javdb.*` / `apps.api.*` paths

**Files:**
- Modify (~50 test files; ~200 import statements). Specific files per `/tmp/phase3-test-imports.txt`. The principal ones:
  - `tests/unit/test_parser.py`
  - `tests/unit/test_history_manager.py`
  - `tests/unit/test_git_helper.py`
  - `tests/unit/test_request_handler.py`
  - `tests/unit/test_rclone_helper.py`
  - `tests/unit/test_config_generator.py`
  - `tests/unit/test_api_parsers.py`
  - `tests/unit/test_video_code_search.py`
  - `tests/unit/test_db.py`
  - `tests/unit/test_login.py`
  - `tests/unit/test_engine.py`
  - `tests/unit/test_dedup_checker.py`
  - `tests/smoke/test_spider_backends.py`
  - `tests/smoke/test_spider_detail_runner.py`

- [ ] **Step 1: Build a sed file with the same rewrites used in Phase 1 (Task 11) plus `api.*` → `apps.api.*`**

```bash
cat > /tmp/phase3-test-rewrite.sed <<'EOF'
# utils.* → javdb.*
s|from utils\.parser |from javdb.spider.parser |g
s|from utils\.history_manager |from javdb.storage.history_manager |g
s|from utils\.rclone_helper |from javdb.integrations.rclone.helper |g
s|from utils\.config_generator |from javdb.infra.config_generator |g
s|from utils\.spider_gateway |from javdb.spider.spider_gateway |g
s|from utils\.proxy_ban_manager |from javdb.proxy.ban_manager |g
s|from utils\.sqlite_datetime |from javdb.storage.sqlite_datetime |g
s|from utils\.infra\.git_helper |from javdb.infra.git_helper |g
s|from utils\.infra\.request_handler |from javdb.infra.request |g
s|from utils\.infra\.config_helper |from javdb.infra.config |g
s|from utils\.infra\.logging_config |from javdb.infra.logging |g
s|from utils\.infra\.path_helper |from javdb.infra.paths |g
s|from utils\.infra\.csv_writer |from javdb.infra.csv_writer |g
s|from utils\.infra\.proxy_pool |from javdb.proxy.pool |g
s|from utils\.infra\.db |from javdb.storage.db.db |g
s|from utils\.infra\.db_layer |from javdb.storage.repos |g
s|from utils\.domain\.contracts |from javdb.spider.contracts |g
s|from utils\.domain\.url_helper |from javdb.spider.url_helper |g
s|from utils\.domain\.magnet_extractor |from javdb.spider.magnet_extractor |g
s|from utils\.domain\.filename_helper |from javdb.spider.filename_helper |g
s|from utils\.domain\.masking |from javdb.infra.masking |g
s|from utils\.bridges\.rust_adapters |from javdb.spider |g

# api.* → apps.api.*
s|from api\.parsers |from apps.api.parsers |g
s|from api\.models |from apps.api.models |g
s|from api\.server |from apps.api.server |g
s|from api\.schemas |from apps.api.schemas |g
s|from api\.routers |from apps.api.routers |g
s|from api\.services |from apps.api.services |g
s|from api\.infra |from apps.api.infra |g

# migration.* → javdb.migrations.*
s|from migration\.tools |from javdb.migrations.tools |g
s|from migration\.migrate_to_current |from javdb.migrations.migrate_to_current |g

# scripts.spider.* → javdb.spider.*
s|from scripts\.spider\.app |from javdb.spider.app |g
s|from scripts\.spider\.runtime |from javdb.spider.runtime |g
s|from scripts\.spider\.fetch |from javdb.spider.fetch |g
s|from scripts\.spider\.detail |from javdb.spider.detail |g
s|from scripts\.spider\.services |from javdb.spider.services |g
s|from scripts\.spider\.compat |from javdb.spider.compat |g

# scripts.ingestion.* → javdb.pipeline.*
s|from scripts\.ingestion |from javdb.pipeline |g

# legacy.* (sole file)
s|from legacy\._spider_legacy |from javdb.spider |g
EOF
```

- [ ] **Step 2: Apply sed to all test files**

```bash
find tests -name "*.py" -not -path "*__pycache__*" | xargs sed -i.bak -f /tmp/phase3-test-rewrite.sed
find tests -name "*.bak" -delete
```

- [ ] **Step 3: Handle any leftover hits that the sed didn't cover**

```bash
grep -rEn "from (utils|api|migration|legacy)\.|from scripts\.(spider|ingestion|_spider_legacy)" tests --include='*.py' | grep -v __pycache__
```

Expected: empty. If any line remains, it's an edge case (rare suffix, dynamic import, comment). Hand-fix each.

- [ ] **Step 4: Run pytest to ensure all rewritten tests still pass**

```bash
pytest tests/ --tb=short 2>&1 | tail -10
```

Expected: all green. If something fails, the most likely cause is a function-name divergence — e.g., `from utils.infra.request_handler import X` was rewritten to `from javdb.infra.request import X`, but the function `X` was renamed during the move. Open the new module to see what it exports, then either rename the test's import or update the new module's `__all__` / re-exports.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(refactor): rewrite all legacy-path test imports to javdb.* / apps.api.* (Phase 3, ADR-007)"
```

---

## Task 2: Rewrite `javdb/migrations/migrate_to_current.py` self-references

**Files:**
- Modify: `javdb/migrations/migrate_to_current.py` lines 166–168

The file imports its own `tools/` subpackage via the now-defunct `migration.tools.*` shim:

```python
# javdb/migrations/migrate_to_current.py:166–168 (current)
from migration.tools.migrate_v6_to_v7_split import _normalize_three_dbs
from migration.tools.align_inventory_with_moviehistory import run_alignment
from migration.tools.migrate_v7_to_v8 import (...)
```

- [ ] **Step 1: Locate and rewrite**

```bash
sed -i.bak 's|from migration\.tools\.|from javdb.migrations.tools.|g' javdb/migrations/migrate_to_current.py
rm -f javdb/migrations/migrate_to_current.py.bak
```

- [ ] **Step 2: Verify**

```bash
grep -nE "from migration\.|from migration\b" javdb/migrations/migrate_to_current.py
```

Expected: empty.

```bash
python3 -c "from javdb.migrations.migrate_to_current import main; print('migrate_to_current import OK')"
```

Expected: `migrate_to_current import OK`.

- [ ] **Step 3: Commit**

```bash
git add javdb/migrations/migrate_to_current.py
git commit -m "refactor(migrations): fix migrate_to_current.py self-references to javdb.migrations.tools.* (Phase 3, ADR-007)"
```

---

## Task 3: Delete the legacy top-level directories

**Files:**
- Delete: `utils/` (recursive)
- Delete: `api/` (recursive)
- Delete: `migration/` (recursive)
- Delete: `legacy/` (recursive)
- Delete: `scripts/spider/` (recursive)
- Delete: `scripts/ingestion/` (recursive)
- Delete: `scripts/_spider_legacy.py` (if not already removed in Phase 2)

> Task 1 already rewrote all test imports. Task 2 fixed `migrate_to_current.py`. Phase 1's redirected compat shells are no longer needed.

- [ ] **Step 1: Final paranoia grep before deletion**

```bash
echo "=== Any code reference left? ==="
grep -rE "from (utils|api|migration|legacy)\." . --include='*.py' | grep -v __pycache__ | grep -v "docs/design/architecture/audit-report-"
grep -rE "from scripts\.(spider|ingestion|_spider_legacy)" . --include='*.py' | grep -v __pycache__
```

Expected: empty. If anything prints, fix before deleting.

- [ ] **Step 2: Delete in one git rm batch**

```bash
git rm -r utils api migration legacy scripts/spider scripts/ingestion 2>/dev/null
git rm -f scripts/_spider_legacy.py 2>/dev/null
```

- [ ] **Step 3: Verify directories gone**

```bash
ls utils api migration legacy scripts/spider scripts/ingestion 2>&1 | grep -c "No such"
```

Expected: `6`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: delete compat directories utils/, api/, migration/, legacy/, scripts/{spider,ingestion}/ (Phase 3, ADR-007)"
```

---

## Task 4: Delete root `compat.py` and `pipeline.py`

**Files:**
- Delete: `compat.py` (root)
- Delete: `pipeline.py` (root)

- [ ] **Step 1: Confirm no remaining references**

```bash
grep -rEn "^import compat\b|from compat |^import pipeline\b|from pipeline " . --include='*.py' | grep -v __pycache__ | grep -v 'apps/cli/pipeline\.py' | grep -v 'javdb/'
```

Expected: empty. (Hits inside `javdb/pipeline/` or `apps/cli/pipeline.py` are real-package references, not the root `pipeline.py` shell.)

- [ ] **Step 2: Delete**

```bash
git rm compat.py pipeline.py
```

- [ ] **Step 3: Verify import fails**

```bash
python3 -c "import compat" 2>&1 | grep -q "ModuleNotFoundError" && echo "OK: compat unavailable" || echo "FAIL"
python3 -c "import pipeline" 2>&1 | grep -q "ModuleNotFoundError" && echo "OK: pipeline unavailable" || echo "FAIL"
```

Expected: both `OK:` lines.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: delete root compat.py and pipeline.py shells (Phase 3, ADR-007)"
```

---

## Task 5: Clean the Dockerfile COPY directives

**Files:**
- Modify: `docker/Dockerfile` (delete lines 54–57: `COPY api/`, `COPY legacy/`, `COPY migration/`, `COPY utils/`)
- Modify: `docker/Dockerfile.api` (delete lines 45–48: same four COPY directives)

> Phase 1 already updated line 20 / line 14 (the `COPY packages/rust/javdb_rust_core/` → `COPY javdb/rust_core/`) — those lines stay.

- [ ] **Step 1: Read current state**

```bash
sed -n '50,60p' docker/Dockerfile
sed -n '40,50p' docker/Dockerfile.api
```

Confirm lines 54–57 of `Dockerfile` and 45–48 of `Dockerfile.api` are the four legacy COPY directives.

- [ ] **Step 2: Delete the four lines from each file**

```bash
sed -i.bak -e '/^COPY api\/ \.\/api\//d' -e '/^COPY legacy\/ \.\/legacy\//d' -e '/^COPY migration\/ \.\/migration\//d' -e '/^COPY utils\/ \.\/utils\//d' docker/Dockerfile docker/Dockerfile.api
rm -f docker/Dockerfile.bak docker/Dockerfile.api.bak
```

- [ ] **Step 3: Verify no COPY referring to legacy dirs remains**

```bash
grep -nE "^COPY (api|legacy|migration|utils)/" docker/Dockerfile docker/Dockerfile.api
```

Expected: empty.

- [ ] **Step 4: Run a dry docker build to ensure nothing else breaks**

```bash
docker build -f docker/Dockerfile -t javdb-test:dry --no-cache . 2>&1 | tail -10
docker build -f docker/Dockerfile.api -t javdb-api-test:dry --no-cache . 2>&1 | tail -10
```

Expected: both builds succeed (or fail on a network step, but the COPY step itself must not fail). If COPY fails because `javdb/` is missing, your local checkout is incomplete; sync and retry. Save the output to confirm at PR time.

- [ ] **Step 5: Plan A API smoke — confirm the `Dockerfile.api` image still serves traffic**

`Dockerfile.api` is consumed by the external `javdb-autospider-web` repo via GHCR (`publish-api-image.yml`). A broken COPY here propagates to that repo's docker-compose, so prove the runtime imports work end-to-end before merge:

```bash
docker run --rm -d --name imp008-smoke -p 18108:8100 \
  -e STORAGE_BACKEND=sqlite -e API_SECRET_KEY=imp008-test \
  javdb-api-test:dry
sleep 5
curl -sf http://127.0.0.1:18108/api/health && echo
docker rm -f imp008-smoke
```

Expected: health JSON prints. A `ModuleNotFoundError` on startup means a Plan A runtime import path was broken by your COPY cleanup — investigate the named module before merging.

- [ ] **Step 6: Commit**

```bash
git add docker/Dockerfile docker/Dockerfile.api
git commit -m "build(docker): remove COPY directives for deleted legacy dirs (Phase 3, ADR-007)"
```

---

## Task 6: Update docs and root README references

**Files (search-and-replace for `packages.python.javdb_*` and legacy paths):**
- `README.md`, `README_CN.md`
- `CLAUDE.md`, `CONTEXT.md`
- `docs/handbook/en/**/*.md` (and the paired `docs/handbook/zh/**/*.md`)
- Skip: `docs/design/architecture/audit-report-*.md` (historical record; preserve as-is)
- Skip: ADR files in `docs/design/adr/` (they document the OLD paths in their decision history; the "Amendments" in ADR-005/006 already explain the migration)

**Replacement rules** (apply to docs files):

| Old | New |
|---|---|
| `packages/python/javdb_platform/db.py` | `javdb/storage/db/db.py` |
| `packages/python/javdb_platform/db_session.py` | `javdb/storage/db/db_session.py` |
| `packages/python/javdb_platform/db_layer/` | `javdb/storage/repos/` |
| `packages/python/javdb_platform/` | `javdb/storage/` *(catch-all; verify per occurrence)* |
| `packages/python/javdb_spider/` | `javdb/spider/` |
| `packages/python/javdb_ingestion/` | `javdb/pipeline/` |
| `packages/python/javdb_core/` | `javdb/spider/` |
| `packages/python/javdb_integrations/` | `javdb/integrations/` |
| `packages/python/javdb_migrations/` | `javdb/migrations/` |
| `packages/rust/javdb_rust_core/` | `javdb/rust_core/` |
| `python3 -m scripts.audit_archive` | `python3 -m apps.cli.db.audit_archive` |
| `python3 -m scripts.aggregate_pending_health` | `python3 -m apps.cli.db.pending_health` |
| `python3 -m scripts.pending_mode_alert_and_pause` | `python3 -m apps.cli.db.pending_alert` |
| `from utils.X` (in code snippets) | `from javdb.X` per the same map as Phase 1 |

- [ ] **Step 1: Apply sed-rewrites to documentation**

```bash
cat > /tmp/phase3-doc-rewrite.sed <<'EOF'
# Catch-all package paths (the most specific first)
s|packages/python/javdb_platform/db\.py|javdb/storage/db/db.py|g
s|packages/python/javdb_platform/db_session\.py|javdb/storage/db/db_session.py|g
s|packages/python/javdb_platform/db_reports\.py|javdb/storage/db/db_reports.py|g
s|packages/python/javdb_platform/db_history_write\.py|javdb/storage/db/db_history_write.py|g
s|packages/python/javdb_platform/db_history_read\.py|javdb/storage/db/db_history_read.py|g
s|packages/python/javdb_platform/db_layer/history_repo\.py|javdb/storage/repos/history_repo.py|g
s|packages/python/javdb_platform/db_layer/operations_repo\.py|javdb/storage/repos/operations_repo.py|g
s|packages/python/javdb_platform/db_layer/sessions_repo\.py|javdb/storage/repos/sessions_repo.py|g
s|packages/python/javdb_platform/db_layer/system_state_repo\.py|javdb/storage/repos/system_state_repo.py|g
s|packages/python/javdb_platform/db_layer/|javdb/storage/repos/|g
s|packages/python/javdb_platform/history_manager\.py|javdb/storage/history_manager.py|g
s|packages/python/javdb_platform/proxy_pool\.py|javdb/proxy/pool.py|g
s|packages/python/javdb_platform/proxy_ban_manager\.py|javdb/proxy/ban_manager.py|g
s|packages/python/javdb_platform/proxy_coordinator_client\.py|javdb/proxy/coordinator/proxy_coordinator_client.py|g
s|packages/python/javdb_platform/runner_registry_client\.py|javdb/proxy/coordinator/runner_registry_client.py|g
s|packages/python/javdb_platform/config_helper\.py|javdb/infra/config.py|g
s|packages/python/javdb_platform/logging_config\.py|javdb/infra/logging.py|g
s|packages/python/javdb_platform/path_helper\.py|javdb/infra/paths.py|g
s|packages/python/javdb_platform/request_handler\.py|javdb/infra/request.py|g
s|packages/python/javdb_spider/runtime/config\.py|javdb/spider/runtime/config.py|g
s|packages/python/javdb_spider/|javdb/spider/|g
s|packages/python/javdb_ingestion/|javdb/pipeline/|g
s|packages/python/javdb_core/masking\.py|javdb/infra/masking.py|g
s|packages/python/javdb_core/|javdb/spider/|g
s|packages/python/javdb_integrations/login\.py|javdb/spider/auth/login.py|g
s|packages/python/javdb_integrations/fetch_page\.py|javdb/infra/fetch_page.py|g
s|packages/python/javdb_integrations/health_check\.py|javdb/infra/health_check.py|g
s|packages/python/javdb_integrations/qb_uploader\.py|javdb/integrations/qb/uploader.py|g
s|packages/python/javdb_integrations/qb_file_filter\.py|javdb/integrations/qb/file_filter.py|g
s|packages/python/javdb_integrations/qb_client\.py|javdb/integrations/qb/client.py|g
s|packages/python/javdb_integrations/pikpak_bridge\.py|javdb/integrations/pikpak/bridge.py|g
s|packages/python/javdb_integrations/rclone_helper\.py|javdb/integrations/rclone/helper.py|g
s|packages/python/javdb_integrations/rclone_manager\.py|javdb/integrations/rclone/manager.py|g
s|packages/python/javdb_integrations/email_notification\.py|javdb/integrations/notify/email.py|g
s|packages/python/javdb_integrations/|javdb/integrations/|g
s|packages/python/javdb_migrations/|javdb/migrations/|g
s|packages/python/javdb_platform/|javdb/storage/|g
s|packages/rust/javdb_rust_core|javdb/rust_core|g

# Python -m forms
s|python3? -m scripts\.audit_archive|python3 -m apps.cli.db.audit_archive|g
s|python3? -m scripts\.aggregate_pending_health|python3 -m apps.cli.db.pending_health|g
s|python3? -m scripts\.pending_mode_alert_and_pause|python3 -m apps.cli.db.pending_alert|g
s|python3? -m scripts\.cleanup_stale_session_audits|python3 -m apps.cli.db.cleanup_stale_session_audits|g
s|python3? -m scripts\.sync_d1_to_sqlite|python3 -m apps.cli.db.sync_d1_to_sqlite|g
s|python scripts/dump_openapi\.py|python -m apps.cli.ops.dump_openapi|g

# Code-snippet imports inside docs (covered by Phase 1's Task 11 mapping)
s|from packages\.python\.javdb_spider|from javdb.spider|g
s|from packages\.python\.javdb_ingestion|from javdb.pipeline|g
s|from packages\.python\.javdb_core\.masking|from javdb.infra.masking|g
s|from packages\.python\.javdb_core|from javdb.spider|g
s|from packages\.python\.javdb_migrations|from javdb.migrations|g
s|from packages\.python\.javdb_integrations|from javdb.integrations|g
s|from packages\.python\.javdb_platform\.db_layer|from javdb.storage.repos|g
s|from packages\.python\.javdb_platform\.db_session|from javdb.storage.db.db_session|g
s|from packages\.python\.javdb_platform\.db_history_write|from javdb.storage.db.db_history_write|g
s|from packages\.python\.javdb_platform\.history_manager|from javdb.storage.history_manager|g
s|from packages\.python\.javdb_platform\.proxy_pool|from javdb.proxy.pool|g
s|from packages\.python\.javdb_platform\.logging_config|from javdb.infra.logging|g
s|from packages\.python\.javdb_platform\.config_helper|from javdb.infra.config|g
s|from packages\.python\.javdb_platform|from javdb.storage|g
s|from utils\.|from javdb.|g
EOF

find docs/handbook/en docs/handbook/zh -name "*.md" -not -path "*/audit-report-*" 2>/dev/null | xargs sed -i.bak -f /tmp/phase3-doc-rewrite.sed
sed -i.bak -f /tmp/phase3-doc-rewrite.sed README.md README_CN.md CLAUDE.md CONTEXT.md
find docs/handbook/en docs/handbook/zh -name "*.bak" -delete 2>/dev/null
rm -f README.md.bak README_CN.md.bak CLAUDE.md.bak CONTEXT.md.bak
```

> **Caution about the `from utils.` → `from javdb.` rewrite:** that catch-all line in the sed file is intentionally aggressive for docs (code snippets in user-facing docs use the same paths as production code). However, the rewrite is *post-hoc* — verify each substitution makes semantic sense in context. The `find` step writes `.bak` files which we delete; if you want to inspect changes first, run the sed without `-i.bak` and `diff` the output.

- [ ] **Step 2: Verify no `packages.python.javdb_*` or `from utils.` remains in user-facing docs**

```bash
grep -rE "packages\.python\.javdb_|from utils\.|packages/python/javdb_|packages/rust/javdb_rust_core" docs/handbook/en docs/handbook/zh README.md README_CN.md CLAUDE.md CONTEXT.md 2>/dev/null | grep -v "audit-report-"
```

Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add docs/handbook/en docs/handbook/zh README.md README_CN.md CLAUDE.md CONTEXT.md
git commit -m "docs: rewrite all legacy-path references to javdb.* and apps.cli.* (Phase 3, ADR-007)"
```

---

## Task 7: Supersede old architecture maps and add the new tree map

**Files:**
- Modify: `docs/design/architecture/python-core-mapping.md` (replace with supersession redirect)
- Modify: `docs/design/architecture/spider-module-reorg.md` (replace with supersession redirect)
- Create: `docs/design/architecture/python-tree-2026-05.md` (the new tree map)

- [ ] **Step 1: Replace `python-core-mapping.md` with a supersession header**

Open the file and replace its entire content with:

```markdown
# Python Core Old-To-New Mapping (SUPERSEDED)

> **SUPERSEDED by [ADR-007](../adr/archive/ADR-007-monorepo-restructure-2026-05.md) on 2026-05-17.**
>
> The mapping recorded here represents the partial reorganisation that landed before ADR-007. The canonical layout is now described in [`python-tree-2026-05.md`](../architecture/python-tree-2026-05.md). ADR-007 also generated [`ADR-007-deletion-manifest.md`](../adr/archive/ADR-007-deletion-manifest.md) which enumerates everything that has since been removed.
>
> This document is retained for historical context only. Do NOT use the path mappings here for new work.
```

- [ ] **Step 2: Replace `spider-module-reorg.md` similarly**

Replace its entire content with:

```markdown
# Spider Module Reorganization Status (SUPERSEDED)

> **SUPERSEDED by [ADR-007](../adr/archive/ADR-007-monorepo-restructure-2026-05.md) on 2026-05-17.**
>
> The spider reorg described here was the first wave (spider package internals only). ADR-007 completed the project-wide restructure into the top-level `javdb/` namespace. The current spider layout is recorded in [`python-tree-2026-05.md`](../architecture/python-tree-2026-05.md).
>
> This document is retained for historical context only.
```

- [ ] **Step 3: Create `python-tree-2026-05.md`**

Use the Write tool to create the file. Content:

```markdown
# Python Tree (post-2026-05 restructure)

This is the current canonical layout, established by [ADR-007](../adr/archive/ADR-007-monorepo-restructure-2026-05.md).

## Top-level

```
JAVDB_AutoSpider_CICD/
├── apps/
│   ├── api/                  # FastAPI service
│   ├── cli/                  # All Python CLI entries
│   ├── web/                  # FE (being replaced; see javdb-autospider-web)
│   └── desktop/              # Electron shell (MVP)
├── javdb/                    # Python namespace (PEP 420; no __init__.py)
│   ├── spider/
│   ├── pipeline/
│   ├── storage/
│   ├── proxy/
│   ├── integrations/
│   ├── infra/
│   ├── migrations/
│   └── rust_core/            # Rust crate; installed as javdb.rust_core
├── docker/, docs/, tests/, scripts/
└── config.py, pytest.ini, requirements.txt, README*, CLAUDE.md, CONTEXT.md
```

## Per-package quick reference

(Each package has its own README; this is a one-line summary.)

- `javdb/spider/` — scraping runtime + parser/contracts/url/filename/magnet + auth/login
- `javdb/pipeline/` — orchestration (was `ingestion`) + pipeline service
- `javdb/storage/` — DB layer + sessions + rollback + d1 + dual + history_manager
- `javdb/proxy/` — pool + ban_manager + policy + recommend/ + coordinator/ (Worker DO clients)
- `javdb/integrations/` — qb/, pikpak/, rclone/, notify/
- `javdb/infra/` — cross-cutting: config, logging, paths, csv_writer, git, request, masking, fetch_page, health_check
- `javdb/migrations/` — SQL + Python migrate tools
- `javdb/rust_core/` — Rust crate source (PyO3 + maturin)

## CLI quick reference (apps/cli/)

Core: `spider`, `pipeline`, `login`.

Subdirectories: `db/`, `qb/`, `pikpak/`, `rclone/`, `notify/`, `ops/`. Invoke via `python -m apps.cli.<subdir>.<name>`.

## Replaces

- `docs/design/architecture/python-core-mapping.md` — Old partial-reorg mapping (SUPERSEDED)
- `docs/design/architecture/spider-module-reorg.md` — Old spider-only reorg status (SUPERSEDED)

## See also

- ADR-007 — decision and three-phase rollout
- ADR-007 deletion manifest — what was removed in Phase 3
```

- [ ] **Step 4: Commit**

```bash
git add docs/design/architecture/python-core-mapping.md docs/design/architecture/spider-module-reorg.md docs/design/architecture/python-tree-2026-05.md
git commit -m "docs(architecture): supersede python-core-mapping + spider-module-reorg with python-tree-2026-05 (Phase 3, ADR-007)"
```

---

## Task 8: Update `scripts/ci/wiki_mapping.json` and dry-run wiki sync

**Files:**
- Modify (if needed): `scripts/ci/wiki_mapping.json`
- Read-only: `scripts/ci/sync_docs_to_wiki.py` (used for dry run; do not edit unless it breaks)

- [ ] **Step 1: Inspect the current mapping**

```bash
cat scripts/ci/wiki_mapping.json
```

Look for any entries that reference legacy paths (`packages/python/javdb_*`, `scripts/<name>`, `utils/`, etc.). If present, update them to the canonical `javdb.*` or `apps/cli/*` paths.

- [ ] **Step 2: Update entries (if any)**

Open `scripts/ci/wiki_mapping.json` and edit by hand. The file is JSON, so use the Edit tool with surgical `old_string` / `new_string` replacements per entry.

- [ ] **Step 3: Dry-run wiki sync**

```bash
mkdir -p /tmp/wiki-test
python3 scripts/ci/sync_docs_to_wiki.py --wiki-dir /tmp/wiki-test --repo-root . 2>&1 | tail -20
```

Expected: completes without error. If it errors on a missing path, that means a doc still references the old structure; trace back, fix, and re-run.

- [ ] **Step 4: Commit (if any changes)**

```bash
git add scripts/ci/wiki_mapping.json 2>/dev/null
git diff --cached --quiet || git commit -m "ci(wiki): update wiki_mapping.json for post-ADR-007 paths (Phase 3, ADR-007)"
```

> The actual wiki regeneration happens after the PR is merged, via the `sync-docs-to-wiki.yml` workflow on push to main. Do not push manually to the wiki repo.

---

## Task 9: Tick every checkbox in the deletion manifest

**Files:**
- Modify: `docs/design/adr/ADR-007-deletion-manifest.md` (change every `- [ ]` to `- [x]` at the end of Phase 3)

- [ ] **Step 1: Replace all checkboxes**

```bash
sed -i.bak 's|^- \[ \]|- [x]|g; s|^  - \[ \]|  - [x]|g' docs/design/adr/ADR-007-deletion-manifest.md
rm -f docs/design/adr/ADR-007-deletion-manifest.md.bak
```

- [ ] **Step 2: Verify**

```bash
grep -c "^- \[ \]" docs/design/adr/ADR-007-deletion-manifest.md
grep -c "^- \[x\]" docs/design/adr/ADR-007-deletion-manifest.md
```

Expected: first line is `0`, second line is > 0 (count of items now ticked).

- [ ] **Step 3: Add a "Completed" footer**

Open `docs/design/adr/ADR-007-deletion-manifest.md` and append at the end:

```markdown

---

**Completed:** YYYY-MM-DD (Phase 3 PR #NNN)
```

Replace `YYYY-MM-DD` with today's date and `#NNN` with the Phase 3 PR number (fill after PR is opened).

- [ ] **Step 4: Commit**

```bash
git add docs/design/adr/ADR-007-deletion-manifest.md
git commit -m "docs(adr): tick all ADR-007 deletion manifest checkboxes (Phase 3, ADR-007)"
```

---

## Verification Gate (Phase 3)

All of the following must pass before opening the Phase 3 PR.

- [ ] **Gate 1: Full pytest passes**

```bash
pytest tests/ --tb=short 2>&1 | tail -3
```

Expected: ends with `XXX passed` and exit code 0. Compare against `/tmp/phase3-baseline.txt` — counts must be ≥ baseline.

- [ ] **Gate 2: Every `apps/cli/**/*.py --help` succeeds**

```bash
find apps/cli -name "*.py" -not -name "__init__.py" -not -name "_*.py" | while read f; do
  mod=$(echo "$f" | sed 's|^apps/cli/||; s|\.py$||; s|/|.|g')
  python3 -m apps.cli.$mod --help >/dev/null 2>&1 && echo "OK: apps.cli.$mod" || echo "FAIL: apps.cli.$mod"
done
```

Expected: every line `OK:`.

- [ ] **Gate 3: No legacy code-imports anywhere (except historical audit reports)**

```bash
grep -rE "from (utils|api|migration|legacy)\." . --include='*.py' | grep -v __pycache__ | grep -v "docs/design/architecture/audit-report-"
```

Expected: empty.

- [ ] **Gate 4: No legacy scripts imports**

```bash
grep -rE "from scripts\.(spider|ingestion|audit_archive|aggregate_pending_health|pending_mode_alert_and_pause|cleanup_stale_session_audits|sync_d1_to_sqlite|dump_openapi|_spider_legacy)" . --include='*.py' | grep -v __pycache__
```

Expected: empty.

- [ ] **Gate 5: No `packages.python.javdb_*` references in code**

```bash
grep -rE "from packages\.python\.javdb_|import packages\.python\.javdb_" . --include='*.py' | grep -v __pycache__
```

Expected: empty.

- [ ] **Gate 6: All legacy top-level directories are gone**

```bash
ls utils/ api/ migration/ legacy/ scripts/spider/ scripts/ingestion/ 2>&1 | grep -c "No such"
```

Expected: `6`.

- [ ] **Gate 7: Root shells are gone**

```bash
python3 -c "import compat" 2>&1 | grep -q "ModuleNotFoundError" && echo "OK: compat gone"
python3 -c "import pipeline" 2>&1 | grep -q "ModuleNotFoundError" && echo "OK: pipeline gone"
```

Expected: both `OK:` lines print.

- [ ] **Gate 8: Docker build dry-run succeeds**

```bash
docker build -f docker/Dockerfile -t javdb-test:dry --no-cache . 2>&1 | tail -5
```

Expected: build succeeds. If a transient network error occurs during a `RUN apt-get` step, retry; the test specifically verifies that no COPY directive fails on a missing source.

- [ ] **Gate 9: No legacy paths in user-facing docs**

```bash
grep -rE "packages\.python\.javdb_|packages/python/javdb_|packages/rust/javdb_rust_core" docs/handbook/en docs/handbook/zh README.md README_CN.md CLAUDE.md CONTEXT.md 2>/dev/null | grep -v "audit-report-"
```

Expected: empty.

- [ ] **Gate 10: Wiki sync dry-run succeeds**

```bash
rm -rf /tmp/wiki-test
mkdir /tmp/wiki-test
python3 scripts/ci/sync_docs_to_wiki.py --wiki-dir /tmp/wiki-test --repo-root . 2>&1 | tail -5
```

Expected: completes without error.

- [ ] **Gate 11: Deletion manifest has zero unchecked items**

```bash
grep -c "^- \[ \]" docs/design/adr/ADR-007-deletion-manifest.md
```

Expected: `0`.

- [ ] **Gate 12: String-form module paths (dynamic-loader edge cases)**

```bash
grep -rEn '"utils\.|"scripts\.(spider|ingestion)\.' . --include='*.py' | grep -v __pycache__
```

Expected: empty. Any hit is a dynamic-import string that the static rewrites in Phase 1 missed.

- [ ] **Gate 13: Plan A integration canary — OpenAPI dump + endpoints + test suite**

The `apps.cli.ops.dump_openapi` module is the smallest end-to-end probe of the API import tree — if any module Plan A's routers depend on was broken by Phase 3 deletions, this script fails at import time and the message names the offending module. (Note: this script was at `scripts/dump_openapi.py` when Plan A merged; IMP-007 moved it to `apps/cli/ops/dump_openapi.py` and fixed the latent `parents[1]` output-path bug. Use the new entry point.)

```bash
STORAGE_BACKEND=sqlite API_SECRET_KEY=dump-only-secret python3 -m apps.cli.ops.dump_openapi
```

Expected: writes `docs/api/openapi.json` (~85 KB, ~41 paths) without `ModuleNotFoundError`.

Confirm all 10 Plan A endpoints survive:

```bash
python3 -c "
import json
s = json.load(open('docs/api/openapi.json'))
need = ['/api/capabilities','/api/onboarding/status','/api/onboarding/test','/api/onboarding/complete','/api/onboarding/dismiss-hint','/api/system/state','/api/sessions','/api/sessions/{session_id}','/api/sessions/{session_id}/rollback','/api/sessions/{session_id}/commit']
missing = [p for p in need if p not in s['paths']]
print('missing:', missing if missing else 'none')
"
```

Expected: `missing: none`.

Run the Plan A test surface explicitly (subset of Gate 1, but isolates breakage to Plan A files):

```bash
STORAGE_BACKEND=sqlite API_SECRET_KEY=test-secret-key pytest \
  tests/unit/test_system_state_repo.py \
  tests/unit/test_rollback_core_library.py \
  tests/integration/test_capabilities_endpoint.py \
  tests/integration/test_test_mode_reset.py \
  tests/integration/test_onboarding_endpoints.py \
  tests/integration/test_system_state_endpoints.py \
  tests/integration/test_sessions_endpoints.py \
  tests/integration/test_openapi_response_shapes.py \
  -v
```

Expected: all green. Any failure isolates to a Plan A import that this phase's sweep broke.

If any gate fails, fix and re-run all gates.

---

## Final commit & PR

- [ ] **Step 1: Squash-ready commits review**

```bash
git log refactor/phase3-delete-compat --oneline ^main
```

Expected: about 9–10 commits, one per Task.

- [ ] **Step 2: Push branch**

```bash
git push -u origin refactor/phase3-delete-compat
```

- [ ] **Step 3: Open PR**

PR title: `refactor: Phase 3 — delete compat shells, finalise restructure per deletion manifest (ADR-007)`

PR body MUST include:
- Link to ADR-007
- Link to the (now fully-ticked) deletion manifest `docs/design/adr/ADR-007-deletion-manifest.md`
- Captured output of all 12 verification Gates above
- Statement: "Closes ADR-007. After this PR, `python-core-mapping.md` and `spider-module-reorg.md` are formally superseded; the new map is `python-tree-2026-05.md`."

- [ ] **Step 4: After PR merges, observe the wiki-sync workflow**

The `.github/workflows/sync-docs-to-wiki.yml` workflow fires on push to `main`. Watch it complete successfully. If it errors, file an immediate follow-up to repair `wiki_mapping.json` or `sync_docs_to_wiki.py`.

- [ ] **Step 5: Final sanity sweep on `main` (post-merge)**

```bash
git checkout main && git pull
pytest tests/ --tb=short 2>&1 | tail -3
ls utils/ api/ migration/ legacy/ 2>&1 | grep -c "No such"
python3 -c "import javdb.spider, javdb.pipeline, javdb.storage, javdb.proxy, javdb.integrations, javdb.infra, javdb.migrations, javdb.rust_core; print('post-merge OK')"
```

Expected: all green, `4` (4 deleted dirs), `post-merge OK`.

ADR-007 is now complete. Coordinate with ADR-005 and ADR-006 owners (per the amendments added to those ADRs) so their remaining PRs operate on the new `javdb/*` paths.

---

## Phase 3 Lessons Learned (post-execution)

These gaps surfaced during the Phase 3 execution and were fixed in follow-up commits. Recording them so the next sed-driven monorepo refactor avoids the same pitfalls.

### 1. Non-Python assets get silently swept by directory deletion

Task 3's `git rm -r migration/` deleted **13 D1 SQL migration files** (`migration/d1/*.sql`), **2 shell scripts** (`migration/tools/migrate_to_d1.sh`, `migration/tools/verify_d1.sh`), and **1 incident report** (`migration/d1/2026_05_08_sessionid_decouple.md`) — none of which had a Phase-1-defined home in the new `javdb/` tree. They had to be restored from `f3fa7a1e~1` into `javdb/migrations/{d1,tools}/`. **Lesson:** before deleting any legacy directory, run `git ls-files <dir> | grep -vE '\.py$'` and confirm every non-Python asset has a target in the new layout.

### 2. sed coverage is narrower than it looks

The Task 1 sed file only matched `from X import Y` (with a trailing space). It missed three forms found in the working tree:

- **Bare `import X.Y as Z`** (21 files in tests/). The original sed had no rule for `^import`-prefixed statements.
- **String-form patch targets** like `@patch('utils.X.Y')` or `monkeypatch.setitem(sys.modules, 'utils.X', …)` (23 files). Required a separate perl pass that handles both `"..."` and `'...'` quoting.
- **`from utils.infra import db` → `import javdb.storage.db.db as db`**: the original tries `from javdb.infra import db`, but `db` is not a submodule of `javdb.infra` — the canonical path is `javdb.storage.db.db`. Different *surface* (a submodule-rooted form), not just a different prefix.

**Lesson:** dry-run the sed against the actual diff before committing — `git diff --name-only base...HEAD` then count expected vs. matched occurrences per pattern.

### 3. Word boundaries (sed BRE doesn't support `\b`)

Patterns like `s|from scripts\.spider\.fetch |...|` require *literal* `fetch ` with trailing space. This silently fails on `from scripts.spider.fetch.backend import X` — the sub-path doesn't have a space after `fetch`. Switch to `perl -i -pe` with `\b` word boundaries to catch arbitrary sub-paths. Validate with `grep -rE "from scripts\." tests/` returning zero before considering Task 1 done.

### 4. `scripts/` had 5 more compat shells than IMP-008 anticipated

The deletion-manifest "scripts/ shell files to delete" list named 9 files but Phase 2 deleted only 4 of them. The remaining 5 (`scripts/{qb_uploader,qb_file_filter,rclone_manager,email_notification,_spider_legacy}.py`) survived because tests still imported through them. Phase 3 must (a) rewrite those test imports to canonical `apps.cli.<subdir>.X` paths, then (b) delete the shells. Doing them in the other order leaves dangling imports.

### 5. `apps/cli/<subdir>/*.py` + `apps/api/{server,services/context}.py` still depended on `compat.py`

IMP-008 Task 4 only enumerated `compat.py` + `pipeline.py` for deletion, but **10 production files** (`apps/cli/{notify,pikpak,rclone,ops,qb}/*.py` plus `apps/api/server.py` + `apps/api/services/context.py`) all imported `from compat import alias_module` or `from compat import activate_repo_root`. They blocked Task 4. The fix: convert each to inline the logic — `_module = importlib.import_module(...); sys.modules[__name__] = _module` and `os.chdir(REPO_ROOT); sys.path.insert(0, str(REPO_ROOT))` respectively. **Lesson:** Task 4's paranoia grep needs to be a *prerequisite*, not a verification — run it before Task 1 even starts, and either delete-or-convert every hit as a Task 0 sub-step.

### 6. Tests asserting deleted state remain compiled and break Gate 1

Two tests existed solely to verify pre-Phase-3 state:

- `tests/unit/test_docker_legacy_copy.py` — asserted `COPY legacy/ ./legacy/` IS in the Dockerfile (Task 5 removed it).
- `tests/unit/test_legacy_spider_wrapper.py` — exercised `scripts/_spider_legacy.py` + `compat.alias_module` (both deleted).

Both need explicit deletion in Phase 3. **Lesson:** any test whose assertion would *flip sign* under Phase 3 must be deleted in the same task that produces the flip.

### 7. `scripts/ci/select_tests.py::PYTHON_SOURCE_ROOTS` was missing `"javdb"`

IMP-006 moved all the canonical packages into `javdb/` but didn't update this constant. Result: every `javdb/**/*.py` change was treated as "non-Python source" by the CI test selector, bypassing both the `SOURCE_CHANGE_LIMIT` guard and the new docstring-only filter. **Lesson:** any new top-level package added during a restructure must be added to `PYTHON_SOURCE_ROOTS`. Better: define the roots as "any directory at depth-1 that contains `*.py`" instead of a hard-coded tuple.

### Post-merge follow-ups (not Phase 3 scope)

These items are noted by Phase 3 but deferred:

- **Rollback library layering inversion** (Plan A `§12.3`): `javdb/storage/rollback/core.py:157` still does `from apps.cli.db import rollback as _rollback_cli`. Option B (extract pipeline into library + rewrite ~10 monkeypatch tests) is the long-term fix; tackle during FE Phase 2.
- **`IMPACT_RULES` in `scripts/ci/select_tests.py`**: still references many deleted legacy paths. Rules don't match anything so no harm, but should be cleaned up for clarity.
- **Test pollution of `reports/`**: multiple unit/integration tests write directly to `reports/operations.db` and `reports/D1/d1_drift.jsonl` instead of `tmp_path`. Each run dirties the working tree. File a separate `chore(tests)` task to relocate the writes.
- **ADR-005 / ADR-006 progress notes**: both ADRs were frozen during ADR-007. Their amendments already map old paths to new; once Phase 3 lands, add a one-line "as of `<sha>`, paths are final" note to each.
