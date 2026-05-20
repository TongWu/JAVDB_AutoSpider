# IMP-007: ADR-007 Phase 2 — Migrate `scripts/` Real Code to `apps/cli/`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relocate every real CLI script under `scripts/` into `apps/cli/<subdir>/` with responsibility-based grouping and redundant-prefix removal; relocate the existing `apps/cli/*.py` files into the same subdirectory layout; update every workflow YAML that calls `python -m scripts.X` to use the new `apps.cli.X` paths; delete `scripts/`'s compat sub-packages (`scripts/spider/`, `scripts/ingestion/`) and the shell `scripts/<name>.py` files now superseded by `apps/cli/`.

**Architecture:** `apps/cli/` becomes the single home for every user-facing Python entry point. Three core entries (`spider.py`, `pipeline.py`, `login.py`) stay at the top of `apps/cli/`; everything else moves into one of six subdirectories: `db/`, `qb/`, `pikpak/`, `rclone/`, `notify/`, `ops/`. `scripts/` is retained only for `ci/*` (impact-test selector, wiki sync) and shell scripts (`verify_*.sh`). Internal `apps/cli/_session_helpers.py` moves with the DB CLIs that use it.

**Tech Stack:** Python 3.11+ entry points (each file remains an `argparse`-driven module callable via `python -m apps.cli.<subdir>.<name>`), GitHub Actions YAML, pytest, ripgrep for verification.

**Source spec:** [ADR-007](../adr/archive/ADR-007-monorepo-restructure-2026-05.md) §"Phase 2 — Migrate scripts/ to apps/cli/" (English version is canonical).

**Prerequisite:** Phase 1 PR is merged. `javdb/` namespace exists and every internal import inside `apps/cli/` already uses `from javdb.*`.

---

## Pre-flight: branch, baseline snapshot

### Task 0: Branch + baseline

**Files:**
- No file changes; only branch setup and reconnaissance.

- [ ] **Step 1: Create the working branch from updated main**

```bash
git checkout main
git pull origin main   # must include the merged Phase 1 PR
git checkout -b refactor/phase2-scripts-to-cli
```

- [ ] **Step 2: Snapshot baseline pytest**

```bash
pytest tests/ -x --tb=no -q 2>&1 | tail -3 | tee /tmp/phase2-baseline.txt
```

Expected: all tests pass.

- [ ] **Step 3: Enumerate the scripts that move and the shells that delete**

```bash
echo "=== Real scripts to move (workflow-invoked or library functions) ==="
ls scripts/audit_archive.py scripts/aggregate_pending_health.py scripts/pending_mode_alert_and_pause.py scripts/cleanup_stale_session_audits.py scripts/sync_d1_to_sqlite.py scripts/dump_openapi.py scripts/check_bake_metrics.py scripts/profile_hot_paths.py scripts/rclone_cleanup_empty_dirs.py scripts/rclone_flatten_by_size.py scripts/rclone_group_jav.py scripts/rclone_rename_jav.py scripts/rclone_update_nfo_titles.py 2>/dev/null
echo "=== Shells to delete (canonical lives under apps/cli/) ==="
ls scripts/_spider_legacy.py scripts/login.py scripts/pikpak_bridge.py scripts/qb_uploader.py scripts/qb_file_filter.py scripts/rclone_manager.py scripts/email_notification.py scripts/health_check.py scripts/fetch_page.py 2>/dev/null
echo "=== Compat subpackages to delete ==="
ls -d scripts/spider scripts/ingestion 2>/dev/null
echo "=== Existing apps/cli/ files (top level) ==="
ls apps/cli/*.py
```

The output is the ground truth for Tasks 1–4 below.

- [ ] **Step 4: Snapshot workflow YAML calls that need rewriting**

```bash
grep -rEn "python3? -m scripts\.|python scripts/" .github/workflows/ 2>/dev/null | grep -v 'scripts/ci/' | tee /tmp/phase2-workflow-baseline.txt
```

Expected: 6 hits — `DailyIngestion.yml` (2 lines), `AdHocIngestion.yml` (2 lines), `AuditArchive.yml` (1 line), `publish-openapi.yml` (1 line).

---

## Task 1: Create the `apps/cli/` subdirectory skeleton

