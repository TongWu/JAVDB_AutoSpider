# ADR-007: Monorepo Restructure — Top-level `javdb/` Namespace and Phase Roll-out

**Status**: Completed 2026-05-17 — Phase 1+2+3 all retired the legacy paths (`api/`, `migration/`, `legacy/`, `packages/`, `compat.py`, `pipeline.py`, the flat `apps/cli/<name>.py` shims). Top-level `javdb/` namespace and `apps/cli/<subdir>/` layout are now canonical (see CLAUDE.md). Residual `utils/__pycache__/` and `scripts/spider/__pycache__/` directories contain stale bytecode only — no source.
**Date**: 2026-05-17
**Deciders**: Architecture depth-pass round 3 (succeeds the incomplete Python core reorg recorded in [docs/design/architecture/python-core-mapping.md](../../architecture/python-core-mapping.md) and [spider-module-reorg.md](../../architecture/spider-module-reorg.md))
**Supersedes**: `docs/design/architecture/python-core-mapping.md`, `docs/design/architecture/spider-module-reorg.md` (both marked superseded at Phase 3)
**Related Implementation Plans**: [IMP-ADR007-01](../../impl/archive/IMP-ADR007-01-restructure-phase1-javdb-tree.md) (Phase 1 — build `javdb/` tree, completed), [IMP-ADR007-02](../../impl/archive/IMP-ADR007-02-restructure-phase2-scripts-to-cli.md) (Phase 2 — migrate `scripts/` to `apps/cli/`, completed), [IMP-ADR007-03](../../impl/archive/IMP-ADR007-03-restructure-phase3-delete-compat.md) (Phase 3 — execute deletion manifest, completed); see also [ADR-007 deletion manifest](ADR-007-deletion-manifest.md) for the Phase 1 deliverable consumed by Phase 3.

---

## Context

The repository carries the scars of an earlier, **incomplete** reorganisation:

- `packages/python/` and `apps/` hold the canonical code.
- `utils/`, `api/`, `migration/`, `legacy/`, the root `pipeline.py`, the root `compat.py`, plus several files and subpackages under `scripts/` exist purely as compatibility wrappers (`alias_module(__name__, "packages.python...")`). They contribute ~200 lines of pure forwarding shims and add five top-level "directories that look like code but aren't".
- Inside `packages/python/javdb_platform/`, ~40 files sit flat at the top level — `db_*.py × 12`, `proxy_*.py × 5`, `*_client.py × 5`, plus infrastructure helpers (`config_helper`, `logging_config`, `csv_writer`, etc.) and orchestration glue (`pipeline_service`, `history_manager`, `spider_gateway`). Locating the actual entry point of any component requires bisecting through this flat namespace.
- `packages/python/javdb_core/` is misnamed: it contains spider parsing primitives (`parser`, `contracts`, `url_helper`, `filename_helper`, `magnet_extractor`, `masking`), not a higher-order "core" abstraction. The project's actual core IS the spider.
- `scripts/` mixes three categories: real workflow-invoked CLIs (`audit_archive`, `aggregate_pending_health`, `pending_mode_alert_and_pause`), pure compat shims (`login.py`, `pikpak_bridge.py`, etc.), and CI internal tools (`ci/select_tests.py`, `ci/sync_docs_to_wiki.py`). User-facing entries cannot be distinguished from forwarding shells without opening each file.
- `packages/` adds a needless two-layer prefix to every import: `from packages.python.javdb_platform.db_layer.history_repo import HistoryRepo` (six segments).

A grep across `packages/`, `apps/`, and `tests/` shows **464** imports already use canonical paths and **202** still flow through the legacy shells, concentrated in `tests/` and `migrate_to_current.py`. Workflows reference legacy paths in five places (`python3 -m scripts.aggregate_pending_health`, etc.). Docker `COPY` directives reference four legacy top-level dirs but do not execute their code.

The earlier reorg (recorded in `python-core-mapping.md` and `spider-module-reorg.md`) restructured the spider package internals but stopped before removing the shells or touching `javdb_platform/`, `javdb_integrations/`, or the top-level layout. We resume and complete that work.

---

## Decision

Carry out a three-phase restructure that:

