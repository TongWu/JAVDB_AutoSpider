# IMP-ADR046-05: ADR-046 Phase 5 — Delete the Global Session-ID Machinery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-046](ADR-046-retire-db-facade.md) — **Phase 5**. Builds on Phases 1–3 (repos are session-bound: `HistoryRepo`/`OperationsRepo`/`StatsRepo` resolve session explicitly). **Phase 5 runs BEFORE Phase 4** (the finale): IMP-04 explicitly depends on Phase 5 having reduced the live caller set. This phase removes the *ambient* session-identity global; Phase 4 ([IMP-ADR046-04](IMP-ADR046-04-privatize-db-facade.md)) then privatizes the `db_*` read/write facade.

**Goal:** Delete the ambient process-global **session-ID** machinery from `javdb/storage/db/_db_session.py` and migrate every reader/setter to obtain `session_id` **explicitly** (ADR-046 D2: "session_id is explicit, never ambient"). The deleted symbols are:

- `set_active_session_id` / `get_active_session_id`
- `_active_session_id_value` (the module global)
- `_SESSION_ID_SENTINEL` / `_resolve_session_id`

…plus their two re-exports from `javdb/storage/db/__init__.py` and the vestigial `SessionLifecycleRepo.get_active_session_id()` wrapper.

## Design decisions (resolved during planning, 2026-06-04)

- **DD-1 (detail-path threading — the central fork).** The spider detail path uses **pure parameter threading**: an explicit `session_id` param on every hop (`process_detail_entries` → `_claim_detail_candidates` → … → `save_parsed_movie_to_history`). It does **NOT** read `runtime.session_id` or any ambient accessor. Most faithful to D2.
- **DD-2 (rclone standalone-vs-inherited probe).** Replace the `get_active_session_id()` "am I standalone or inherited?" probe with an explicit `session_id: Optional[str] = None` param at the rclone entry points. The ownership flags stay correct via `created_local_session = (session_id is None)`. Standalone CLI passes `None` → creates+owns the local session (behavior preserved). Verify no current in-pipeline rclone caller relies on inheritance (grep at execution).
- **DD-3 (best-effort callees: email / sentinel / dedup).** Add an optional `session_id: Optional[str] = None` param threaded from orchestration. Standalone callers pass `None`. These target **NULLABLE**-`SessionId` tables (DedupRecords, PikpakHistory, EmailNotificationHistory, InventoryAlignNoExactMatch); `OperationsRepo._resolve_session()` returns `None` and the row persists untagged (the Phase-2 contract — must NOT raise).
- **DD-4 (scope — session-id only).** Delete the session-id accessors ONLY. **KEEP**: `_active_session_id_lock` (genuinely shared — it guards run-identity + write-mode too), `get/set_active_run_identity`, `get/set_active_write_mode`, `_resolve_write_mode`, `generate_session_id`, `generate_integer_id`, `is_valid_session_id`, `SESSION_ID_PATTERN`.
- **DD-5 (order — delete LAST).** Migrate ALL readers/setters first; delete the accessors + re-exports + the `SessionLifecycleRepo` wrapper in the final task (Task 10). The accessors stay untouched until then so every intermediate commit is green. Several readers use **function-local lazy imports** (`legacy`, `pikpak`, `email`, `sentinel`, `history_manager`) — these only raise `ImportError` when their branch *executes*, so deleting the symbol before migrating them would surface as intermittent runtime failures, not import-time errors. Delete last.
- **DD-6 (legacy is dead).** `javdb/legacy/` is not imported by any production code (only 2 test files import it). Replace its function-local `get_active_session_id()` reads with `session_id=None` (an untagged actor update on the rollback-only dead path is acceptable) and drop the lazy import so no dangling reference to a deleted symbol remains.

**Out-of-scope adjacent mechanism (do NOT touch):** `javdb/infra/csv_writer.py` has its **own** `_active_session_id` module-global with its own `set_active_session_id` — a SEPARATE mechanism unrelated to `_db_session.py`. Leave it entirely alone.

