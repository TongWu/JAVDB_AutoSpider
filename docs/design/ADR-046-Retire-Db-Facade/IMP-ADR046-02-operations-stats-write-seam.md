# IMP-ADR046-02: ADR-046 Phase 2 — Operations/Stats Write Seam (session-bound) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-046](ADR-046-retire-db-facade.md) — **Phase 2 (re-scoped)**. Depends on Phase 1 ([IMP-ADR046-01](IMP-ADR046-01-history-write-seam.md)) for the `_require_session` / constructor-`session_id` pattern. **Scope was narrowed (see ADR-046 roadmap amendment):** this phase only binds the session on `OperationsRepo`/`StatsRepo` writes; **deleting the global session machinery moved to a new Phase 5** (the global has ~17 readers across the codebase — too large to be a tail of this phase).

**Goal:** Make `OperationsRepo`/`StatsRepo` session-tagging writes resolve `session_id` explicitly (explicit arg > constructor-bound > raise), removing the lone ambient `get_active_session_id()` read in `OperationsRepo.replace_rclone_inventory`, and thread an explicit session from their callers.

**Architecture:** Mirror Phase 1's `HistoryRepo` exactly — add `session_id` to the repo constructor + a `_require_session(explicit=None)` helper, and route session-tagging writes through it. **`StatsRepo` already takes `session_id` explicitly on every method (no global)** — it needs only an optional constructor param for API symmetry; its writes are otherwise unchanged. The process-global stays in place (Phase 5 deletes it once all ~17 readers migrate).

**Tech Stack:** Python 3 + `pytest`. Test command (broken venv): `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <files> -q`. Branch off this Phase-2 worktree; commit with `git -c user.name=Ted -c user.email=ted@wu.engineer` + the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

---

## File Structure

| Path | Modify/Test | Responsibility |
| --- | --- | --- |
| `javdb/storage/repos/operations_repo.py` | Modify | Add ctor `session_id` + `_require_session`; route `replace_rclone_inventory` (and the session-tagging writes) through it |
| `javdb/storage/repos/stats_repo.py` | Modify | Add optional ctor `session_id` for symmetry; writes already explicit (no behavior change) |
| `tests/unit/test_adr046_p2_operations_session.py` | **Create** | Pin OperationsRepo session resolution + guard |
| (caller modules — discovered in Task 3) | Modify | Construct `OperationsRepo(session_id=sid)` instead of relying on the global |

---

## Task 1: `OperationsRepo` — constructor session + `_require_session` + fix the global read

**Files:** `javdb/storage/repos/operations_repo.py`; create `tests/unit/test_adr046_p2_operations_session.py`.

- [ ] **Step 1.1 — Failing test.** Create `tests/unit/test_adr046_p2_operations_session.py`:
```python
"""ADR-046 Phase 2: OperationsRepo resolves session explicitly (arg > bound >
raise); replace_rclone_inventory no longer reads the process-global."""
import inspect
import pytest
from javdb.storage.repos.operations_repo import OperationsRepo

_SID = "20260603T000000.000000Z-aaaa-0000"


def test_require_session_order():
    assert OperationsRepo(session_id=_SID)._require_session() == _SID
    assert OperationsRepo(session_id=_SID)._require_session("OTHER") == "OTHER"
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo()._require_session()


def test_replace_rclone_inventory_no_longer_reads_global():
    src = inspect.getsource(OperationsRepo.replace_rclone_inventory)
    assert "get_active_session_id" not in src


def test_replace_rclone_inventory_without_session_raises():
    with pytest.raises(RuntimeError, match="requires a session_id"):
        OperationsRepo().replace_rclone_inventory([])
```
Run → FAIL. (`PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_adr046_p2_operations_session.py -q`)