1. **Collapses the `packages/python/` prefix** into a single top-level project namespace `javdb/` (PEP 420 namespace package, no `__init__.py`).
2. **Splits `javdb_platform/`** into focused top-level packages under `javdb/`.
3. **Folds `javdb_core/`** into `javdb/spider/` (with `masking.py` to `javdb/infra/`).
4. **Renames `javdb_ingestion/` to `javdb/pipeline/`** to align with workflow and CLI usage.
5. **Moves the Rust crate** source to `javdb/rust_core/` and updates the maturin install name to `javdb.rust_core`.
6. **Migrates `scripts/` real code** to `apps/cli/` with responsibility-based subdirectories.
7. **Deletes all compat shells** and updates every import, test, doc, wiki, and Dockerfile reference.

### Final top-level layout

```
JAVDB_AutoSpider_CICD/
├── apps/
│   ├── api/                  # FastAPI service (unchanged structurally)
│   ├── cli/                  # all Python CLI entry points
│   │   ├── spider.py, pipeline.py, login.py       # core entries (top level)
│   │   ├── db/, qb/, pikpak/, rclone/, notify/, ops/   # responsibility subdirs
│   │   └── README.md
│   ├── web/, desktop/        # untouched (FE rewrite in separate repo)
│   └── reports/, logs/
├── javdb/                    # ★ Python namespace (PEP 420; no __init__.py at this level)
│   ├── spider/               # scraping runtime + parser/contracts/url/filename/magnet + auth/login
│   ├── pipeline/             # orchestration (was ingestion) + pipeline service
│   ├── storage/              # db/, repos/, sessions/, rollback/, d1, dual_connection, history_manager
│   ├── proxy/                # pool, ban_manager, policy, recommend/, coordinator/
│   ├── integrations/         # qb/, pikpak/, rclone/, notify/
│   ├── infra/                # config, logging, paths, csv_writer, git_helper, request, masking, fetch_page, health_check, config_generator
│   ├── migrations/           # SQL + Python migrate tools
│   └── rust_core/            # ★ Rust crate source; installs as `javdb.rust_core`
├── docker/, docs/, tests/
├── scripts/                  # ci/ + verify_*.sh only
├── reports/, logs/, node_modules/
├── config.py, config.py.example, requirements.txt, package.json, pytest.ini
└── README.md, README_CN.md, CLAUDE.md, CONTEXT.md
```

### Deleted top-level entries

`packages/`, `utils/`, `api/`, `migration/`, `legacy/`, root `compat.py`, root `pipeline.py`, the compat subpackages `scripts/spider/` and `scripts/ingestion/`, plus every `scripts/<name>.py` shell whose canonical implementation already lives under `apps/cli/`.

### Naming rules adopted

- **Top-level packages** under `javdb/` use **domain-language** names (`spider`, `pipeline`, `storage`, `proxy`, `integrations`, `infra`, `migrations`). No `javdb_` prefix internally — the namespace covers it.
- **Leaf file names** drop redundant prefixes when the parent directory already names the concept. Examples: `qb_uploader.py` in `apps/cli/qb/` becomes `uploader.py`; `rclone_manager.py` in `apps/cli/rclone/` becomes `manager.py`; `email_notification.py` in `apps/cli/notify/` becomes `email.py`.
- **CLI subdir grouping** by acting-upon: `db/` (database/session ops), `qb/`, `pikpak/`, `rclone/`, `notify/`, `ops/` (diagnostics & dev tools). Three core entries stay at the top of `apps/cli/`: `spider.py`, `pipeline.py`, `login.py`.

### Rust crate

- Source moves from `packages/rust/javdb_rust_core/` to `javdb/rust_core/`.
- `pyproject.toml` adds `[tool.maturin] module-name = "javdb.rust_core"`; `[project] name` may stay `javdb_rust_core` (wheel name) or be renamed.
- All 15+ Python imports `from javdb_rust_core import ...` become `from javdb.rust_core import ...`.
- `javdb/` must remain a PEP 420 namespace package (no `__init__.py` at `javdb/`); the maturin-installed `javdb.rust_core` extension coexists in `site-packages/javdb/` via namespace package merging.
- The source directory `javdb/rust_core/` contains only Rust crate files (no `.py`, no `__init__.py`); the compiled `.so` is found in `site-packages` at import time.

