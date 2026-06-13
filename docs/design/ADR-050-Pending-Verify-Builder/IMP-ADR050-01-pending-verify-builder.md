# IMP-ADR050-01: Consolidate the `pending_session_verify` Builder — Implementation Plan

> **Status: 🔲 Proposed (2026-06-13).** Single PR; pure relocation, no behaviour change. Authored from [ADR-050](ADR-050-pending-verify-record-builder.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-050](ADR-050-pending-verify-record-builder.md) — Phase 1 (the only phase).

**Goal:** Replace three independent `pending_session_verify` record builders with one pure `build_pending_verify_record()` + an exported field-name vocabulary, bound by all three emitters and the `log_analysis` consumer — with byte-identical JSONL output.

**Architecture / approach:** Extract the shared 17-field core; the three emitters become thin delegations supplying source-specific inputs. The builder is pure (callers pass pre-fetched `stats` and `shadow_audit_result`); `GITHUB_OUTPUT` and shadow-audit stay in the CLI. The consumer binds to exported `F_*` constants so a rename can't silently break the alert.

**Tech Stack:** Python 3, `pytest`, JSONL (`reports/D1/d1_drift.jsonl`), `lifecycle_helpers.append_jsonl_record`.

**Verification posture:** Output equivalence — capture the JSONL emitted by each of the three paths before and after; assert key-for-key identity on a fixture session. The existing emit/parse tests are the regression gate.

---

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/storage/sessions/pending_verify.py` | **Create** | `build_pending_verify_record(...) -> dict` (pure) + `F_*` field-name constants (or a `FIELDS` namespace). The single source of the record schema. |
| `apps/cli/db/commit_session.py` | Modify | Delete `_emit_pending_verify` (L239–309); each of the 4 call sites (L408–412, L450–461, L471–479, L500–508) builds via `build_pending_verify_record(..., source='commit_session', shadow_audit_result=<computed>)` then `append_jsonl_record(record)` + `write_github_output(...)`. Shadow-audit (`_shadow_audit_drift`) stays here. |
| `javdb/storage/sessions/commit.py` | Modify | Delete `_emit_commit_metrics` (L135–192); its call site (L332) builds via `build_pending_verify_record(..., source='commit_session_lib', stats=<dict or {}>, shadow_audit_result=None)` then `append_jsonl_record(record)`. |
| `javdb/storage/rollback/core.py` | Modify | Delete `_emit_pending_verify_for_session` (L182–251); the 3 call sites (L393–400, L408–415, L431–437) build via `build_pending_verify_record(..., source='rollback', rollback_extras={...})`; the `pre_write_mode != pending` guard moves to the call site. |
| `javdb/integrations/notify/email/log_analysis.py` | Modify | `_CRITICAL_ALERT_FIELDS` (L986–990) becomes a tuple of imported `F_*` constants; `_evaluate_pending_alerts` (L1044–1055) uses them. No behaviour change. |
| `tests/unit/test_pending_verify_builder.py` | **Create** | Unit-test the builder for all three `source` paths (commit / commit_lib / rollback): shared core fields present, source-specific extras merged, `shadow_audit_enabled` reflects input. |
| `tests/unit/test_commit_session_events.py` | Modify | The `_emit_pending_verify` monkeypatch (L45) re-targets the builder or `append_jsonl_record`. |
| `CONTEXT.md` | Modify | Add **Pending Verify Record** + **Pending Verify Builder** (ADR-050 Domain Language). |

## Task 0: Baseline & schema capture

- [ ] **Step 0.1 — Green baseline:** `pytest tests/unit/test_commit_session_events.py tests/unit/test_rollback_pending_mode.py tests/integration/test_sessions_endpoints.py -q`.
- [ ] **Step 0.2 — Capture the three records.** Run a fixture session through each path (commit success, commit failure, rollback) and save the emitted JSONL lines as the equivalence golden. Enumerate the exact field set each source emits (confirm the 17-field core + each source's extras).

  **Verification gate:** the three golden records are captured; the field inventory matches ADR-050 (17 core; lib emitter's 19; rollback extras `rollback_mode`/`cleanup_path_mismatch_count`).

## Task 1: Author the builder + constants (red→green)

- [ ] **Step 1.1 — `pending_verify.py`:** define every `F_*` field-name constant and `build_pending_verify_record(...)` assembling the core + `source`-conditional extras + merged `rollback_extras`, `shadow_audit_result`. Pure — no imports of `HistoryRepo`/`get_db`.
- [ ] **Step 1.2 — `test_pending_verify_builder.py`:** assert the builder reproduces each Task 0.2 golden when given that source's inputs.

  **Verification gate:** `pytest tests/unit/test_pending_verify_builder.py -q` green; builder output == golden for all three sources.

## Task 2: Re-point the three emitters

- [ ] **Step 2.1 — `commit_session.py`:** delete `_emit_pending_verify`; the 4 call sites delegate + keep `append_jsonl_record` and `write_github_output`; shadow-audit computed at the call site and passed as `shadow_audit_result`.
- [ ] **Step 2.2 — `sessions/commit.py`:** delete `_emit_commit_metrics`; the call site delegates with `stats` pre-fetched (try/except → `{}`), `shadow_audit_result=None`.
- [ ] **Step 2.3 — `rollback/core.py`:** delete `_emit_pending_verify_for_session`; the 3 call sites delegate with `rollback_extras`; move the `pre_write_mode != pending` guard out to the call sites.

  **Verification gate:** `pytest tests/unit/test_commit_session_events.py tests/unit/test_rollback_pending_mode.py -q` green; the emitted JSONL matches the Task 0.2 golden key-for-key.

## Task 3: Bind the consumer + docs

- [ ] **Step 3.1 — `log_analysis.py`:** `_CRITICAL_ALERT_FIELDS` and `_evaluate_pending_alerts` import and use the `F_*` constants.
- [ ] **Step 3.2 — `test_commit_session_events.py`:** re-target the monkeypatch.
- [ ] **Step 3.3 — CONTEXT.md:** add the two terms.

  **Verification gate:** `grep -n "from javdb.storage.sessions.pending_verify import" javdb/integrations/notify/email/log_analysis.py` non-empty; `pytest -k "log_analysis or pending_alert" -q` green.

## Task 4: Final gates

- [ ] `pytest tests/unit tests/integration -k "commit or rollback or pending or sessions or log_analysis" -q` green.
- [ ] `grep -rn "_emit_pending_verify\b\|_emit_commit_metrics\b\|_emit_pending_verify_for_session\b" javdb apps` → empty (all three deleted).
- [ ] JSONL equivalence: a fresh fixture run's `d1_drift.jsonl` lines match the Task 0.2 golden.
- [ ] `ruff check javdb/storage/sessions apps/cli/db/commit_session.py javdb/storage/rollback/core.py javdb/integrations/notify/email/log_analysis.py` clean.

## Rollback

Pure refactor; revert the PR. No data/schema/D1 change; the JSONL path and write class (diagnostic, ADR-042) are unchanged.

## Out of Scope

- Changing what/when/where the record is written.
- The `PipelineEvent` spine (`_emit_event`, ADR-036).
- Promoting the record to a typed return (stays a dict).
