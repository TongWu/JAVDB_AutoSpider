# D1 Access Port — Phase 5: Finish the Recovery Facade

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the last backend-identity reads from the business layer. Two commit-path helpers still call `current_backend()` and reach around the connection facade to do D1 recovery; route them through facade methods (duck-typed, like the existing `flush()`) so business code expresses *intent*, not backend identity. This finishes the ADR-010 D1/D2 boundary ("the port owns transport, reliability, batching, recovery").

**Architecture:** Add `assert_recovery_drained` + a residue-cleanup method to `D1Connection` / `DualConnection` (mirroring the existing `flush` facade). Rewrite `_assert_no_blocking_d1_recovery` and `_d1_retry_pending_cleanup` to duck-type on those methods (`getattr(conn, "...", None)`) instead of branching on `current_backend()`. SQLite connections simply don't expose the methods, so the duck-typed caller skips them — exactly the `hasattr(conn, "flush")` pattern already at `_db_history_write.py:108,134`.

**Why this is a follow-up IMP, not a new ADR:** This makes no new decision — it completes ADR-010's already-accepted D1/D2 port boundary. The broader "abstract over sqlite/d1/dual" idea from the architecture review (Candidate F) was assessed and **rejected**: routing is already encapsulated in `_get_connection`, the remaining leak is only these two helpers, and the rest (logging strings, d1-feature guards) evaporates when the SQLite mirror is retired. Only this reduction is worth doing.

**Tech Stack:** Python 3.11+, pytest. Single repo. No schema change.

**Related:** [ADR-010](ADR-010-d1-access-port.md) (D1/D2 — the port owns recovery)

**Status:** Proposed

---

## Scope

- **In:** facade methods on `D1Connection`/`DualConnection`; rewrite the two helpers to duck-type; rewrite the one test that mocks `current_backend`.
- **Out (assessed, deliberately skipped):**
  - **Logging sites** — `dedup.py:170`, `pikpak/bridge.py:809`, `qb/uploader.py:713`, `rclone/manager.py:325`, `notify/email.py:2232` interpolate `current_backend()` into log strings only. Harmless; they vanish with SQLite retirement. Not touched.
  - **The seam itself** — `_get_connection` / `_backend_mode` (`_db_connection.py`), `verify_d1_schema_versions`, `_db_migrations.py` init routing. This is the backend abstraction; leave it.
  - **MovieClaim d1-feature guards** — `spider/runtime/context.py:381`, `state.py:1051` (`if storage_backend() != "d1": return`). These are correct and grow in importance under D1-only; do not touch.
  - No `Backend` enum / strategy object (ADR-010 rejected expanding the abstraction).

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Modify | `javdb/storage/d1_client.py` | Add `assert_recovery_drained(*, ordering_key)` + residue-cleanup method (near `flush`, `:330`) |
| Modify | `javdb/storage/dual_connection.py` | Mirror both methods (delegate to the D1 side; `:1034` near `flush`) |
| Modify | `javdb/storage/db/_db_history_write.py` | Rewrite `_assert_no_blocking_d1_recovery` (`:114-129`) + `_d1_retry_pending_cleanup` (`:1401-1441`) to duck-type; drop the `current_backend()` reads/import |
| Modify | `tests/unit/test_d1_recovery_commit_gate.py` | Stop monkeypatching `current_backend` (`:21-25`); use a fake conn exposing/omitting the facade methods |

---

## Task 1: Add the facade methods

- [ ] `D1Connection.assert_recovery_drained(self, *, ordering_key)` (`d1_client.py`, near `flush` at `:330`): the outbox-status check currently inlined at `_db_history_write._assert_no_blocking_d1_recovery:121-129` — inspect the recovery outbox for blocking work and raise if found.
- [ ] A residue-cleanup method (e.g. `drain_recovery_residue(self, *, ordering_key)` or fold into the existing `drain_recovery`, `d1_port.py:305`) wrapping the cleanup UPDATE/DELETE pair from `_d1_retry_pending_cleanup:1419-1430`. **Preserve its lifecycle:** that helper deliberately opens a *separate* `make_d1_connection` because the surrounding `with _get_db(...)` block has already committed/closed — the facade method opens its own port connection internally.
- [ ] Mirror both on `DualConnection` (`dual_connection.py:1034`, delegate to the D1 side).
- [ ] **Do NOT** add them to `sqlite3.Connection` — absence is the signal, consistent with `hasattr(conn, "flush")` (`_db_history_write.py:108`).

## Task 2: Rewrite the two helpers to duck-type

- [ ] `_assert_no_blocking_d1_recovery` (`:114-129`): open the connection, `fn = getattr(conn, "assert_recovery_drained", None); if fn: fn(ordering_key=...)`. **Delete** the `current_backend() not in ("d1","dual")` guard (`:119`) and the `current_backend` import.
  - **Caveat:** if the port instance does not already hold the outbox handle the check needs, keep the helper reading `recovery_outbox_path()` (same path the port writes) but swap *only* the `current_backend()` guard for `hasattr(conn, "flush")` as the "is-this-a-D1-conn" signal. Either way, the `current_backend()` read is gone.
- [ ] `_d1_retry_pending_cleanup` (`:1401-1441`): same duck-typed pattern via the residue-cleanup facade method; drop the `make_d1_connection` re-open from the business layer (it moves inside the facade method) and the `current_backend()` check (`:1411`).
- [ ] `_flush_pending_d1_batch` (`:132-136`) is already correct (`getattr(conn, "flush", None)`) — leave it as the reference pattern. Call sites (`:1524,1533-1534,1634-1635,1651`) are unchanged.

## Task 3: Rewrite the test to stop mocking the backend

- [ ] `tests/unit/test_d1_recovery_commit_gate.py:21-25`: replace `monkeypatch.setattr(db_connection, "current_backend", lambda: "d1")` with a **fake connection** that exposes (or omits) `assert_recovery_drained` — the concrete "tests stop mocking `STORAGE_BACKEND`" deliverable. The gate behavior (raise when recovery is blocked) is now driven by the facade method, not by a backend string.

## Task 4: Verification gates

- [ ] `grep -n "current_backend" javdb/storage/db/_db_history_write.py` returns **nothing** (the business layer no longer reads backend identity).
- [ ] `pytest tests/unit/test_d1_recovery_commit_gate.py tests/unit/test_commit_session_bulk.py tests/unit/test_d1_dual.py tests/unit/test_d1_port.py -q` — green (ADR-010 anchors).
- [ ] Full storage suite green; a sqlite-mode commit and a (mocked) d1-mode commit both behave as before.
- [ ] Update this IMP's `Status` to `Completed`; check off `IMP-ADR010-05` in the ADR-010 roadmap.

## Risks

- **Outbox path vs connection handle** (Task 2 caveat) — verify `D1AccessPort` owns/exposes the recovery outbox the gate inspects; if not, use the `hasattr(conn,"flush")` fallback and keep reading `recovery_outbox_path()`. Either path removes the `current_backend()` read.
- **Separate-connection lifecycle** in `_d1_retry_pending_cleanup` — the facade method must open its own port connection (the caller's `with` block is already closed), or the cleanup runs on a stale/closed handle.