---

## Three-Phase Roll-out

### Phase 1 — Build the `javdb/` tree (largest single PR)

Scope:

1. Create `javdb/` at repo root (no `__init__.py`).
2. Move `packages/python/javdb_spider/` → `javdb/spider/`. Merge in `javdb_core/parser.py`, `contracts.py`, `url_helper.py`, `filename_helper.py`, `magnet_extractor.py`. Add `javdb/spider/auth/login.py` (from `javdb_integrations/login.py`).
3. Rename `packages/python/javdb_ingestion/` → `javdb/pipeline/`. Merge in `javdb_platform/pipeline_service.py` as `javdb/pipeline/service.py`.
4. Explode `packages/python/javdb_platform/` into:
   - `javdb/storage/` — db.py + db_*.py × 9 + d1_client, dual_connection, sqlite_datetime, history_manager, plus existing `db_layer/` (renamed `repos/`), `sessions/`, `rollback/`.
   - `javdb/proxy/` — proxy_ban_manager, proxy_policy, proxy_pool, plus `recommend/` (recommend_proxy_*) and `coordinator/` (do_client_base, proxy_coordinator_client, login_state_client, movie_claim_client, runner_registry_client, work_distributor_client). Coordinator stays under `proxy/` because Worker DO coordination is only activated in proxy-pool mode.
   - `javdb/infra/` — config_helper (→ `config.py`), config_generator, csv_writer, git_helper, logging_config (→ `logging.py`), path_helper (→ `paths.py`), request_handler (→ `request.py`).
   - `javdb/spider/spider_gateway.py` (moved up from `javdb_platform/`).
5. Split `packages/python/javdb_integrations/`:
   - `javdb/integrations/qb/` ← qb_client, qb_file_filter, qb_uploader, plus `qb_config.py` from `javdb_platform/`.
   - `javdb/integrations/pikpak/` ← pikpak_bridge.
   - `javdb/integrations/rclone/` ← rclone_helper, rclone_manager.
   - `javdb/integrations/notify/` ← email_notification.
   - `javdb/spider/auth/` ← login.
   - `javdb/infra/` ← fetch_page, health_check, masking (from `javdb_core/`).
6. Distribute `packages/python/javdb_platform/bridges/rust_adapters/`: csv_adapter → merged into `javdb/infra/csv_writer.py`; request_adapter → merged into `javdb/infra/request.py`; dedup_adapter → merged into `javdb/spider/services/dedup.py`; parser_adapter → merged into `javdb/spider/parser.py`; history_adapter → merged into `javdb/storage/history_manager.py`. The `bridges/` concept disappears.
7. Move Rust crate `packages/rust/javdb_rust_core/` → `javdb/rust_core/`. Update `pyproject.toml` (`module-name = "javdb.rust_core"`). Update all 15+ `from javdb_rust_core import` → `from javdb.rust_core import`.
8. Delete the now-empty `packages/` directory.
9. Update **all** internal imports inside `javdb/` and `apps/` (~600 lines) from `packages.python.javdb_*` → `javdb.*`.
10. **Temporarily** update every legacy compat shell (`utils/*`, `api/*`, `migration/*`, `scripts/spider/*`, `scripts/ingestion/*`) so their `alias_module(__name__, "packages.python...")` target is re-pointed to the new `javdb.*` path. The shells stay alive but forward to the new locations.
11. Update CI workflow path filters and build steps that reference `packages/rust/javdb_rust_core` (`.github/workflows/build-rust-extension.yml`, `.github/actions/install-rust-wheel/action.yml`, `docker/Dockerfile`, `docker/Dockerfile.api`).
12. Write a `README.md` in every new directory (one-line dir purpose at top, then a table listing each `.py` file with a 1–2 line description).
13. **Generate the deletion manifest** at `docs/design/adr/ADR-007-deletion-manifest.md` listing every compat artefact that Phase 3 must remove, with exact file paths and (for tests) exact line numbers.

Verification gate (all must pass):

