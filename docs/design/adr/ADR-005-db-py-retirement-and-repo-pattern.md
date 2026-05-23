# ADR-005: Full Retirement of db.py + Repo Class Abstraction + Audit Mode Retirement

**Status**: Accepted — **PR-1 through PR-5 shipped on 2026-05-22**; ADR-005 storage/audit/db.py retirement is complete
**Date**: 2026-05-16
**Deciders**: Architecture depth-pass round 2
**Prerequisites**: [ADR-006](ADR-006-pending-mode-default-rollout.md) — Pending Mode default must first be rolled out to 100% and the auto-fallback redesigned before this ADR can execute its D10 gate
**Successor**: [ADR-001](archive/ADR-001-split-db-module.md) — delivers the Phase 3 that ADR-001 never finished, and corrects its over-fine "split by read/write" decision
**Related**: [ADR-011](ADR-011-javdb-parsing-module.md) supersedes D4 / PR-6 parser-helper relocation

## Outstanding Work

PR-1 (Repo classes) ✅ shipped: `HistoryRepo`, `OperationsRepo`, `StatsRepo`, `SessionsRepo`, `SystemStateRepo` are present in `javdb/storage/repos/`. Completion status:

- **PR-2** ✅ shipped (#70, 2026-05-21): `db.py` internally forwards to Repos (dual-write phase).
- **PR-3** ✅ shipped (#71, 2026-05-21): migrated spider/history_manager callers off the function family.
- **PR-4** ✅ shipped (2026-05-22): dropped Audit Mode tables and removed audit write / rollback branches.
- **PR-5** ✅ shipped (2026-05-22): deleted `javdb/storage/db/db.py`; the ADR-001 modules remain as the canonical implementation modules, not shell facades.
- **PR-6** ✅ shipped (2026-05-22): renamed 9 shell modules to `_db_*.py`; `__init__.py` re-exports 65 public symbols; 254 import statements migrated to package-level imports.
- **Parser-helper relocation** remains outside ADR-005 and is tracked by [ADR-011](ADR-011-javdb-parsing-module.md). There is no remaining ADR-005 implementation work.

## Amendments

- **2026-05-17 amendment 1**: After this ADR was accepted, [ADR-007](archive/ADR-007-monorepo-restructure-2026-05.md) reorganised the Python namespace (`packages/python/javdb_*` → top-level `javdb/`). Any PRs from this ADR's implementation order that have not yet merged when ADR-007 Phase 1 lands must operate on the new paths:

  | This ADR refers to | After ADR-007 Phase 1 |
  |---|---|
  | `packages/python/javdb_platform/db.py` | (deleted by ADR-005 D1; its internals already moved to `javdb/storage/db/` by ADR-007) |
  | `packages/python/javdb_platform/db_layer/history_repo.py` | `javdb/storage/repos/history_repo.py` |
  | `packages/python/javdb_platform/db_layer/operations_repo.py` | `javdb/storage/repos/operations_repo.py` |
  | `packages/python/javdb_platform/db_layer/reports_repo.py` (new in D1) | `javdb/storage/repos/reports_repo.py` |
  | `packages/python/javdb_platform/db_layer/stats_repo.py` (new in D1) | `javdb/storage/repos/stats_repo.py` |
  | `packages/python/javdb_platform/db_session.py` | `javdb/storage/db/db_session.py` |
  | `packages/python/javdb_platform/db_history_write.py` etc. | `javdb/storage/db/db_history_write.py` etc. |
  | callers under `packages/python/javdb_spider/` | `javdb/spider/` |
  | callers under `apps/cli/`, `apps/api/`, `scripts/` | `apps/cli/<subdir>/`, `apps/api/`, `apps/cli/` (per ADR-007 Phase 2) |
  | callers under `packages/python/javdb_migrations/tools/` | `javdb/migrations/tools/` |

  Path rename only — Repo class semantics, D1–D10 gate logic, and the 30-day bake dependency on ADR-006 are unchanged. ADR-007's deletion manifest ensures no unfinished work references the legacy paths.

- **2026-05-17 amendment 2**: **Repo signature pattern + naming alignment with code already shipped.** When PR-1 was about to start, an inspection of `javdb/storage/repos/` revealed two legitimate Repo patterns already coexisting in the codebase, each appropriate for its access shape:

  | Class | File | Signature | Why this shape |
  |---|---|---|---|
  | `SessionsRepo` | `javdb/storage/repos/sessions_repo.py` | `__init__(conn)`; methods take `session_id` per call | API-layer **read** surface — the FastAPI request context already holds an open conn; reads are short, no transaction boundary |
  | `SystemStateRepo` | `javdb/storage/repos/system_state_repo.py` | `__init__(conn)`; methods take `key` per call | Same as above — KV reads/writes in a single API call |

  PR-1's three new Repos (`HistoryRepo`, `OperationsRepo`, `StatsRepo`) wrap the **write-mostly function family** in `javdb/storage/db/*.py` (`db_load_history`, `db_stage_history_write`, `db_save_spider_stats`, etc.) — those functions all take a `db_path: Optional[str] = None` and open their own conn for transactional safety, *not* a caller-supplied conn. Forcing them onto the SessionsRepo pattern would require either (a) inlining their SQL into the Repo body (no longer "thin delegate", real risk of bake interference) or (b) breaking each function family's `with get_db(...) as conn:` transaction boundary (correctness risk).

  Decisions:

  1. **Write-Repo signature**: `HistoryRepo`, `OperationsRepo`, `StatsRepo` use `__init__(*, db_path: Optional[str] = None)`; methods take `session_id` per call. The Repo carries no per-call state — it's a typed surface over the existing function family. The original D5 "(conn, session_id=None) constructor" wording is superseded for these three classes; D5's actual goal (eliminate the `db_session._active` thread-local global) is satisfied either way, because `session_id` still flows explicitly through every method that needs it.
  2. **`ReportsRepo` is subsumed by the already-shipped `SessionsRepo`** — D6's planned name was a draft; the implementation correctly named it after its single table (`ReportSessions`). Adding a second class for the same concern would create duplicate test surface and confuse callers. No rename is needed; `SessionsRepo` *is* the "ReportsRepo" of D6.

  D6's four-class plan becomes three new write-Repo classes + reuse of `SessionsRepo`. All other D-level decisions are unchanged.

- **2026-05-20 amendment 3**: **Parser-helper relocation extracted to ADR-011.** D4 / PR-6 originally moved three helpers from `apps.api.parsers.common` into a lower module. That overlapped with a larger parsing-boundary correction. [ADR-011](ADR-011-javdb-parsing-module.md) now owns the full JavDB Parsing Interface move to `javdb.parsing`, including those helpers under `javdb.parsing.common`. ADR-005 remains responsible for Storage/Repo retirement only. Any remaining ADR-005 implementation that needs these helpers should import from `javdb.parsing.common` once ADR-011 Phase 1 has landed.

- **2026-05-21 amendment 4**: **ADR-006 sign-off completed via operator-approved 7-day clean bake bypass.** The original plan required a 30-day bake until approximately 2026-06-15; the maintainer confirmed one clean week with no pending-mode issues and explicitly approved bypassing the remaining wait to continue. This removes the ADR-005 PR-2 start blocker. Risk handling is unchanged: PR-2 must not delete audit schema, audit code, or caller compatibility; ADR-005 PR-4 still requires the D10 trio to pass before audit-table deletion.

- **2026-05-21 amendment 5**: **PR-2 and PR-3 shipped.** PR-2 (#70) routed the `db.py` facade through Repo classes (636 additions, 272 deletions, 13 files). PR-3 (#71) migrated spider and history_manager callers to use Repos directly (573 additions, 44 deletions, 11 files). Both include boundary regression tests enforcing that migrated callers cannot fall back to raw `db_*` functions. Remaining: PR-4 (drop audit tables, gated by D10) and PR-5 (delete `db.py`).

- **2026-05-22 amendment 6**: **D10 gate sign-off — PR-4 unblocked.** BakeCheck.yml has reported all three D10 metrics passing for 4 consecutive days (2026-05-18 through 2026-05-21). The operator approved proceeding with PR-4 on 2026-05-22 despite workflow audit-option removal being 6 days old (1 day short of the 7-day text in D10 #3), since the functional evidence — zero audit sessions, zero orphan rows, 6 consecutive successful DailyIngestion runs — proves the risk is moot. PR-4 (drop audit tables + remove audit code) and PR-5 (delete `db.py`) are now unblocked.

- **2026-05-22 amendment 7**: **PR-4 and PR-5 shipped.** Audit Mode is fully retired: audit tables, audit archive/cleanup tooling, and audit write/rollback branches are gone. `javdb/storage/db/db.py` was deleted. The former ADR-001 split modules (`db_history_read.py`, `db_history_write.py`, `db_stats.py`, etc.) are no longer shell modules; they now own the low-level implementation and are intentionally retained behind the package public API in `javdb/storage/db/__init__.py`.

---

## D10 Gate Check Results

### Initial check (2026-05-16) — two items failed

| Gate item | Status | Evidence |
|---|---|---|
| #1 Count of `WriteMode='audit'` in the last 30 days = 0 | FAIL | Last 30 days audit=54 / pending=13; all-time audit=354 / pending=13 |
| #2 No orphan audit rows | PASS | `MovieHistoryAudit`=9, `TorrentHistoryAudit`=3, 0 bound to committed sessions |
| #3 Workflows removed the `audit` option at least 7 days ago | FAIL | 3 workflows still list `audit` as a valid `write_mode_override` value; `DailyIngestion.yml` L1093 has a live auto-fallback to audit |

A **documentation discrepancy** also surfaced: CONTEXT.md / CLAUDE.md / the ADR-001 docstring claim "Pending Mode is default", but the code fallback at `db_session.py:188` and the SQLite schema's `WriteMode TEXT DEFAULT 'audit'` both show the **actual default is still audit**. This is aspirational, not factual.

**Conclusion**: D2(c) "fully retire Audit Mode" was not executable on 2026-05-16, because Audit Mode was the actual runtime mode for 80% of sessions and the live safety net for Pending Mode failures. Later amendments supersede the start blocker: PR-1 shipped, and the ADR-006 sign-off completed on 2026-05-21 via operator-approved 7-day clean bake bypass to unblock PR-2.

### Re-check (2026-05-22) — all items pass, operator sign-off granted

`BakeCheck.yml` daily cron results since `BAKE_SINCE=2026-05-16`:

| Date | audit_session_count | orphan_audit_rows | pause_trigger_count | Result |
|---|---|---|---|---|
| 2026-05-18 | 0 (threshold 0) ✅ | 0 (threshold 0) ✅ | 1 (threshold 1) ✅ | PASS |
| 2026-05-19 | 0 ✅ | 0 ✅ | 1 ✅ | PASS |
| 2026-05-20 | 0 ✅ | 0 ✅ | 1 ✅ | PASS |
| 2026-05-21 | 0 ✅ | 0 ✅ | 1 ✅ | PASS |

Supporting evidence:
- DailyIngestion: 6 consecutive successful runs (2026-05-16 through 2026-05-21)
- All active workflow code paths: zero audit references (verified 2026-05-22 full grep audit)
- Workflow audit-option removal: 6 days (1 day short of D10 #3's text), bypassed by operator

**Conclusion**: The operator approved PR-4 on 2026-05-22. All functional D10 metrics pass; the 1-day shortfall on the workflow-removal calendar check is immaterial given zero audit session activity. PR-4 and PR-5 may proceed.

---

---

## Context

ADR-001 planned to split the 6,370-line `db.py` into nine function-scoped modules (`db_connection.py` / `db_session.py` / `db_history_read.py` / `db_history_write.py` / `db_reports.py` / `db_stats.py` / `db_operations.py` / `db_rollback.py` / `db_migrations.py`) across three phases: Phase 1 extract modules → Phase 2 migrate importers → Phase 3 delete the `db.py` façade and eliminate global state.

### Actual state (round 2 depth-pass probing)

- `db.py` still has **5,298 lines and 131 `def`/`class` declarations** and still **carries real implementation**:
  - `db_upsert_history` (line 2373) — the Audit Mode write path
  - `_audit_*` helpers (lines 2178–2361)
  - Every schema migration (`_migrate_v5_to_v6` / `_migrate_single_to_split` / `init_db` / `_ensure_*_columns`)
  - All Operations-domain helpers (alongside the almost-empty `db_operations.py`)
  - Connection pooling and session-ID generation (alongside `db_connection.py` / `db_session.py`)
- The newly extracted `db_history_read.py` (371 lines) and `db_history_write.py` (238 lines) are mostly forwarders into `db.py`, e.g. `db_history_write.db_upsert_history(*args, **kwargs)` is a one-line proxy.
- The third-layer abstraction `db_layer/history_repo.py` already exists, but contains only four module-level functions (no `HistoryRepo` class — the CLAUDE.md example code wrote a check the codebase couldn't cash).
- `db.py` reverse-imports `apps.api.parsers.common` (lines 45–50), breaking the monorepo layering.
- `db_session._active` global state remains the implicit contract of the write path.
- Audit Mode is marked "scheduled to sunset 2026-08-13" in CONTEXT.md, but the code still routes the main write path through it; the `JAVDB_AUDIT_WRITES_DISABLED` kill switch exists but is not enabled by default.

### Problems

1. **Three-layer abstraction forwards through forwards**, violating ADR-001's own **locality** principle — understanding one history write still requires hopping across three files.
2. ADR-001's decision #1 ("split by read/write") **delivered no benefit** in practice: every real caller (`history_manager.py`, `db_rollback.py`, CLI tools) crosses both seams simultaneously. Per LANGUAGE.md, that is not a seam — it is a redundant cut through a single usage pattern.
3. ADR-001 Phase 3's "eliminate global state" never started, so the write interface's invariant ("thread has set an active session") hides outside the function signatures.
4. Long-running coexistence of Audit Mode and Pending Mode forces `db_history_write.py` to support both paths — extra surface area for no business value (pending is meant to be the default anyway).
5. Migrations are still all in `db.py`, unsplit.

---

## Decision

The 11 items below take effect as one group; they are not separately cherry-pickable — any single item alone would render some of the others meaningless.

### D1: Empty `db.py` of all real implementation

Code in the four domains (History / Operations / Migrations / Connection+Session utilities) moves out together. `db.py` is ultimately deleted; no façade is preserved.

### D2: Fully retire Audit Mode (read and write)

- Delete the `db_upsert_history` audit path and all `_audit_*` helpers.
- Drop the two tables `MovieHistoryAudit` and `TorrentHistoryAudit` (migration v14).
- Delete the "read audit table → restore" branch in `db_rollback`.
- Remove the `audit` option from the workflow input `write_mode_override`; keep only `pending`.
- Delete the environment variables `JAVDB_HISTORY_WRITE_MODE` and `JAVDB_AUDIT_WRITES_DISABLED`.
- Keep the `ReportSessions.WriteMode` column (for backward compatibility with historical rows); new writes always set `pending`.

### D3: Introduce the Repo class style

Stop exposing DB access via families of module-level functions; use classes instead:

```python
class HistoryRepo:
    def __init__(self, conn, session_id: str | None = None): ...
    def stage_movie(self, href: str, ...): ...
    def stage_torrent(self, ...): ...
    def commit(self): ...
    def rollback(self): ...
    def load_history(self, phase: int | None = None): ...
    def load_history_snapshot(self): ...
    def check_torrent_in_history(self, href: str, kind: str): ...
```

Write methods require a non-empty `session_id` at construction; read methods accept `session_id=None`.

### D4: Sink URL / parsing utilities

Superseded by [ADR-011](ADR-011-javdb-parsing-module.md). The three functions in `apps.api.parsers.common` that `db.py` uses (`movie_href_lookup_values`, `javdb_absolute_url`, `absolutize_supporting_actors_json`) are now part of the full JavDB Parsing Interface migration. They move to `javdb.parsing.common`, not `packages/python/javdb_core/url_utils.py`.

The layering invariant remains: Storage/Repo code must not import parser helpers from `apps.api`. After ADR-011 Phase 1, it imports from `javdb.parsing.common`.

### D5: `Repo(conn, session_id)` constructor signature

`session_id` is bound at construction time; there is no more `db_session._active` global. Tests construct `HistoryRepo(test_conn, "test-session-id")` explicitly — no global to patch.

### D6: Four Repos converted to classes in lockstep

| Repo | Physical location | Status |
|---|---|---|
| `HistoryRepo` | `packages/python/javdb_platform/db_layer/history_repo.py` | Upgrade (file exists, add the class) |
| `OperationsRepo` | `packages/python/javdb_platform/db_layer/operations_repo.py` | Upgrade (file exists, add the class) |
| `ReportsRepo` | `packages/python/javdb_platform/db_layer/reports_repo.py` | New |
| `StatsRepo` | `packages/python/javdb_platform/db_layer/stats_repo.py` | New |

All Repos share the `BaseRepo(conn, session_id=None)` constructor protocol; write methods guard with `_require_session()` to ensure `session_id` is non-empty.

Alongside the four Repos, introduce a `RollbackCoordinator(conn).rollback_session(session_id)` coordinator. It invokes each Repo's `.rollback()` in order (replacing `db_rollback.db_rollback_session()`).

### D7: Migrations — one file per version

Slice migrations by schema version:

```
packages/python/javdb_migrations/
├── runner.py                       # dispatcher: init_db(), detect_schema_version()
├── versions/
│   ├── __init__.py
│   ├── v6_split_dbs.py             # def migrate(conn) -> None
│   ├── v7_actor_columns.py
│   ├── v8_rollback_columns.py
│   ├── v9_to_v13_*.py              # existing versions
│   └── v14_drop_audit_tables.py    # new: drop MovieHistoryAudit / TorrentHistoryAudit
└── tools/                          # ad-hoc maintenance scripts, structure unchanged
    ├── cleanup_history_priorities.py
    └── ...
```

Delete `packages/python/javdb_platform/db_migrations.py`.

### D8: Migration interface is a function, not a class

Every `v{N}_*.py` exposes `def migrate(conn) -> None`. A migration is a one-shot stateless schema change; it needs no class template. Repos are stateful domain operations. The two forms are allowed to differ.

### D9: Phased PR roll-out (zero-breakage)

```
PR-1  Build the 4 new Repo classes under db_layer/ (coexist with existing function families;
      zero caller changes)
PR-2  db.py internals delegate to the Repo classes (dual-write parallel: callers keep using
      db.py unchanged, but the underlying code path goes through the new Repos)
PR-3a Migrate callers in packages/python/javdb_spider/ and javdb_platform/history_manager.py
PR-3b Migrate callers in packages/python/javdb_ingestion/ and javdb_integrations/
PR-3c Migrate callers in apps/cli/, apps/api/, scripts/, packages/python/javdb_migrations/tools/
PR-4  ✅ Shipped 2026-05-22: confirmed D10 gate, dropped audit tables, and
      removed audit write/rollback code.
PR-5  ✅ Shipped 2026-05-22: deleted db.py. The ADR-001 split modules are
      retained as canonical implementation modules rather than shell facades.
PR-6  Superseded by ADR-011. Parser/helper relocation is extracted from
      ADR-005 and implemented through ADR-011 Phase 1-3. Remaining ADR-005
      work should consume helpers from javdb.parsing.common after Phase 1.
PR-7  Re-arrange migrations into per-version files per D7; delete db_migrations.py
```

Each PR is independently revertable. PR-1 and PR-2 introduce no behavioural change and can land on their own; PR-3a/b/c is incremental.

### D10: Audit Mode retirement safety gate

Before PR-4 starts, confirm:

1. `ReportSessions` has 0 rows with `WriteMode='audit'` in the last 30 days.
2. `MovieHistoryAudit` / `TorrentHistoryAudit` contain no orphan audit rows from committed sessions (or have been cleared by `StaleSessionCleanup`).
3. The three workflows (`DailyIngestion`, `AdHocIngestion`, `TestIngestion`) have had the `audit` option removed from `write_mode_override` for at least 7 days.

If the check fails, first set `JAVDB_AUDIT_WRITES_DISABLED=1` org-wide and bake for 1–2 weeks before continuing.

### D11: Test strategy — replace, don't layer

- Existing unit tests targeting `db_history_write.py` / `db_session.py` globals are **deleted** once the new Repo interface is in place (per DEEPENING.md "Replace, don't layer").
- New unit tests are written against the Repo class interface: `HistoryRepo(in_memory_conn, "session-x").stage_movie(...)` → assert observable outcome.
- Integration tests that exercise cross-Repo coordination (e.g. `RollbackCoordinator.rollback_session`) are kept.
- Tests do not mock `get_active_session_id()` — they pass `session_id` at construction.

---

## Alternatives Considered

### Alternative A — Keep ADR-001's 9-module function families; only finish Phase 3 by deleting `db.py`

**Rejected**. A caller walk-through (`history_manager.py` imports both `db_history_read` and `db_history_write` within 50 lines) showed that the "read/write split" seam is never used independently in real usage patterns. Per LANGUAGE.md ("two adapters = real seam"), these two files do not constitute a real seam. Keeping them only adds import paths and file counts.

### Alternative B — Convert only `HistoryRepo` to a class; keep the other domains as function families

**Rejected**. A single Repo is not a pattern ("one Repo = hypothetical seam"). Either every write domain unifies on the Repo-class form (to gain "tests don't need to mock global state"), or all domains stay as function families. A mixed style would make every new contributor guess every time.

### Alternative C — Keep the Audit Mode write path as a fallback for diagnosing D1 / SQLite drift

**Rejected**. Audit Mode was the answer to the pre-ADR-001 problem of "rollback without a Pending table". Pending Mode now covers that fully. Dual Mode drift diagnosis lives in `dual_connection.DualConnection.drift_jsonl`, separate from Audit Mode. Keeping both paths means `HistoryRepo.stage_movie` carries `if write_mode == 'audit': ...` forever, defeating the simple signature in D5.

### Alternative D — Land everything in one big PR

**Rejected**. ~4,000–5,000 lines of migration + dozens of caller changes + test rewrites in a single PR makes review unsafe and revert granularity too coarse. The 7-PR roll-out in D9 is a necessary price.

### Alternative E — Keep the Connection / Session utilities in `db.py` as a façade

**Rejected**. The goal of D1 is for `db.py` to **actually disappear**. Leaving any utility behind means `db.py` keeps functioning as an "escape hatch", and new code drifts back to `from packages.python.javdb_platform.db import ...` instead of `from db_connection import ...`. If we do this, we do it all the way.

---

## Consequences

### Positive

1. **Locality genuinely lands** — understanding one history write requires reading only `HistoryRepo`, not three files.
2. **The interface is the test surface** — `HistoryRepo(test_conn, "x").stage_movie(...)` is a direct call; no globals to patch.
3. **Audit Mode is fully retired** — ~1,200 lines of legacy code go away (audit writes + audit helpers + audit rollback branch + audit tables).
4. **Layering invariant** — `packages` depends on `apps` one-directionally; new code cannot accidentally add a reverse import.
5. **ADR-001 actually completes** — Phase 3 lands, and ADR-001's decision #1 (read/write split) is corrected.

### Negative

1. **Coordination cost across 7 PRs** — each is independent but they must land in order.
2. **Caller change surface is wide** — an estimated 50+ files need their imports and call shape updated.
3. **Test rewrite** — the entire batch of global-mock tests like `tests/unit/test_workflow_resolve_write_mode.py` is deleted and rewritten.
4. **The `HistoryRepo` example in CLAUDE.md is now a contract** — it must be implemented; no more "writing checks the codebase can't cash".

### Risks

1. **External scripts may still import deleted `db.py`**.
   - **Mitigation**: the package public API now re-exports supported storage helpers from `javdb.storage.db`; internal callers were migrated before deletion and grep checks enforce no `javdb.storage.db.db` imports remain.
2. **Historical docs may imply audit fallback is still available**.
   - **Mitigation**: current operator docs mark Audit Mode retired; historical Appendix sections must be explicitly labelled as archival context.
3. **The Repo class's `session_id=None` default lets "forgot to pass session" become an implicit bug again.**
   - **Mitigation**: every `stage` / `commit` / `rollback` method's first line is `self._require_session()`, which raises `RuntimeError`. The interface contract is executable.
4. **Storage/Repo work may accidentally keep importing helpers from `apps.api.parsers.common`.**
   - **Mitigation**: ADR-011 Phase 2 migrates internal callers to `javdb.parsing.common`; ADR-011 Phase 3 deletes the API parser compatibility Adapters.

---

## Related Decisions

- **ADR-001** (partially corrected + completed): this ADR is the real delivery of ADR-001 Phase 3, and corrects its "split History module by read/write" decision.
- **ADR-002 / ADR-003 / ADR-004**: Worker-side refactors, no direct coupling with this ADR.
- **ADR-011**: owns parser/helper relocation that D4 / PR-6 originally scoped too narrowly.

---

## References

- [CONTEXT.md](../../../CONTEXT.md) — Domain vocabulary (updated alongside this ADR with Repo / Layering Invariant / Audit Mode retirement status)
- [LANGUAGE.md](https://example.invalid/skill/improve-codebase-architecture/LANGUAGE.md) — Architecture language (Module / Interface / Seam / Adapter / Depth)
- [DEEPENING.md](https://example.invalid/skill/improve-codebase-architecture/DEEPENING.md) — Test strategy "Replace, don't layer"
- ADR-001 lessons-learned §4: module boundaries should be based on responsibility rather than physical structure — this ADR adds a further correction: **also based on real usage patterns rather than hypothetical ones**.

---

## Appendix A: Before / After Interface Comparison

```python
# Before
from packages.python.javdb_platform.db_session import set_active_session_id
from packages.python.javdb_platform.db_history_write import db_stage_history_write
set_active_session_id("20260516T093000.000000Z-0001-0001")
db_stage_history_write(conn, movie_data)  # implicit dependency on thread-local session

# After
from packages.python.javdb_platform.db_layer.history_repo import HistoryRepo
repo = HistoryRepo(conn, "20260516T093000.000000Z-0001-0001")
repo.stage_movie(href="/movies/abc123", ...)  # session explicit
repo.commit()
```

## Appendix B: v14 Migration Sketch

```python
# packages/python/javdb_migrations/versions/v14_drop_audit_tables.py
def migrate(conn) -> None:
    """v14: drop MovieHistoryAudit / TorrentHistoryAudit per ADR-005."""
    conn.execute("DROP TABLE IF EXISTS MovieHistoryAudit")
    conn.execute("DROP TABLE IF EXISTS TorrentHistoryAudit")
```