**Tech Stack:** Python 3 + `pytest`. Test command (this worktree lacks the gitignored Rust `.so`, so point `PYTHONPATH` at the checkout's pure-Python fallback): `PYTHONPATH=javdb/rust_core/python python3 -m pytest <files> -q`. Commit identity: `git -c user.name=Ted -c user.email=ted@wu.engineer`; trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

> **⚠ Line numbers are a 2026-06-04 snapshot.** Re-grep each site before editing; functions may have shifted.

---

## File Structure

| Path | Modify/Test | Responsibility |
| --- | --- | --- |
| `javdb/spider/app/run_service.py` | Modify | Owner: thread `result_context.session_id` into callees; drop set/clear of the session global |
| `javdb/spider/detail/runner.py` | Modify | Add `session_id` param to `process_detail_entries` + `_claim_detail_candidates`; switch reads; fix stale `session_id_int` name |
| `javdb/storage/history_manager.py` | Modify | `save_parsed_movie_to_history(session_id=...)`; keep None→warn+skip guard |
| `javdb/integrations/rclone/manager/service.py` | Modify | Explicit `session_id` param (3 sites); ownership flags key off the param |
| `javdb/spider/services/dedup.py` | Modify | `append_dedup_record` / `mark_records_deleted` take `session_id` |
| `javdb/integrations/pikpak/bridge/service.py` | Modify | Thread `session_id` to `save_to_pikpak_history`; delete set/clear plumbing |
| `javdb/integrations/notify/email/delivery.py` | Modify | `send_email(session_id=...)` |
| `javdb/ops/sentinel/service.py` + `field_health.py` | Modify | Delete `_active_session_id()` helper; thread param |
| `javdb/migrations/tools/align_inventory_with_moviehistory.py` | Modify | Use `args.session_id`; delete set-global |
| `javdb/legacy/_spider_legacy.py` | Modify | Dead path → `session_id=None` |
| `javdb/storage/repos/session_lifecycle_repo.py` | Modify | Delete `get_active_session_id()` method (Task 9) |
| `javdb/storage/db/_db_session.py` | Modify | Delete the 5 session-id symbols (Task 10) |
| `javdb/storage/db/__init__.py` | Modify | Remove 2 import lines + 2 `__all__` entries (Task 10) |
| ~14 test files (per task) | Modify | Migrate from setting the global to passing session explicitly |

---

## Task 1: Spider detail write path — pure parameter threading (DD-1) ⚠ highest friction

**Files:** `javdb/spider/app/run_service.py`, `javdb/spider/detail/runner.py`, `javdb/storage/history_manager.py`; tests `tests/unit/test_spider_detail_runner.py`, `tests/unit/test_history_manager.py`, `tests/unit/test_spider_run_result.py`, `tests/unit/test_actor_link_absolutize_on_commit.py`, `tests/unit/test_href_absolute_contract.py`.

**Context:** `run_service` is the single session owner — `_session_id = SessionLifecycleRepo().create_report_session(...)` (~:565), also stored as `result_context.session_id` (~:577, a ContextVar-backed `_SpiderResultContext`). It SETS the global at ~:592, READS it on the failure path at ~:958, CLEARS at ~:976. The detail path reads the global in `runner.py` (~:173 claim affinity, ~:490 stage affinity) and in `history_manager.save_parsed_movie_to_history` (~:171, writes NOT-NULL `MovieHistory`/`TorrentHistory`).

- [ ] **Step 1.1 — Enumerate ALL callers of `save_parsed_movie_to_history`** (the hop count is the risk). Known: `runner.py:~1123` (production), `history_manager.py:~362` (internal — trace which path), `migrations/tools/update_history_format.py:~166` (one-off tool), `legacy/_spider_legacy.py:~1568,~1745` (dead — Task 8). For each, decide the session source: production → threaded `session_id`; tool/legacy → `None` (keep the existing None→warn+skip guard at ~:172-175).
- [ ] **Step 1.2 — Failing tests.** Update `test_spider_detail_runner.py` (smoke ~:822,:858 currently `monkeypatch.setattr(dc, 'get_active_session_id', lambda: 4242/None)`) to instead pass `session_id="<TEXT id>"` to `_claim_detail_candidates` and assert it reaches the claim client; use a TEXT id (not `4242`). Add a `test_history_manager.py` case asserting `save_parsed_movie_to_history(..., session_id=sid)` stages under `sid` and that `session_id=None` warns+skips. Run → FAIL.
- [ ] **Step 1.3 — `history_manager.save_parsed_movie_to_history`.** Add keyword-only `session_id: Optional[str] = None`; replace the `get_active_session_id()` read (~:171) with the param; drop the lazy import (~:143); keep the `None → warn + skip` guard verbatim.
- [ ] **Step 1.4 — `runner.py`.** Add keyword-only `session_id: Optional[str] = None` to `process_detail_entries` (~:425) and `_claim_detail_candidates` (~:119). Replace reads at ~:173 and ~:490 with the param. Pass `session_id=session_id` into the `save_parsed_movie_to_history(...)` call (~:1123) and into the `_claim_detail_candidates(...)` call (~:484). **Rename the stale `session_id_int` var (~:173) to `session_id`** (session IDs are TEXT now; the `_int` suffix is pre-2026-05-13 cruft). Drop `get_active_session_id` from the import (~:12).
- [ ] **Step 1.5 — `run_service.py` (owner).** Pass `session_id=result_context.session_id` into the two `process_detail_entries(...)` calls (~:678, ~:751). Replace the failure-path read (~:958) with `_get_result_context().session_id` (or the `result_context` already in scope). **Delete** the SET (~:592) and CLEAR (~:976) of the session global and drop `set_active_session_id`/`get_active_session_id` from the imports (~:584-588, ~:951, ~:972). **Keep** the run-identity/write-mode set/clear (out of scope).
- [ ] **Step 1.6 — Verify + commit.** Run the listed test files green, plus `tests/unit/test_actor_link_absolutize_on_commit.py` and `tests/unit/test_href_absolute_contract.py` (migrate any `set_active_session_id(...)` fixtures there to explicit session binding on the repo/helper under test). Commit `refactor(spider): thread explicit session_id through the detail write path (ADR-046 P5)`.

---

## Task 2: rclone manager service — explicit session param (DD-2)

**Files:** `javdb/integrations/rclone/manager/service.py`; test `tests/unit/test_rclone_manager.py`.

**Context:** 3 reads of `SessionLifecycleRepo().get_active_session_id()`: ~:598 (self-heal `mark_orphan_records` — nullable write), ~:1207 (scan probe), ~:1410 (replace probe). Sites 1207/1410 use it as a standalone-vs-inherited signal: `created_local_session = (staging_sid is None)` controls finalize ownership.

- [ ] **Step 2.1 — Confirm callers.** Grep callers of the rclone scan/replace service functions; confirm the only current entry is the standalone CLI (`apps/cli/rclone/manager`), which has no parent session. If so, DD-2's default-`None` path always creates the local session (behavior identical to today).
- [ ] **Step 2.2 — Failing test.** Rewrite the `test_rclone_manager.py` cases that set the global + `FakeSessionLifecycleRepo.get_active_session_id` (~:48-51,:84,:119-129,:401,:420,:469-470,:716,:765) to drive the standalone/inherited branch via the new explicit `session_id` param instead. Run → FAIL.
- [ ] **Step 2.3 — Implement.** Add `session_id: Optional[str] = None` to the rclone entry/service functions that reach these sites; thread it down. Replace the 3 reads with the param. `created_local_session`/`_created_local_staging_session` key off `(session_id is None)`. Site ~:598 uses `OperationsRepo(session_id=session_id)` (nullable; non-raising).
- [ ] **Step 2.4 — Verify + commit.** `pytest tests/unit/test_rclone_manager.py -q` green. Commit `refactor(rclone): thread explicit session_id; drop the ambient standalone probe (ADR-046 P5)`.

---

## Task 3: dedup service — explicit session param (DD-3)

**Files:** `javdb/spider/services/dedup.py`; test `tests/unit/test_dedup_checker.py`.

- [ ] **Step 3.1 — Failing test.** `test_dedup_checker.py` (~:45-48) sets the global to tag writes; rewrite to pass `session_id` to `append_dedup_record` / `mark_records_deleted` and assert the `OperationsRepo` ctor receives it (and that `None` → untagged, no raise). Run → FAIL.
- [ ] **Step 3.2 — Implement.** Add `session_id: Optional[str] = None` to `append_dedup_record` (~:568 area) and `mark_records_deleted` (~:595 area); replace the `SessionLifecycleRepo().get_active_session_id()` reads with `OperationsRepo(session_id=session_id)`. Thread `session_id` from callers (overlaps Task 2 wiring where dedup is invoked from rclone).
- [ ] **Step 3.3 — Verify + commit.** `pytest tests/unit/test_dedup_checker.py -q` green. Commit `refactor(dedup): explicit session_id on dedup record writes (ADR-046 P5)`.

---

## Task 4: pikpak bridge — thread session, delete plumbing (DD-3)

**Files:** `javdb/integrations/pikpak/bridge/service.py`; test `tests/unit/test_pikpak_bridge.py`.

**Context:** `pikpak_bridge(...)` already takes `session_id=None` (~:463) and `_pikpak_bridge_impl` (~:488) has it; the set/clear (~:464-473, ~:480-485) only exists so the deep callee `save_to_pikpak_history` (~:409, called 8× at ~:703-778) can read the global at ~:437.

- [ ] **Step 4.1 — Failing test.** `test_pikpak_bridge.py` (~:194,:197,:203) asserts the bridge sets the global to 42 and clears it. Rewrite to assert `session_id` is threaded into `save_to_pikpak_history` (no global). Run → FAIL.
- [ ] **Step 4.2 — Implement.** Add `session_id: Optional[str] = None` to `save_to_pikpak_history` (~:409); ~:437 → `OperationsRepo(session_id=session_id)`; drop the lazy import (~:431). Pass `session_id` into all 8 `save_to_pikpak_history(...)` calls from `_pikpak_bridge_impl`. **Delete** the set/clear plumbing (~:464-473, ~:480-485); `pikpak_bridge` becomes a thin pass-through to `_pikpak_bridge_impl`.
- [ ] **Step 4.3 — Verify + commit.** `pytest tests/unit/test_pikpak_bridge.py -q` green. Commit `refactor(pikpak): thread session_id to pikpak-history writes, drop ambient plumbing (ADR-046 P5)`.

---

## Task 5: email delivery — explicit session param (DD-3)

**Files:** `javdb/integrations/notify/email/delivery.py`; check callers in `apps/cli/notify/` + the pipeline notify step.

- [ ] **Step 5.1 — Failing test.** Add/extend a delivery test asserting `send_email(session_id=sid)` records EmailNotificationHistory under `sid` (and `None` → untagged). Run → FAIL.
- [ ] **Step 5.2 — Implement.** Add `session_id: Optional[str] = None` to `send_email` (~:58); replace the lazy read (~:109-113) with the param (passed positionally to `append_email_history` at ~:126). Thread `session_id` from the notify orchestration callers (enumerate them; standalone/ad-hoc passes `None`).
- [ ] **Step 5.3 — Verify + commit.** Run the email + notify tests green. Commit `refactor(notify): explicit session_id on email-history writes (ADR-046 P5)`.

---

## Task 6: sentinel + field_health — delete helper, thread param (DD-3)

**Files:** `javdb/ops/sentinel/service.py`, `javdb/ops/sentinel/field_health.py`; test `tests/unit/test_index_sentinel_observe.py`.

**Context:** `service.persist_run(fills, *, session_id=None, repo=None)` (~:50) already accepts `session_id`; the ambient `_active_session_id()` helper (~:38-47) is only the fallback (~:51). `field_health.persist_run` (~:77) calls `_svc_persist(fills, repo=repo)` (~:88) without forwarding session.

- [ ] **Step 6.1 — Failing test.** `test_index_sentinel_observe.py` (~:52,:58,:68,:71) drives behavior via `set_active_session_id`. Rewrite to pass `session_id` explicitly (line ~:54 already uses the explicit form — match it). Run → FAIL.
- [ ] **Step 6.2 — Implement.** Delete `_active_session_id()` (~:38-47); simplify ~:51 to `sid = session_id`. Add `session_id: str | None = None` to `field_health.persist_run` (~:77) and forward `_svc_persist(fills, session_id=session_id, repo=repo)` (~:88). The run_service call (~:600) passes `session_id=result_context.session_id` (wire in Task 1 Step 1.5 or here — note the cross-task touch).
- [ ] **Step 6.3 — Verify + commit.** `pytest tests/unit/test_index_sentinel_observe.py -q` green. Commit `refactor(sentinel): explicit session_id for field-health persist (ADR-046 P5)`.

---

## Task 7: align_inventory tool — use args.session_id (DD per IMP-04 Task 1.1)

**Files:** `javdb/migrations/tools/align_inventory_with_moviehistory.py`; test `tests/integration/test_align_inventory_with_moviehistory.py` (if present).

**Context:** Reads at ~:810 (nested closure `_apply_align_result`) and ~:945 (`run_alignment` body); both close over `args`. CLI arg `--session-id` (~:1150). Sets the global in `main()` (~:1176-1177).

- [ ] **Step 7.1 — Implement.** Lines ~:810,~:945 → `session_id=args.session_id`. Drop `get_active_session_id` from the multi-import (~:79). **Delete** the set-global block in `main()` (~:1174-1182). (`db_upsert_align_no_exact_match` takes `session_id` and writes the nullable `InventoryAlignNoExactMatch`; `args.session_id` may be `None` for standalone — valid.)
- [ ] **Step 7.2 — Verify + commit.** Run the align integration test if it exists (`pytest tests/integration/test_align_inventory_with_moviehistory.py -q`); else smoke-import the module. Commit `refactor(align): use args.session_id explicitly, drop set-global (ADR-046 P5)`.

---

## Task 8: legacy spider — dead-path session_id=None (DD-6)

**Files:** `javdb/legacy/_spider_legacy.py`; verify `tests/unit/test_adr005_pr3a_repo_callers.py` (imports legacy at ~:176,:226).

- [ ] **Step 8.1 — Implement.** At ~:1585-1587 and ~:1764-1766, replace `HistoryRepo(session_id=get_active_session_id())` with `HistoryRepo(session_id=None)` and delete the function-local `from javdb.storage.db import get_active_session_id` import lines. (Confirm `javdb.legacy` has no production importer via grep.)
- [ ] **Step 8.2 — Verify + commit.** The 2 legacy tests mock `HistoryRepo` and assert `batch_update_movie_actors` was called (not the session value), so they pass unchanged — confirm. `pytest tests/unit/test_adr005_pr3a_repo_callers.py -q`. Commit `refactor(legacy): drop ambient session read on the dead actor-update path (ADR-046 P5)`.

---

## Task 9: Remove the vestigial `SessionLifecycleRepo.get_active_session_id()` wrapper

**Files:** `javdb/storage/repos/session_lifecycle_repo.py`; test `tests/unit/test_session_lifecycle_repo.py`.

**Precondition:** Tasks 2 + 3 (its only production callers — rclone, dedup) are done.

- [ ] **Step 9.1 — Confirm zero production callers.** `grep -rn "\.get_active_session_id()" javdb apps --include='*.py'` returns nothing outside the repo def + tests.
- [ ] **Step 9.2 — Delete** the `get_active_session_id` method (~:24-27). Delete the delegate test `test_active_session_id_delegates` (~:101-102).
- [ ] **Step 9.3 — Verify + commit.** `pytest tests/unit/test_session_lifecycle_repo.py -q` green. Commit `refactor(storage): drop vestigial SessionLifecycleRepo.get_active_session_id (ADR-046 P5)`.

---

## Task 10: Delete the global accessors + re-exports (LAST) + regression guard

**Precondition:** Tasks 1–9 complete; no reader/setter remains. **Files:** `javdb/storage/db/_db_session.py`, `javdb/storage/db/__init__.py`; new test `tests/unit/test_adr046_p5_global_session_retired.py`.

- [ ] **Step 10.1 — Pre-flight grep (must be empty).**
```bash
grep -rn "get_active_session_id\|set_active_session_id" javdb apps --include='*.py' \
  | grep -vE "javdb/storage/db/_db_session.py|csv_writer.py|run_identity|write_mode"
```
Any hit (other than `_db_session.py` itself, the unrelated `csv_writer.py`, and run-identity/write-mode lines) must be migrated before proceeding.
- [ ] **Step 10.2 — Failing regression test.** Create `tests/unit/test_adr046_p5_global_session_retired.py`: assert `get_active_session_id`/`set_active_session_id` are NOT importable from `javdb.storage.db` (`pytest.raises(ImportError)`), not in `javdb.storage.db.__all__`, and absent from `_db_session.py` source; assert `generate_session_id`, `get_active_run_identity`, `get_active_write_mode` ARE still importable (KEEP-set guard). Run → it will fail until 10.3/10.4.
- [ ] **Step 10.3 — Delete from `_db_session.py`:** `set_active_session_id`, `get_active_session_id`, `_active_session_id_value`, `_SESSION_ID_SENTINEL`, `_resolve_session_id`. **KEEP** `_active_session_id_lock` (still used by run-identity + write-mode — verify the 4 remaining usages compile) and everything else. (Optional cosmetic: rename the lock to `_active_context_lock` — skip unless trivial.)
- [ ] **Step 10.4 — Remove re-exports from `__init__.py`:** the 2 import-block lines (~:46 `set_active_session_id`, ~:47 `get_active_session_id`) and the 2 `__all__` entries (~:213, ~:220). **KEEP** run-identity/write-mode/generator imports + `__all__` entries. (`_SESSION_ID_SENTINEL`/`_resolve_session_id` are not re-exported — no `__init__` change for them.)
- [ ] **Step 10.5 — Cosmetic (optional):** update the stale prose in `_db_operations.py:~115,~522` ("set via set_active_session_id or pass explicitly") → "(pass explicitly)". Not required for correctness.
- [ ] **Step 10.6 — Verify + commit.** `test_adr046_p5_global_session_retired.py` green; then full suites: `PYTHONPATH=... pytest tests/unit tests/integration tests/smoke -q` (modulo the known pre-existing stale-`.so` `video_code_family` failure). `ruff check javdb apps` clean on touched files. Importer integrity: `python -c "import javdb.storage.db, javdb.spider.app.run_service, javdb.spider.detail.runner, javdb.integrations.rclone.manager.service, javdb.integrations.pikpak.bridge.service, javdb.ops.sentinel.service"`. Commit `refactor(storage): delete the ambient session-id global (ADR-046 P5)`.

---

## Migration order & dependencies

1. **Tasks 1–8** are mostly independent (each migrates one module's readers; the global persists, so the tree stays green). Suggested order: 1 (owner+detail, hardest) → 7 (align, self-contained) → 4 (pikpak) → 5 (email) → 6 (sentinel) → 2 (rclone) → 3 (dedup) → 8 (legacy).
2. **Task 9** after Tasks 2 + 3 (removes the wrapper its callers used).
3. **Task 10** strictly LAST (after 1–9): deletes the accessors + re-exports; the lazy/function-local imports in legacy/pikpak/email/sentinel/history_manager must all be migrated first or they raise `ImportError` at branch-execution time.

## Out of Scope

- Run-identity (`get/set_active_run_identity`) and write-mode (`get/set_active_write_mode`) ambient state — NOT session-id; explicitly KEPT (DD-4).
- `javdb/infra/csv_writer.py`'s own `_active_session_id` — separate mechanism, untouched.
- Privatizing the `db_*` facade — Phase 4 ([IMP-ADR046-04](IMP-ADR046-04-privatize-db-facade.md)).
- The `_db_session.py` lock rename — cosmetic, deferred.

## Self-Review

- Scope honored: only the session-id accessors deleted; the shared lock + run-identity + write-mode + generators kept (verified `_resolve_session_id`/`_SESSION_ID_SENTINEL` have no other callers).
- D2 satisfied: every migrated reader resolves session from an explicit param/attribute; the detail path uses pure parameter threading (DD-1, the chosen fork).
- Green at every commit: the global is deleted LAST (DD-5); nullable-table writes stay non-raising when session-less (DD-3, the Phase-2 contract).
- Highest risk is Task 1 (caller hop count for `save_parsed_movie_to_history`) — Step 1.1 mandates enumerating all callers before threading.