- `pytest tests/` passes in full (unit + integration + smoke).
- Every existing `apps/cli/*.py --help` succeeds.
- `python3 -c "import javdb.spider, javdb.pipeline, javdb.storage, javdb.proxy, javdb.integrations, javdb.infra, javdb.migrations, javdb.rust_core"` succeeds.
- `maturin develop --release` from `javdb/rust_core/` produces a working wheel.

### Phase 2 — Migrate `scripts/` to `apps/cli/`

Scope:

1. Move real-code scripts into `apps/cli/<subdir>/` with prefix drops:
   - `scripts/audit_archive.py` → `apps/cli/db/audit_archive.py`
   - `scripts/aggregate_pending_health.py` → `apps/cli/db/pending_health.py`
   - `scripts/pending_mode_alert_and_pause.py` → `apps/cli/db/pending_alert.py`
   - `scripts/cleanup_stale_session_audits.py` → `apps/cli/db/cleanup_stale_session_audits.py`
   - `scripts/sync_d1_to_sqlite.py` → `apps/cli/db/sync_d1_to_sqlite.py`
   - `scripts/dump_openapi.py` → `apps/cli/ops/dump_openapi.py`
   - `scripts/rclone_*.py` (5 files) → `apps/cli/rclone/*.py` (drop `rclone_` prefix)
   - `scripts/check_bake_metrics.py` → `apps/cli/ops/check_bake_metrics.py`
   - `scripts/profile_hot_paths.py` → `apps/cli/ops/profile_hot_paths.py`
   - Existing `apps/cli/qb_uploader.py` → `apps/cli/qb/uploader.py` (and analogues for `qb_file_filter`, `pikpak_bridge`, `rclone_manager`, `email_notification`).
   - Existing top-level `apps/cli/{rollback,migration,audit_archive,commit_session,cleanup_stale_in_progress,sweep_movie_claim_stages}.py` → `apps/cli/db/`.
2. Delete `scripts/spider/`, `scripts/ingestion/`, `scripts/_spider_legacy.py`.
3. Delete `scripts/<name>.py` shells now superseded by `apps/cli/`.
4. Update every workflow YAML that calls `python3 -m scripts.X` or `python scripts/X.py` (except `scripts/ci/*`): `DailyIngestion.yml`, `AdHocIngestion.yml`, `AuditArchive.yml`, `publish-openapi.yml`.
5. Write `apps/cli/README.md` (subcommand map) and per-subdir READMEs.
6. Write `scripts/README.md` stating "this directory holds only CI/Python and shell ops scripts; user-facing entries live in `apps/cli/`."

Verification gate:

- `pytest tests/` passes.
- Every `apps/cli/**/*.py --help` succeeds (or `python -m apps.cli.<subdir>.<name> --help`).
- `grep -rE "python3? -m scripts\.|python scripts/" .github/workflows | grep -v 'scripts/ci/'` returns empty.
- Workflow YAML lints clean.

### Phase 3 — Delete compat shells (precision deletion against manifest)

Scope:

1. Rewrite test imports (~202 occurrences) from `from utils.X`, `from scripts.X`, `from api.X`, `from migration.X` to the canonical `from javdb.X` (or `from apps.api.X`) paths. The exact files and line numbers are listed in `ADR-007-deletion-manifest.md`.
2. Rewrite `javdb/migrations/migrate_to_current.py` self-references (`from migration.tools.*` → `from javdb.migrations.tools.*`).
3. Delete directories: `utils/`, `api/`, `migration/`, `legacy/`.
4. Delete root files: `compat.py`, `pipeline.py`.
5. Delete `docker/Dockerfile` lines 54–57 and `docker/Dockerfile.api` lines 45–48 (the `COPY utils/`, `COPY api/`, `COPY legacy/`, `COPY migration/` directives).
6. Update `docs/handbook/en/**/*.md`, `docs/handbook/zh/**/*.md`, `README.md`, `README_CN.md`, `CLAUDE.md`, `CONTEXT.md` — replace every `packages.python.javdb_*` and legacy-path reference with the new `javdb.*` paths.
7. Mark `docs/design/architecture/python-core-mapping.md` and `docs/design/architecture/spider-module-reorg.md` as **superseded** (header note pointing to this ADR); replace their content with a redirect to the new map.
8. Add `docs/design/architecture/python-tree-2026-05.md` (new map after restructure).
9. Trigger `.github/workflows/sync-docs-to-wiki.yml` so the wiki regenerates from the updated `docs/handbook/en/`.
10. Update `scripts/ci/wiki_mapping.json` if any stale paths remain.

