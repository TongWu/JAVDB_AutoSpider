# IMP-ADR053-01: Type `D1TransientError`'s Recovery Signals + `D1RecoveryBlockerError` — Implementation Plan

> **Status: ✅ Completed (2026-06-14).** Single PR; behaviour-preserving (representation change only). Authored from [ADR-053](ADR-053-d1-transient-error-typed-recovery.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-053](ADR-053-d1-transient-error-typed-recovery.md) — Phase 1 (the only phase).

**Goal:** Replace the five monkey-patched recovery signals on `D1TransientError` with declared typed fields, introduce `D1RecoveryBlockerError(RuntimeError)` for the blocker case, convert all `getattr`/string-match reads to attribute/`isinstance`, and delete the 8 `# type: ignore[attr-defined]` suppressions — with no behaviour change.

**Architecture / approach:** The signals keep their meanings, set points, and read decisions. `outbox_required`/`durable`/`is_export_lock`/`retry_after` become class-level typed fields with immutable defaults on `D1TransientError` (set post-construction, as today). `d1_recovery_blocker` becomes a dedicated `D1RecoveryBlockerError` subclass (it is raised on a bare `RuntimeError`). Reads switch to `isinstance` + attribute; the fragile `str(exc)` blocker fallback is deleted since the new subclass is the sole raise site.

**Tech Stack:** Python 3, `pytest`, `mypy`/`ruff` (the `# type: ignore` removals must stay clean).

**Verification posture:** Behaviour-preserving — existing D1/dual/recovery/backoff tests stay green; add unit tests that construct the typed signals and assert the recovery/blocker decisions. The type checker (no `# type: ignore`) is an additional gate.

---

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/storage/d1_client.py` | Modify | Add the four typed fields to `D1TransientError` (`d1_recovery_outbox_required: bool = False`, `d1_recovery_durable: bool = False`, `is_export_lock: bool = False`, `retry_after: Optional[str] = None`). Add `class D1RecoveryBlockerError(RuntimeError)` (alongside `D1Error`). Export both as needed. |
| `javdb/storage/d1_port.py` | Modify | `:259-263` raise `D1RecoveryBlockerError(...)` instead of monkey-patching a bare `RuntimeError`. Remove `# type: ignore[attr-defined]` from the assignments at `:292`, `:293`, `:448`, `:459`, `:483`, `:596`, `:601`, `:608` (now declared). `:490`/`:507` read `exc.retry_after`/`exc.is_export_lock` directly (still within `D1TransientError`-typed scope). |
| `javdb/storage/dual_connection.py` | Modify | `_requires_durable_recovery:1122-1127` → `isinstance(exc, D1TransientError) and exc.d1_recovery_outbox_required and not exc.d1_recovery_durable`. `_blocks_queued_recovery_flush:1129-1136` → `isinstance(exc, D1RecoveryBlockerError)`; **delete** the `"unresolved D1 recovery work" in str(exc)` fallback. Import `D1TransientError`/`D1RecoveryBlockerError`. |
| `tests/unit/test_d1_port.py` / `tests/unit/test_d1_dual.py` (or nearest) | Modify/Create | Unit-test: a `D1TransientError` with `d1_recovery_outbox_required=True, d1_recovery_durable=False` triggers `_requires_durable_recovery`; a `D1RecoveryBlockerError` triggers `_blocks_queued_recovery_flush` and a plain `RuntimeError` with the old message does **not** (proving the fallback removal is intentional); `retry_after`/`is_export_lock` drive backoff as before. |
| `CONTEXT.md` | Modify | Add **D1 recovery signals** + **`D1RecoveryBlockerError`** (ADR-053 Domain Language). |

## Task 0: Baseline & site enumeration

- [x] **Step 0.1 — Green focused baseline:** `pytest tests/unit/test_d1_port.py tests/unit/test_d1_dual.py -q`.
  The broader `pytest tests/unit -k "d1 or dual or recovery or backoff or outbox" -q`
  remains unsuitable for this task because local `javdb.rust_core` lacks the proxy
  Rust symbols during unrelated test collection.
- [x] **Step 0.2 — Enumerate every set/read site + the ignores:**
  ```bash
  grep -rn "d1_recovery_outbox_required\|d1_recovery_durable\|d1_recovery_blocker\|is_export_lock\|retry_after\|unresolved D1 recovery work\|type: ignore\[attr-defined\]" javdb/storage tests --include="*.py"
  ```
  **Verification gate:** the set matches ADR-053 (6 cross-module + 2 intra-module set sites; 3 cross-module reads; the one `str(exc)` fallback). If a set/read site exists outside `d1_client`/`d1_port`/`dual_connection`, STOP and reconcile.