**Files:**
- Create: `apps/cli/db/`, `apps/cli/qb/`, `apps/cli/pikpak/`, `apps/cli/rclone/`, `apps/cli/notify/`, `apps/cli/ops/`
- Create: `__init__.py` in each new subdirectory

- [ ] **Step 1: Make subdirs and init markers**

```bash
mkdir -p apps/cli/db apps/cli/qb apps/cli/pikpak apps/cli/rclone apps/cli/notify apps/cli/ops
touch apps/cli/db/__init__.py
touch apps/cli/qb/__init__.py
touch apps/cli/pikpak/__init__.py
touch apps/cli/rclone/__init__.py
touch apps/cli/notify/__init__.py
touch apps/cli/ops/__init__.py
```

- [ ] **Step 2: Verify**

```bash
ls apps/cli/ | sort
```

Expected: includes `db/`, `qb/`, `pikpak/`, `rclone/`, `notify/`, `ops/` plus existing top-level files.

- [ ] **Step 3: Commit**

```bash
git add apps/cli/db apps/cli/qb apps/cli/pikpak apps/cli/rclone apps/cli/notify apps/cli/ops
git commit -m "feat(cli): create apps/cli/ subdirectory skeleton (Phase 2, ADR-007)"
```

---

## Task 2: Move `scripts/` real code into `apps/cli/<subdir>/` (with prefix drops)

**Files:**
- Move (database/session ops to `apps/cli/db/`):
  - `scripts/audit_archive.py` → `apps/cli/db/audit_archive.py`
  - `scripts/aggregate_pending_health.py` → `apps/cli/db/pending_health.py` *(drop `aggregate_` and `_mode` redundancy)*
  - `scripts/pending_mode_alert_and_pause.py` → `apps/cli/db/pending_alert.py` *(drop `_mode` and shorten)*
  - `scripts/cleanup_stale_session_audits.py` → `apps/cli/db/cleanup_stale_session_audits.py` *(no prefix to drop)*
  - `scripts/sync_d1_to_sqlite.py` → `apps/cli/db/sync_d1_to_sqlite.py`
- Move (rclone maintenance to `apps/cli/rclone/`):
  - `scripts/rclone_cleanup_empty_dirs.py` → `apps/cli/rclone/cleanup_empty_dirs.py`
  - `scripts/rclone_flatten_by_size.py` → `apps/cli/rclone/flatten_by_size.py`
  - `scripts/rclone_group_jav.py` → `apps/cli/rclone/group_jav.py`
  - `scripts/rclone_rename_jav.py` → `apps/cli/rclone/rename_jav.py`
  - `scripts/rclone_update_nfo_titles.py` → `apps/cli/rclone/update_nfo_titles.py`
- Move (diagnostics & dev tools to `apps/cli/ops/`):
  - `scripts/dump_openapi.py` → `apps/cli/ops/dump_openapi.py`
  - `scripts/check_bake_metrics.py` → `apps/cli/ops/check_bake_metrics.py`
  - `scripts/profile_hot_paths.py` → `apps/cli/ops/profile_hot_paths.py`

- [ ] **Step 1: Move DB ops (5 files)**

```bash
git mv scripts/audit_archive.py             apps/cli/db/audit_archive.py
git mv scripts/aggregate_pending_health.py  apps/cli/db/pending_health.py
git mv scripts/pending_mode_alert_and_pause.py apps/cli/db/pending_alert.py
git mv scripts/cleanup_stale_session_audits.py apps/cli/db/cleanup_stale_session_audits.py
git mv scripts/sync_d1_to_sqlite.py         apps/cli/db/sync_d1_to_sqlite.py
```

- [ ] **Step 2: Move rclone maintenance (5 files)**

```bash
git mv scripts/rclone_cleanup_empty_dirs.py apps/cli/rclone/cleanup_empty_dirs.py
git mv scripts/rclone_flatten_by_size.py    apps/cli/rclone/flatten_by_size.py
git mv scripts/rclone_group_jav.py          apps/cli/rclone/group_jav.py
git mv scripts/rclone_rename_jav.py         apps/cli/rclone/rename_jav.py
git mv scripts/rclone_update_nfo_titles.py  apps/cli/rclone/update_nfo_titles.py
```

- [ ] **Step 3: Move ops/diagnostics (3 files)**