Verification gate:

- `pytest tests/` passes.
- All `apps/cli/**/*.py --help` succeed.
- `grep -rE "from (utils|api|migration|legacy)\." . --include='*.py' | grep -v __pycache__` returns empty (excluding `docs/design/architecture/audit-report-*.md` historical records).
- `grep -rE "from scripts\.(spider|ingestion|audit_archive|aggregate_pending_health|pending_mode_alert_and_pause|cleanup_stale_session_audits|sync_d1_to_sqlite|dump_openapi)" .` returns empty.
- `ls utils/ api/ migration/ legacy/ scripts/spider/ scripts/ingestion/ 2>&1 | grep -c "No such"` is 6.
- `python3 -c "import compat"` raises `ModuleNotFoundError`.
- `docker build -f docker/Dockerfile .` (dry build) succeeds.
- Every checkbox in `ADR-007-deletion-manifest.md` is ticked.

---

## Alternatives Considered

### Alternative A — Keep `packages/python/` umbrella; only collapse the inner shells

**Rejected.** The verbose four-segment import prefix is the most common readability complaint in code review and orientation. Keeping `packages/` saves perhaps three lines of pyproject changes but every future import everywhere pays the cost.

### Alternative B — Top-level `src/` (PEP src-layout) with pytest pythonpath shortcut

**Rejected.** Would yield the shortest imports (`from spider import …`), but `spider`, `pipeline`, `storage`, `infra` are generic enough that collision with a PyPI package or stdlib feature is a real risk; tracebacks would also fail to tell a reader "this `spider` is the JavDB one or something else". A project-scoped namespace (`javdb.*`) is worth one extra segment.

### Alternative C — Phase 1 deletes compat shells in the same PR as the move

**Rejected.** A single PR would carry the rename + the test rewrites + the doc updates + the Dockerfile edits in one diff, which is unreviewable. The temporary-compat strategy keeps each phase reviewable in isolation; the deletion manifest prevents the temporary state from leaking past Phase 3.

### Alternative D — Keep `javdb_` prefix on every subpackage (`javdb/javdb_spider/`, etc.)

**Rejected.** The user explicitly called out the redundancy. Once the `javdb/` namespace exists, prefixing every sub-name with `javdb_` is double-naming.

### Alternative E — Split `coordinator/` out as a top-level package

**Rejected.** Worker DO coordination (movie claim, work distributor, runner registry, login state) is only invoked when proxy-pool mode is active. It is a sub-feature of proxy management, not an independent cross-cutting concern. `javdb/proxy/coordinator/` is the right home.

### Alternative F — Deprecation grace period (shells stay alive 1–2 releases with `DeprecationWarning`)

**Rejected.** The user excluded the only known external consumers (sibling `wiki` repo and `proxy-coordinator` repo). No other external callers exist. A grace period extends the time the repository advertises two valid import paths and delays the cleanup goal.

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Hidden circular imports surface after reshuffle (e.g. `javdb/spider/contracts` ↔ `javdb/pipeline` ↔ `javdb/storage`) | High | Phase 1 verification runs full pytest + a CLI smoke that imports every package's top module. Break cycles with `TYPE_CHECKING` or local imports. |
| `scripts/ci/select_tests.py` (impact-based test selection) builds a stale path map and selects the wrong tests after the rename | Medium | All three phases use **full** `pytest tests/` (not impact selection). Phase 3 patches the selector. |
| Wiki sync fails after Phase 3 path overhaul | Medium | Phase 3 PR runs `scripts/ci/sync_docs_to_wiki.py --wiki-dir /tmp/wiki-test` as a pre-merge dry-run. |
| Docker image build fails because COPY directives reference deleted top-level dirs | Medium | Phase 3 verification includes a local `docker build -f docker/Dockerfile .` (no push). |
| Rust namespace package mechanics surprise pytest pythonpath resolution | Medium | Phase 1 verification explicitly asserts both `import javdb.spider` (source tree) and `import javdb.rust_core` (maturin-installed) succeed in the same Python process. `javdb/` must have no `__init__.py`. |
| Tests reference shells via dynamic strings (e.g. `subprocess.run(["python3", "-m", "utils.X"])`) that grep misses | Low | Phase 1 also scans for string-form module paths (`grep -rEn '"utils\.|"scripts\.spider\.'`). Any hits are added to the deletion manifest as explicit follow-ups. |
| User has local-only scripts that import the legacy paths | Low | User confirmed only `wiki` and `proxy-coordinator` sibling repos are external consumers, and those are excluded. Other local scripts are the user's responsibility. |