## Task 1: Declare the typed members (`d1_client.py`)

- [x] **Step 1.1 — Four fields on `D1TransientError`** with immutable defaults (per ADR-053 D1).
- [x] **Step 1.2 — `class D1RecoveryBlockerError(RuntimeError)`** with a docstring, beside `D1Error`.

  **Verification gate:** `python -c "from javdb.storage.d1_client import D1TransientError, D1RecoveryBlockerError; e=D1TransientError('x'); print(e.d1_recovery_outbox_required, e.d1_recovery_durable, e.is_export_lock, e.retry_after)"` → `False False False None`.

## Task 2: Convert the set sites (`d1_port.py`)

- [x] **Step 2.1 — Blocker:** `:259-263` → `raise D1RecoveryBlockerError("unresolved D1 recovery work for ordering key {key}; drain it before flushing queued writes")`.
- [x] **Step 2.2 — Drop the `# type: ignore[attr-defined]`** from the 6 cross-module + 2 intra-module assignments (`:292`, `:293`, `:448`, `:459`, `:483`, `:596`, `:601`, `:608`) — now declared fields.
- [x] **Step 2.3 — Intra-module reads** (`_compute_backoff:490`, `:507`) read `exc.retry_after`/`exc.is_export_lock` directly (drop the `getattr`).

  **Verification gate:** `ruff check javdb/storage/d1_port.py` clean; `grep -n "type: ignore" javdb/storage/d1_port.py` shows none for these attrs; `pytest tests/unit -k "backoff or export_lock or d1_port" -q` green.

## Task 3: Convert the reads + delete the fallback (`dual_connection.py`)

- [x] **Step 3.1 — `_requires_durable_recovery`** → `isinstance(exc, D1TransientError) and exc.d1_recovery_outbox_required and not exc.d1_recovery_durable`.
- [x] **Step 3.2 — `_blocks_queued_recovery_flush`** → `self._d1_queued_pending_writes > 0 and isinstance(exc, D1RecoveryBlockerError)`; **delete** `"unresolved D1 recovery work" in str(exc)`.
- [x] **Step 3.3 — Imports:** add `D1TransientError`, `D1RecoveryBlockerError` to the `d1_client` import.

  **Verification gate:** `grep -n "getattr(exc, \"d1_recovery\|unresolved D1 recovery work" javdb/storage/dual_connection.py` → empty; `python -c "import javdb.storage.dual_connection"` ok.

## Task 4: Tests + docs

- [x] **Step 4.1 — Recovery-signal tests:** construct `D1TransientError` with the fields set and assert `_requires_durable_recovery`; construct `D1RecoveryBlockerError` and assert `_blocks_queued_recovery_flush`; assert a plain `RuntimeError` carrying the old message is **no longer** treated as a blocker (pins the fallback removal).
- [x] **Step 4.2 — Backoff:** `retry_after`/`is_export_lock` still drive `_compute_backoff` as before.
- [x] **Step 4.3 — CONTEXT.md:** add the two terms.

  **Verification gate:** new tests green.

## Task 5: Final gates

- [x] `pytest tests/unit/test_d1_port.py tests/unit/test_d1_dual.py -q` green.
- [x] `grep -rn "type: ignore\[attr-defined\]" javdb/storage/d1_port.py javdb/storage/dual_connection.py` → none for the recovery signals.
- [x] `grep -rn "unresolved D1 recovery work" javdb/storage` → only `D1RecoveryBlockerError` messages (no `str(exc)` match).
- [x] `ruff check javdb/storage/d1_client.py javdb/storage/d1_port.py javdb/storage/dual_connection.py` clean.
- [x] `git diff --stat`: typed fields + subclass added; 8 ignores removed; string fallback deleted.

## Rollback

Pure refactor; revert the PR. No data/schema/D1 change; recovery policy (ADR-042) and classification (ADR-009) untouched.

## Out of Scope

- Recovery policy / backoff behaviour (ADR-042) — typed inputs, same decisions.
- Transient/permanent classification (ADR-009).
- A `D1RecoveryState` dataclass (declared fields suffice).