- [ ] **Step 1.2 — Add ctor + helper** (copy Phase 1's `HistoryRepo` shape verbatim). Replace:
```python
    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path
```
with:
```python
    def __init__(
        self, *, db_path: Optional[str] = None, session_id: Optional[str] = None,
    ) -> None:
        self._db_path = db_path
        self._session_id = session_id

    def _require_session(self, explicit: Optional[str] = None) -> str:
        """Resolve a write session: explicit arg > bound session > raise (ADR-046)."""
        sid = explicit if explicit is not None else self._session_id
        if not sid:
            raise RuntimeError(
                "OperationsRepo write requires a session_id "
                "(pass it, or bind via OperationsRepo(session_id=...))"
            )
        return sid
```

- [ ] **Step 1.3 — Fix the lone global read.** In `replace_rclone_inventory`, replace:
```python
        from javdb.storage.db import get_active_session_id
        from javdb.storage.db._db_operations import db_replace_rclone_inventory
        return db_replace_rclone_inventory(
            entries=entries,
            db_path=self._db_path,
            session_id=get_active_session_id(),
        )
```
with:
```python
        from javdb.storage.db._db_operations import db_replace_rclone_inventory
        return db_replace_rclone_inventory(
            entries=entries,
            db_path=self._db_path,
            session_id=self._require_session(),
        )
```

- [ ] **Step 1.4 — Route the explicit session-tagging writes through the NON-RAISING resolver.** For the writes that accept `session_id=None` and forward it (`append_dedup_record`, `append_pikpak_history`, `mark_records_deleted`, `mark_orphan_records`, `upsert_align_no_exact_match`), change `session_id=session_id` → `session_id=self._resolve_session(session_id)` — resolution **explicit > bound > None**, which **never raises**. **These tables' `SessionId` columns are NULLABLE** (`DedupRecords`, `PikpakHistory`), and standalone jobs (WeeklyDedup, ad-hoc PikPak) legitimately write **session-less** — a raising guard here would crash them / silently drop rows. So use the non-raising `_resolve_session`, **NOT** a mandatory `_require_session`. *(As-built correction, 2026-06-03: `OperationsRepo` uses `_resolve_session` for ALL its session-tagging writes — including `replace_rclone_inventory` (Step 1.3) — and carries **no** `_require_session`. The earlier draft of Steps 1.2–1.4 said `_require_session`; that was an over-reach that a quality review caught and reverted, since these columns are nullable.)* **Do NOT touch** the non-tagging writes (`clear_rclone_inventory`, `append_rclone_inventory` — `RcloneInventory` has no `SessionId` column) or the already-explicit staging writes (`open/append/merge/drop_rclone_staging`, `swap_rclone_inventory`). Tests: a session-less nullable-table write must **NOT** raise and must persist with `SessionId` NULL (see `tests/unit/test_adr046_p2_session_less_writes.py`).

- [ ] **Step 1.5 — Run, verify PASS.** `pytest tests/unit/test_adr046_p2_operations_session.py tests/unit/test_operations_repo.py -q` → PASS (update any `test_operations_repo.py` case that called a session-tagging write with no session).

- [ ] **Step 1.6 — Commit.**
```bash
git add javdb/storage/repos/operations_repo.py tests/unit/test_adr046_p2_operations_session.py
git -c user.name=Ted -c user.email=ted@wu.engineer commit -m "refactor(storage): OperationsRepo session-bound writes, drop global read (ADR-046 P2)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `StatsRepo` — optional ctor session for symmetry (no behavior change)

**File:** `javdb/storage/repos/stats_repo.py`.

- [ ] **Step 2.1 — Confirm StatsRepo is already explicit.** Read it: every `save_*`/`get_*` method takes `session_id: str` positionally; there is NO `get_active_session_id()` in `stats_repo.py` or `_db_stats.py`. So there is no global to remove here.
- [ ] **Step 2.2 — Add an optional ctor `session_id`** (API symmetry with History/Operations) and let the `save_*` methods accept the bound session when the positional arg is omitted — ONLY if it does not break existing callers (they all pass `session_id` positionally today, so this is additive). Minimal version: add `session_id: Optional[str] = None` to `__init__` and store it; leave the methods' required positional `session_id` as-is. (Do **not** force `_require_session` on StatsRepo unless a caller actually needs the bound-session affordance — YAGNI.)
- [ ] **Step 2.3 — Commit** (if any change): `git -c ... commit -m "refactor(storage): StatsRepo optional ctor session_id for repo-API symmetry (ADR-046 P2)"`. If Step 2.2 concludes no change is warranted, record that in the task report and skip the commit.

---

## Task 3: Thread the explicit session at the callers of the now-session-bound writes

- [ ] **Step 3.1 — Find the callers** that relied on the global for `replace_rclone_inventory` (and any session-tagging Operations write):
```bash
grep -rn "OperationsRepo(" javdb apps --include='*.py' | grep -v "OperationsRepo(session_id\|OperationsRepo(\*, "
grep -rn "\.replace_rclone_inventory(" javdb apps --include='*.py'
```
Expected: the rclone manager service (`javdb/integrations/rclone/manager/service.py`) is the primary caller; it currently sets the global (or relies on it being set). For each caller, construct `OperationsRepo(session_id=<the session in scope>)`. The rclone service already resolves a session via `SessionLifecycleRepo().get_active_session_id()` (e.g. `service.py:598,1205,1408`) — pass that value into the repo constructor instead of relying on the ambient global inside the repo.

- [ ] **Step 3.2 — Thread + test.** Update each caller to pass `session_id=`. Add/adjust a focused test per caller (monkeypatch `OperationsRepo` to capture the bound `session_id`, mirroring Phase 1's `finalize_detail_phase` test). Run the rclone manager tests: `pytest tests/unit/test_rclone_manager.py -q` → green (fix any that relied on the global).

- [ ] **Step 3.3 — Commit.** `git add` the touched caller(s) + tests; commit `refactor(rclone): thread explicit session into OperationsRepo writes (ADR-046 P2)`.

---

## Task 4: Verification

- [ ] **Step 4.1 — The global read is gone from OperationsRepo.** `grep -n "get_active_session_id" javdb/storage/repos/operations_repo.py` → no matches. (StatsRepo had none.)
- [ ] **Step 4.2 — Targeted + full unit suites.** `pytest tests/unit/test_adr046_p2_operations_session.py tests/unit/test_operations_repo.py tests/unit/test_stats_repo.py tests/unit/test_rclone_manager.py -q` green; then `pytest tests/unit -q` (modulo the known pre-existing failures noted in Phase 1).
- [ ] **Step 4.3 — Confirm the global still exists (NOT deleted here).** `_db_session.py`'s `get_active_session_id` etc. remain — Phase 5 deletes them once the remaining ~17 readers migrate. This phase only removed *OperationsRepo*'s dependence on it.

## Out of Scope (→ Phase 5 or later)

- Deleting the global session machinery (`_db_session.py`) — Phase 5 (migrate the ~17 readers: spider runtime, rclone, pikpak, notify, sentinel, align tool, `SessionLifecycleRepo.get_active_session_id`).
- Rerouting commit/rollback (Phase 3); privatizing `db_*` (Phase 4).

## Self-Review

- Scope honored: only `OperationsRepo` had an ambient read (`replace_rclone_inventory`); `StatsRepo` was already explicit (Task 2 is near-no-op). The global is **not** deleted (Phase 5).
- Pattern: identical to Phase 1's `HistoryRepo` (`_require_session`, ctor `session_id`).