---

## Deletion Manifest

A precise, line-level manifest of every artefact Phase 3 must remove ships as `docs/design/adr/ADR-007-deletion-manifest.md`, generated and committed in Phase 1. Phase 3 PR description references that file and ticks every checkbox. The manifest enumerates:

- Directories to remove (`utils/`, `api/`, `migration/`, `legacy/`, `scripts/spider/`, `scripts/ingestion/`, `packages/` once empty).
- Root files (`compat.py`, `pipeline.py`).
- Specific Dockerfile lines.
- Every test file + line number where a legacy import must be rewritten.
- Verification grep commands and their expected output (empty).

---

## Implementation Order (PR Sequence)

```
PR-1  Phase 1: build javdb/ tree, update internal imports, temporary
      compat shell redirection, Rust crate move, deletion manifest         [target #?]
      Verification: full pytest + CLI smoke + maturin develop

PR-2  Phase 2: migrate scripts/ → apps/cli/<subdir>/, workflow YAML
      updates, scripts/ shells removed                                     [depends on PR-1]
      Verification: full pytest + apps/cli/**/--help smoke + workflow lint

PR-3  Phase 3: rewrite test imports, delete compat shells, Dockerfile
      COPY cleanup, docs/wiki/README sync, supersede legacy maps,
      ADR-007 deletion manifest fully ticked                               [depends on PR-2]
      Verification: full pytest + grep gates empty + docker build dry-run
```

Each PR is independently revertable. PR-1 leaves the repo in a consistent state (legacy imports still work via redirected shells). PR-2 leaves the repo in a consistent state (apps/cli/ is the user-facing entry point). PR-3 completes the cleanup.

---

## Follow-Ups (Out of Scope for This ADR)

- `javdb/storage/history_manager.py` re-exports several policy functions from `javdb.pipeline.policies` for backward compatibility with old callers (`should_process_movie`, `determine_torrent_type`, etc.). Once consumers are updated to import from `javdb.pipeline.policies` directly, the re-export tail in `history_manager.py` can be deleted. This is a separate small PR after Phase 3.
- `apps/web/` and `apps/desktop/` are scheduled for removal once the new frontend repo `javdb-autospider-web` reaches GA. Separate ADR or PR at that time.
- `scripts/ci/select_tests.py` may need an upgrade to handle the new path structure; Phase 3 patches the immediate path map but a more durable test-selection rewrite is a separate concern.

---

## Related ADRs and Documents

- Supersedes: [`docs/design/architecture/python-core-mapping.md`](../../architecture/python-core-mapping.md), [`docs/design/architecture/spider-module-reorg.md`](../../architecture/spider-module-reorg.md)
- Coordinates with: [ADR-005](../ADR-005-db-py-retirement-and-repo-pattern.md) (db.py retirement; storage internals reorganised by this ADR; ADR-005 D2 PR-1 will operate inside the new `javdb/storage/` tree)
- Coordinates with: [ADR-006](../ADR-006-pending-mode-default-rollout.md) (pending-mode rollout; `pending_mode_alert_and_pause.py` is one of the scripts migrated in Phase 2; the rename to `apps/cli/db/pending_alert.py` does not change behaviour but the workflow YAML must be updated in the same PR)
- New map (post-restructure): `docs/design/architecture/python-tree-2026-05.md` (created in Phase 3)