```bash
git mv scripts/dump_openapi.py        apps/cli/ops/dump_openapi.py
git mv scripts/check_bake_metrics.py  apps/cli/ops/check_bake_metrics.py
git mv scripts/profile_hot_paths.py   apps/cli/ops/profile_hot_paths.py
```

- [ ] **Step 4: Verify moves**

```bash
echo "=== apps/cli/db/ ==="
ls apps/cli/db/
echo "=== apps/cli/rclone/ ==="
ls apps/cli/rclone/
echo "=== apps/cli/ops/ ==="
ls apps/cli/ops/
echo "=== scripts/ residue ==="
ls scripts/*.py 2>/dev/null
```

Expected: `db/` has 6 (5 moved + `__init__.py`); `rclone/` has 6; `ops/` has 4; `scripts/*.py` only the shells (login, pikpak_bridge, qb_uploader, qb_file_filter, rclone_manager, email_notification, health_check, fetch_page, _spider_legacy) plus `__init__.py`.

- [ ] **Step 5: Smoke-test each moved CLI parses and shows --help**

```bash
for mod in apps.cli.db.audit_archive apps.cli.db.pending_health apps.cli.db.pending_alert apps.cli.db.cleanup_stale_session_audits apps.cli.db.sync_d1_to_sqlite apps.cli.rclone.cleanup_empty_dirs apps.cli.rclone.flatten_by_size apps.cli.rclone.group_jav apps.cli.rclone.rename_jav apps.cli.rclone.update_nfo_titles apps.cli.ops.dump_openapi apps.cli.ops.check_bake_metrics apps.cli.ops.profile_hot_paths; do
  python3 -m $mod --help >/dev/null 2>&1 && echo "OK: $mod" || echo "FAIL: $mod"
done
```

