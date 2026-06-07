# IMP-ADR009-03: ADR-009 Phase 2 - Guarded Drift Apply

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value |
| ----------- | ----- |
| **Status**  | Completed |
| **Date**    | 2026-05-24 |
| **Phase**   | P2 |
| **Related** | [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md) |

**Goal:** Add the operator-gated `--apply --session-id <id>` path that deletes only safe D1-side orphan pending rows after re-diagnosis.

**Architecture:** Build on the read-only classifier from [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md). The apply path reuses the same verdict logic, refuses every non-`SAFE_TO_APPLY` state, executes fixed DELETE predicates, and appends an audit JSONL record.

**Tech Stack:** Python 3.11+, argparse, sqlite3-shaped D1 facade, JSONL audit records, pytest.

**Source spec:** [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), D5.

**Completion note:** This branch already contains later P3 email integration work. P2 completion is scoped only to the guarded `--apply --session-id <id>` path: re-diagnosis at apply time, refusal of non-`SAFE_TO_APPLY` states, fixed pending-row DELETE predicates, `--max-deletes` protection, and drift-resolution audit evidence.

**Checkbox semantics for this closure pass:** Only steps rerun or directly verified during this closure pass are checked. Historical RED, TDD-authoring, implementation, and documentation-authoring steps remain unchecked when they were not re-run; current completion status is based on the checked closure verification and the Completion Evidence below.

---

## Files

| Path | Responsibility |
| --- | --- |
| `javdb/storage/drift_diagnose.py` | Guarded apply orchestration, safety rails, fixed DELETEs, audit write. |
| `apps/cli/db/drift_diagnose.py` | Exposes `--apply`, `--session-id`, and `--max-deletes` while delegating to storage. |
| `tests/unit/test_drift_diagnose.py` | Safety-rail and mutation-path coverage. |
| `apps/cli/db/README.md` | Documents the apply mode and exit codes. |
| `docs/handbook/en/ops/d1-rollback.md` | Operator SOP for diagnosis and manual apply. |
| `docs/handbook/zh/ops/d1-rollback.md` | Chinese mirror of the operator SOP. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md` | Records P2 status after implementation. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md` | Chinese mirror of P2 status. |

---

## Task 1: Verify P1 Baseline

- [ ] **Step 1: Run read-only diagnose tests.**

```bash
python3 -m apps.cli.db.drift_diagnose --help
pytest tests/unit/test_drift_diagnose.py -k "not apply" -v
```

Expected: help exits 0 and the read-only tests pass. If this fails, complete [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md) before starting P2.

Closure-pass note: baseline P1 was already closed by [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md). These exact baseline commands were not re-run during this P2 closure pass; the P2-focused apply and full drift-diagnose test file were run in Task 5 instead.

---

## Task 2: Add Safety-Rail Tests

**Files:**
- Modify: `tests/unit/test_drift_diagnose.py`

- [ ] **Step 1: Add tests for ADR-009 D5 rails.**

Closure-pass note: historical TDD-authoring step; not re-run during this closure pass. Existing safety-rail and mutation-path coverage was verified by the apply-focused and full drift-diagnose test runs in Completion Evidence.

Cover:

- `--apply` without `--session-id` exits 2;
- missing `ReportSessions` row exits 2;
- non-`committed` session exits 2;
- verdict other than `SAFE_TO_APPLY` exits 1;
- orphan count above `--max-deletes` exits 2;
- successful apply deletes only rows where `SessionId = ? AND ApplyState = 'pending'`;
- successful apply writes a `kind == "drift_resolution"` audit record.

Example assertion for the fixed DELETE invariant:

```python
rows = d1_history.execute(
    "SELECT SessionId, ApplyState FROM PendingTorrentHistoryWrites ORDER BY Seq"
).fetchall()
assert [dict(row) for row in rows] == [
    {"SessionId": "other", "ApplyState": "pending"},
    {"SessionId": "sid", "ApplyState": "applied"},
]
```

- [ ] **Step 2: Run apply tests and observe expected failure.**

```bash
pytest tests/unit/test_drift_diagnose.py -k "apply" -v
```

Expected: FAIL until `--apply` is implemented.

Closure-pass note: historical RED step; not re-run during this closure pass because the implementation already existed. The current apply tests pass, as recorded in Completion Evidence.

---

## Task 3: Implement Guarded Apply

**Files:**
- Modify: `javdb/storage/drift_diagnose.py`
- Modify: `apps/cli/db/drift_diagnose.py`

- [ ] **Step 1: Add apply arguments.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. The current arguments were verified through the passing apply-path tests in Completion Evidence.

Support:

```text
--apply
--session-id <SessionId>
--max-deletes <n>    default 100
```

- [ ] **Step 2: Re-run classification at apply time.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. The behavior is covered by the current apply-path tests in Completion Evidence.

The apply path must construct a single-session suspect and call the same classification logic used by diagnose mode. Never trust a stale suggested command from earlier output.

- [ ] **Step 3: Enforce apply exit codes.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Exit-code behavior is covered by the current apply-path tests in Completion Evidence.

Use ADR-009 apply semantics:

| Exit | Meaning |
| --- | --- |
| 0 | Apply succeeded. |
| 1 | Current verdict is not `SAFE_TO_APPLY`. |
| 2 | Argument error, missing/non-committed session, unreadable status, or `--max-deletes` exceeded. |