Expected: every line `OK:`. If any `FAIL:` appears, the file likely has a residual `if __name__ == "__main__"` block that relies on the old script location; fix by replacing any `sys.path` hacks or relative file-path constants with the new module location.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(cli): move scripts/ real code into apps/cli/{db,rclone,ops}/ with prefix drops (Phase 2, ADR-007)"
```

---

## Task 3: Move existing `apps/cli/*.py` files into responsibility subdirectories

**Files:**
- Move to `apps/cli/db/`:
  - `apps/cli/rollback.py` → `apps/cli/db/rollback.py`
  - `apps/cli/migration.py` → `apps/cli/db/migration.py`
  - `apps/cli/commit_session.py` → `apps/cli/db/commit_session.py`
  - `apps/cli/cleanup_stale_in_progress.py` → `apps/cli/db/cleanup_stale_in_progress.py`
  - `apps/cli/sweep_movie_claim_stages.py` → `apps/cli/db/sweep_claim_stages.py` *(drop `movie_` redundancy with `_stages`)*
  - `apps/cli/_session_helpers.py` → `apps/cli/db/_session_helpers.py`
- Move to `apps/cli/qb/`:
  - `apps/cli/qb_uploader.py` → `apps/cli/qb/uploader.py`
  - `apps/cli/qb_file_filter.py` → `apps/cli/qb/file_filter.py`
- Move to `apps/cli/pikpak/`:
  - `apps/cli/pikpak_bridge.py` → `apps/cli/pikpak/bridge.py`
- Move to `apps/cli/rclone/`:
  - `apps/cli/rclone_manager.py` → `apps/cli/rclone/manager.py`
- Move to `apps/cli/notify/`:
  - `apps/cli/email_notification.py` → `apps/cli/notify/email.py`
- Move to `apps/cli/ops/`:
  - `apps/cli/fetch_page.py` → `apps/cli/ops/fetch_page.py`
  - `apps/cli/health_check.py` → `apps/cli/ops/health_check.py`
  - `apps/cli/config_generator.py` → `apps/cli/ops/config_generator.py`

> Note: `apps/cli/spider.py`, `apps/cli/pipeline.py`, `apps/cli/login.py` stay at the top of `apps/cli/`. They are the three core user-facing entries.

- [ ] **Step 1: Move DB ops (6 files)**

```bash
git mv apps/cli/rollback.py                 apps/cli/db/rollback.py
git mv apps/cli/migration.py                apps/cli/db/migration.py
git mv apps/cli/commit_session.py           apps/cli/db/commit_session.py
git mv apps/cli/cleanup_stale_in_progress.py apps/cli/db/cleanup_stale_in_progress.py
git mv apps/cli/sweep_movie_claim_stages.py apps/cli/db/sweep_claim_stages.py
git mv apps/cli/_session_helpers.py         apps/cli/db/_session_helpers.py
```

- [ ] **Step 2: Move per-service entries**

```bash
git mv apps/cli/qb_uploader.py           apps/cli/qb/uploader.py
git mv apps/cli/qb_file_filter.py        apps/cli/qb/file_filter.py
git mv apps/cli/pikpak_bridge.py         apps/cli/pikpak/bridge.py
git mv apps/cli/rclone_manager.py        apps/cli/rclone/manager.py
git mv apps/cli/email_notification.py    apps/cli/notify/email.py
```

- [ ] **Step 3: Move ops entries**

```bash
git mv apps/cli/fetch_page.py        apps/cli/ops/fetch_page.py
git mv apps/cli/health_check.py      apps/cli/ops/health_check.py
git mv apps/cli/config_generator.py  apps/cli/ops/config_generator.py
```

- [ ] **Step 4: Update internal `apps.cli.*` imports inside the moved files**

After moving, files that previously imported `from apps.cli._session_helpers import X` need to import `from apps.cli.db._session_helpers import X`. Same for any other cross-CLI imports.

```bash
grep -rEn "from apps\.cli\._session_helpers" apps/cli/ --include='*.py'
```

If any hits, rewrite:

```bash
grep -rl "from apps.cli._session_helpers" apps/cli/ | xargs sed -i.bak 's|from apps\.cli\._session_helpers|from apps.cli.db._session_helpers|g'
find apps/cli -name "*.bak" -delete
```

Also check for any `apps.cli.<oldname>` references that need updating (e.g., if `commit_session.py` was imported by another CLI):

```bash
grep -rEn "from apps\.cli\.(rollback|migration|commit_session|cleanup_stale_in_progress|sweep_movie_claim_stages|qb_uploader|qb_file_filter|pikpak_bridge|rclone_manager|email_notification|fetch_page|health_check|config_generator)" apps tests --include='*.py' | grep -v __pycache__
```

For each hit, rewrite to the new subdir path (e.g., `from apps.cli.rollback` → `from apps.cli.db.rollback`). Build a sed file analogous to Phase 1's `/tmp/phase1-rewrite.sed`:

```bash
cat > /tmp/phase2-cli-rewrite.sed <<'EOF'
s|from apps\.cli\.rollback|from apps.cli.db.rollback|g
s|from apps\.cli\.migration|from apps.cli.db.migration|g
s|from apps\.cli\.commit_session|from apps.cli.db.commit_session|g
s|from apps\.cli\.cleanup_stale_in_progress|from apps.cli.db.cleanup_stale_in_progress|g
s|from apps\.cli\.sweep_movie_claim_stages|from apps.cli.db.sweep_claim_stages|g
s|from apps\.cli\._session_helpers|from apps.cli.db._session_helpers|g
s|from apps\.cli\.qb_uploader|from apps.cli.qb.uploader|g
s|from apps\.cli\.qb_file_filter|from apps.cli.qb.file_filter|g
s|from apps\.cli\.pikpak_bridge|from apps.cli.pikpak.bridge|g
s|from apps\.cli\.rclone_manager|from apps.cli.rclone.manager|g
s|from apps\.cli\.email_notification|from apps.cli.notify.email|g
s|from apps\.cli\.fetch_page|from apps.cli.ops.fetch_page|g
s|from apps\.cli\.health_check|from apps.cli.ops.health_check|g
s|from apps\.cli\.config_generator|from apps.cli.ops.config_generator|g
EOF

find apps tests -name "*.py" -not -path "*__pycache__*" | xargs sed -i.bak -f /tmp/phase2-cli-rewrite.sed
find apps tests -name "*.bak" -delete
```

- [ ] **Step 5: Verify no stale `apps.cli.<flat>` imports remain**

```bash
grep -rEn "from apps\.cli\.(rollback|migration|commit_session|cleanup_stale_in_progress|sweep_movie_claim_stages|_session_helpers|qb_uploader|qb_file_filter|pikpak_bridge|rclone_manager|email_notification|fetch_page|health_check|config_generator)" apps tests --include='*.py' | grep -v __pycache__
```

Expected: empty.

- [ ] **Step 6: Smoke-test each moved CLI**

```bash
for mod in apps.cli.db.rollback apps.cli.db.migration apps.cli.db.commit_session apps.cli.db.cleanup_stale_in_progress apps.cli.db.sweep_claim_stages apps.cli.qb.uploader apps.cli.qb.file_filter apps.cli.pikpak.bridge apps.cli.rclone.manager apps.cli.notify.email apps.cli.ops.fetch_page apps.cli.ops.health_check apps.cli.ops.config_generator; do
  python3 -m $mod --help >/dev/null 2>&1 && echo "OK: $mod" || echo "FAIL: $mod"
done
```

Expected: every line `OK:`.

- [ ] **Step 7: Smoke-test core entries still work**

```bash
for mod in apps.cli.spider apps.cli.pipeline apps.cli.login; do
  python3 -m $mod --help >/dev/null 2>&1 && echo "OK: $mod" || echo "FAIL: $mod"
done
```

Expected: every line `OK:`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(cli): reorganise existing apps/cli/ files into db/qb/pikpak/rclone/notify/ops subdirs (Phase 2, ADR-007)"
```

---

## Task 4: Delete `scripts/` compat sub-packages and shell files

**Files:**
- Delete: `scripts/spider/` (entire subdirectory)
- Delete: `scripts/ingestion/` (entire subdirectory)
- Delete: `scripts/_spider_legacy.py`
- Delete: `scripts/login.py`, `scripts/pikpak_bridge.py`, `scripts/qb_uploader.py`, `scripts/qb_file_filter.py`, `scripts/rclone_manager.py`, `scripts/email_notification.py`, `scripts/health_check.py`, `scripts/fetch_page.py` (compat shells now superseded by `apps/cli/<subdir>/<name>.py`)

> These deletions are temporarily safe because (a) Phase 1 redirected the shells to point at `javdb.*` paths, and (b) all in-repo callers either already use `apps.cli.*` paths or import from `javdb.*` (the shells are no longer referenced by any non-test code). Tests still importing through these shells will be addressed in Phase 3 — but tests do NOT import from `scripts/<name>.py` shells, only from `scripts/spider/*` and `scripts/ingestion/*`, which we delete here. Verify before deleting:

- [ ] **Step 1: Confirm no test in the repo imports the shells we are about to delete**

```bash
grep -rEn "from scripts\.spider|from scripts\.ingestion|from scripts\._spider_legacy" tests --include='*.py' | grep -v __pycache__
```

If non-empty, those tests will break. Surface the list to the user before continuing — they were assumed in Phase 3 to still be there. The deletion manifest currently lists them under "test imports requiring rewrite"; if any such import exists, we MUST defer the deletion of that specific compat package to Phase 3.

**Decision rule:**
- If grep returns hits only for `scripts.spider` and `scripts.ingestion` (sub-packages), defer those two deletions to Phase 3. Keep `scripts/spider/` and `scripts/ingestion/` alive in Phase 2; delete only the standalone shell files (`login.py` etc.).
- If grep returns hits for `scripts._spider_legacy`, defer that deletion too.

- [ ] **Step 2: Delete the safe shell files (not currently imported by tests)**

```bash
git rm scripts/login.py scripts/pikpak_bridge.py scripts/qb_uploader.py scripts/qb_file_filter.py scripts/rclone_manager.py scripts/email_notification.py scripts/health_check.py scripts/fetch_page.py
```

- [ ] **Step 3: Delete `scripts/spider/` and `scripts/ingestion/` IF Step 1 confirmed no test imports survive**

If Step 1 found `from scripts.spider.*` or `from scripts.ingestion.*` test imports (which Phase 1 left in place since shells are alive), defer this step. Tests will be rewritten in Phase 3, after which the deletion will be safe.

If Step 1 was clean (or after Phase 3 rewrite), run:

```bash
git rm -r scripts/spider scripts/ingestion
```

**For Phase 2 specifically, do NOT delete `scripts/spider/` and `scripts/ingestion/` if tests still import through them.** Phase 3 will delete them once it rewrites the test imports.

- [ ] **Step 4: Delete `scripts/_spider_legacy.py` if not referenced**

```bash
grep -rEn "from scripts\._spider_legacy|import scripts\._spider_legacy" . --include='*.py' | grep -v __pycache__
```

If empty, delete:

```bash
git rm scripts/_spider_legacy.py
```

- [ ] **Step 5: Verify scripts/ residue**

```bash
ls scripts/
```

Expected: `__init__.py`, `ci/`, `verify_proxy_coordinator_deploy.sh`, plus possibly `spider/`, `ingestion/`, `_spider_legacy.py` if deferred to Phase 3.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(scripts): delete compat shells now superseded by apps/cli/ (Phase 2, ADR-007)"
```

---

## Task 5: Update workflow YAML files

Every workflow that invokes `python3 -m scripts.<X>` (other than `scripts/ci/*`) must be updated to the new `apps.cli.<subdir>.<name>` invocation. Same for `python scripts/<X>.py` forms.

**Files to update:**
- `.github/workflows/DailyIngestion.yml` (2 calls)
- `.github/workflows/AdHocIngestion.yml` (2 calls)
- `.github/workflows/AuditArchive.yml` (1 call)
- `.github/workflows/publish-openapi.yml` (1 call)

**Replacement map:**

| Old | New |
|---|---|
| `python3 -m scripts.aggregate_pending_health` | `python3 -m apps.cli.db.pending_health` |
| `python3 -m scripts.pending_mode_alert_and_pause` | `python3 -m apps.cli.db.pending_alert` |
| `python3 -m scripts.audit_archive` | `python3 -m apps.cli.db.audit_archive` |
| `python scripts/dump_openapi.py` | `python -m apps.cli.ops.dump_openapi` |

- [ ] **Step 1: Read current contents to confirm exact line forms**

```bash
grep -nE "python3? -m scripts\.|python scripts/" .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml .github/workflows/AuditArchive.yml .github/workflows/publish-openapi.yml
```

- [ ] **Step 2: Apply the rewrites**

```bash
for f in .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml .github/workflows/AuditArchive.yml .github/workflows/publish-openapi.yml; do
  sed -i.bak \
    -e 's|python3 -m scripts\.aggregate_pending_health|python3 -m apps.cli.db.pending_health|g' \
    -e 's|python3 -m scripts\.pending_mode_alert_and_pause|python3 -m apps.cli.db.pending_alert|g' \
    -e 's|python3 -m scripts\.audit_archive|python3 -m apps.cli.db.audit_archive|g' \
    -e 's|python scripts/dump_openapi\.py|python -m apps.cli.ops.dump_openapi|g' \
    "$f"
done
find .github/workflows -name "*.bak" -delete
```

- [ ] **Step 3: Verify no legacy script invocation remains in workflows (excluding scripts/ci/)**

```bash
grep -rE "python3? -m scripts\.|python scripts/" .github/workflows/ | grep -v 'scripts/ci/'
```

Expected: empty.

- [ ] **Step 4: YAML lint (optional but recommended)**

```bash
# If yamllint is installed:
yamllint .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml .github/workflows/AuditArchive.yml .github/workflows/publish-openapi.yml 2>&1 | head -20
# Otherwise, use a basic Python YAML parse:
python3 -c "
import yaml, sys
for f in ['.github/workflows/DailyIngestion.yml', '.github/workflows/AdHocIngestion.yml', '.github/workflows/AuditArchive.yml', '.github/workflows/publish-openapi.yml']:
    try: yaml.safe_load(open(f))
    except Exception as e: print(f'{f}: {e}'); sys.exit(1)
print('all workflows parse OK')
"
```

Expected: `all workflows parse OK`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/
git commit -m "ci(workflows): update python -m scripts.X invocations to apps.cli.X (Phase 2, ADR-007)"
```

---

## Task 6: Write `apps/cli/README.md` and per-subdir READMEs (aggregate task)

Each README follows the same structure as the Phase 1 javdb READMEs (header sentence + Files table + Subdirectories list + Depends-on). Locations:

- `apps/cli/README.md` — top-level CLI subcommand map; lists the three core entries and points to each subdir README
- `apps/cli/db/README.md`
- `apps/cli/qb/README.md`
- `apps/cli/pikpak/README.md`
- `apps/cli/rclone/README.md`
- `apps/cli/notify/README.md`
- `apps/cli/ops/README.md`
- `scripts/README.md` — clarifies that this directory now only hosts CI tools and shell scripts; user-facing entries live in `apps/cli/`

- [ ] **Step 1: List files per subdir**

```bash
for d in apps/cli/db apps/cli/qb apps/cli/pikpak apps/cli/rclone apps/cli/notify apps/cli/ops; do
  echo "=== $d ==="
  ls $d/*.py 2>/dev/null
done
```

Use this listing to populate each README's Files table. Pull descriptions from each file's `argparse` `description=` arg or top docstring.

- [ ] **Step 2: Write `apps/cli/README.md`**

Use the Write tool. Content:

```markdown
# apps/cli

Every Python CLI entry point in this repository. Each module is callable via `python -m apps.cli.<name>` (top-level) or `python -m apps.cli.<subdir>.<name>` (responsibility subdir).

## Top-level (core) entries

| Module | Purpose |
|---|---|
| `spider` | Run the JavDB spider (daily or ad-hoc URL mode). |
| `pipeline` | Run the full ingestion pipeline (spider → filter → upload → notify). |
| `login` | Refresh the JavDB session cookie. |

## Subdirectories (by responsibility)

| Subdir | What lives here | README |
|---|---|---|
| `db/` | Database/session operations: migration, rollback, audit archive, pending-mode health, session cleanup, D1 sync | `db/README.md` |
| `qb/` | qBittorrent integration: uploader, file filter | `qb/README.md` |
| `pikpak/` | PikPak bridge for old torrents | `pikpak/README.md` |
| `rclone/` | Rclone library maintenance: manager + cleanup/flatten/group/rename/nfo tools | `rclone/README.md` |
| `notify/` | Email notifications | `notify/README.md` |
| `ops/` | Diagnostics & dev tools: health check, page fetcher, bake metrics, profiler, OpenAPI dumper, config generator | `ops/README.md` |

All entries are workflow-callable: `.github/workflows/*.yml` reference them via `python3 -m apps.cli.<...>`.
```

- [ ] **Step 3: Write each subdir README**

For each subdir, list the `.py` files and write a 1–2 line description per file. Example for `apps/cli/db/README.md`:

```markdown
# apps/cli/db

Database and session operational CLIs. All commands operate on the SQLite/D1 storage backend (per `STORAGE_BACKEND` env).

## Files

| File | Purpose |
|---|---|
| `migration.py` | Run database migrations (forward-only). |
| `rollback.py` | Roll back a session by `SessionId` or by `(run_id, run_attempt)`. |
| `audit_archive.py` | Archive old audit rows to JSON files; prune from DB. |
| `commit_session.py` | Manually commit a finalising session (recovery tool). |
| `cleanup_stale_in_progress.py` | Sweep sessions stuck in `in_progress` for > N hours. |
| `cleanup_stale_session_audits.py` | Delete audit rows tied to committed sessions (orphan cleanup). |
| `sweep_claim_stages.py` | Cron-driven cleanup of movie claim stage records. |
| `pending_health.py` | Aggregate Pending-Mode write metrics across recent sessions; emit alert payload. |
| `pending_alert.py` | When `pending_health` flags a critical alert, send the alert email and write `pipeline_paused_until` to `.publish-config.yml`. |
| `sync_d1_to_sqlite.py` | Reconciliation: copy D1 rows into local SQLite for offline analysis. |
| `_session_helpers.py` | Internal helper shared by the rollback / commit-session entries. |

## Workflow invocations

- `DailyIngestion.yml` and `AdHocIngestion.yml` call `pending_health` and `pending_alert`.
- `AuditArchive.yml` calls `audit_archive`.
```

Apply the same template to `qb/`, `pikpak/`, `rclone/`, `notify/`, `ops/`.

- [ ] **Step 4: Write `scripts/README.md`**

```markdown
# scripts

This directory hosts ONLY:

1. `ci/` — internal CI Python tools (`select_tests.py`, `sync_docs_to_wiki.py`, `wiki_mapping.json`). These are not user-facing and are not exposed as `apps/cli/` entries.
2. `verify_*.sh` and other shell scripts used by deployment/verification flows.

User-facing Python CLI entries live in **[apps/cli/](../apps/cli/)**. If you are looking for `spider`, `pipeline`, `qb_uploader`, `audit_archive`, `rclone_*`, or any other command-line tool, see `apps/cli/`.

This separation was established by [ADR-007](../docs/design/adr/archive/ADR-007-monorepo-restructure-2026-05.md) (Phase 2).
```

- [ ] **Step 5: Commit**

```bash
git add apps/cli/README.md apps/cli/**/README.md scripts/README.md
git commit -m "docs(refactor): add README per apps/cli/ subdir + scripts/ explainer (Phase 2, ADR-007)"
```

---

## Verification Gate (Phase 2)

All of the following must pass before opening the Phase 2 PR.

- [ ] **Gate 1: Full pytest passes**

```bash
pytest tests/ --tb=short 2>&1 | tail -3
```

Expected: ends with `XXX passed` and exit code 0. Counts ≥ baseline from `/tmp/phase2-baseline.txt`.

- [ ] **Gate 2: Every `apps/cli/**/*.py --help` succeeds**

```bash
find apps/cli -name "*.py" -not -name "__init__.py" -not -name "_*.py" | while read f; do
  mod=$(echo "$f" | sed 's|^apps/cli/||; s|\.py$||; s|/|.|g')
  python3 -m apps.cli.$mod --help >/dev/null 2>&1 && echo "OK: apps.cli.$mod" || echo "FAIL: apps.cli.$mod"
done
```

Expected: every line starts with `OK:`. No `FAIL:`.

- [ ] **Gate 3: Workflows reference no legacy script paths**

```bash
grep -rE "python3? -m scripts\.|python scripts/" .github/workflows/ | grep -v 'scripts/ci/'
```

Expected: empty.

- [ ] **Gate 4: Workflows parse as valid YAML**

```bash
python3 -c "
import yaml, glob
errs = []
for f in glob.glob('.github/workflows/*.yml'):
    try: yaml.safe_load(open(f))
    except Exception as e: errs.append((f, str(e)))
print(f'{len(errs)} YAML errors')
for f, e in errs: print(f, e)
"
```

Expected: `0 YAML errors`.

- [ ] **Gate 5: `scripts/` contains only `ci/` + shell + (optionally) deferred compat subpackages**

```bash
ls scripts/
```

Expected: `__init__.py`, `README.md`, `ci/`, `verify_proxy_coordinator_deploy.sh`, and (if deferred to Phase 3) `spider/`, `ingestion/`, `_spider_legacy.py`. No other `.py` files.

- [ ] **Gate 6: `apps/cli/` flat layout sanity**

```bash
ls apps/cli/*.py
ls apps/cli/db/*.py
```

Expected: top level has 3 + `__init__.py` (spider.py, pipeline.py, login.py). `db/` has at least the 11 db files moved by Tasks 2+3.

- [ ] **Gate 7: All in-repo `apps.cli.<name>` imports resolve**

```bash
grep -rEn "from apps\.cli\." apps tests --include='*.py' | grep -v __pycache__ | head -20
python3 -c "
import importlib, re, subprocess
out = subprocess.check_output(['grep', '-rEn', 'from apps\\.cli\\.', 'apps', 'tests', '--include=*.py']).decode()
mods = set()
for line in out.splitlines():
    if '__pycache__' in line: continue
    m = re.search(r'from (apps\\.cli\\.[\\w.]+) import', line)
    if m: mods.add(m.group(1))
errs = 0
for m in sorted(mods):
    try: importlib.import_module(m)
    except Exception as e: print(f'FAIL: {m}: {e}'); errs += 1
print(f'{errs} import failures')
"
```

Expected: `0 import failures`.

If any gate fails, fix and re-run all gates.

---

## Final commit & PR

- [ ] **Step 1: Squash-ready commits review**

```bash
git log refactor/phase2-scripts-to-cli --oneline ^main
```

Expected: about 6–8 commits, one per Task.

- [ ] **Step 2: Push branch**

```bash
git push -u origin refactor/phase2-scripts-to-cli
```

- [ ] **Step 3: Open PR**

PR title: `refactor: Phase 2 — migrate scripts/ to apps/cli/ + update workflows (ADR-007)`

PR body MUST include:
- Link to ADR-007
- Output of Gate 2 (every `apps.cli.**/--help` `OK:`)
- Output of Gate 3 (empty)
- Output of Gate 4 (`0 YAML errors`)
- Output of Gate 7 (`0 import failures`)
- Statement: "Phase 3 (delete compat shells) depends on this PR."

- [ ] **Step 4: After PR merges, proceed to Phase 3 plan**

See `docs/design/impl/IMP-008-restructure-phase3-delete-compat.md`.