- [ ] **Step 4: Execute only fixed DELETE predicates.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. The fixed DELETE invariant is covered by `test_apply_delete_sql_includes_sessionid_and_applystate` in the passing test runs recorded below.

The mutation SQL must be exactly scoped to the target session and pending state:

```sql
DELETE FROM PendingMovieHistoryWrites
WHERE SessionId = ? AND ApplyState = 'pending'
```

```sql
DELETE FROM PendingTorrentHistoryWrites
WHERE SessionId = ? AND ApplyState = 'pending'
```

- [ ] **Step 5: Append audit evidence.**

Closure-pass note: historical implementation step; not re-implemented during this closure pass. Audit-record behavior is covered by `test_apply_audit_record_format` in the passing test runs recorded below.

Append one JSONL record to the configured drift log:

```json
{
  "kind": "drift_resolution",
  "source": "drift_diagnose_apply",
  "session_id": "sid",
  "deleted_movie_orphans": 0,
  "deleted_torrent_orphans": 1,
  "verdict_at_apply": "SAFE_TO_APPLY"
}
```

Use the existing project JSONL helper if one is already available; otherwise write append-only UTF-8 JSONL with a trailing newline.

---

## Task 4: Update Operator Documentation

**Files:**
- Modify: `apps/cli/db/README.md`
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [ ] **Step 1: Document apply usage in the CLI README.**

Closure-pass note: historical documentation-authoring step; not re-authored during this closure pass. The current branch already has a `drift_diagnose` diagnose/apply row in the CLI README, so this step remains unchecked; P2 closure verifies operator-facing documentation through the paired handbook SOP update and token check in Completion Evidence.

Add:

```markdown
`--apply --session-id <id>` re-runs diagnosis and deletes only D1 orphan `Pending*` rows guarded by `SessionId = ? AND ApplyState = 'pending'`.
```

- [ ] **Step 2: Document operator commands.**

Closure-pass note: paired handbook SOPs were updated during this closure pass and verified by the token check in Completion Evidence.

English and Chinese SOPs must both include:

```bash
python3 -m apps.cli.db.drift_diagnose --since 24
python3 -m apps.cli.db.drift_diagnose --since 24 --json
python3 -m apps.cli.db.drift_diagnose --apply --session-id <SessionId>
```

Both SOPs must keep CLI names, env vars, JSON fields, and SQL predicates untranslated.

---

## Task 5: Verify P2

- [x] **Step 1: Run the guarded apply tests.**

```bash
pytest tests/unit/test_drift_diagnose.py -k "apply" -v
```

Expected: PASS.

- [x] **Step 2: Run the full ADR-009 drift diagnose test file.**

```bash
pytest tests/unit/test_drift_diagnose.py -v
```

Expected: PASS.

- [x] **Step 3: Verify docs pairing.**

```bash
python3 - <<'PY'
from pathlib import Path
en = Path("docs/handbook/en/ops/d1-rollback.md").read_text(encoding="utf-8")
zh = Path("docs/handbook/zh/ops/d1-rollback.md").read_text(encoding="utf-8")
for token in ["drift_diagnose", "SAFE_TO_APPLY", "--apply --session-id", "ApplyState = 'pending'"]:
    assert token in en, token
    assert token in zh, token
PY
```

Expected: exits 0.

Closure-pass result: PASS after paired handbook SOP update. The equivalent `/opt/anaconda3/bin/python -c '...'` token check found all expected P2 tokens in both `docs/handbook/en/ops/d1-rollback.md` and `docs/handbook/zh/ops/d1-rollback.md`.

- [x] **Step 4: Verify this IMP and paired handbook diff have no whitespace errors.**

```bash
git diff --check -- docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md docs/design/ADR-009-D1-Drift-Classifier/IMP-ADR009-03-drift-diagnose-guarded-apply.md
```

Expected: exits 0.

---

## Completion Evidence

Closure verification run on 2026-05-24:

```bash
pytest tests/unit/test_drift_diagnose.py -k "apply" -v
```

Result: PASS (`20 passed, 45 deselected in 0.30s`). The selected tests cover guarded apply argument validation, non-safe verdict refusal, committed-session requirements, `--max-deletes`, fixed pending-state DELETE scoping, successful apply, and drift-resolution audit records.

```bash
pytest tests/unit/test_drift_diagnose.py -v
```

Result: PASS (`65 passed in 0.69s`). The full ADR-009 drift diagnose unit file passed.

```bash
/opt/anaconda3/bin/python -c 'from pathlib import Path; en = Path("docs/handbook/en/ops/d1-rollback.md").read_text(encoding="utf-8"); zh = Path("docs/handbook/zh/ops/d1-rollback.md").read_text(encoding="utf-8"); tokens = ["drift_diagnose", "SAFE_TO_APPLY", "--apply --session-id", "ApplyState = '\''pending'\''"]; [(_ for _ in ()).throw(AssertionError(token)) for token in tokens if token not in en or token not in zh]'
```

Result: PASS after paired handbook SOP update.

```bash
git diff --check -- docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md docs/design/ADR-009-D1-Drift-Classifier/IMP-ADR009-03-drift-diagnose-guarded-apply.md
```

Result: PASS (exited 0 with no output).
