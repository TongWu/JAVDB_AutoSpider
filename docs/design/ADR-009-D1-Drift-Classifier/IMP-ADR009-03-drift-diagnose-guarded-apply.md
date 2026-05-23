# IMP-ADR009-03: ADR-009 Phase 2 - Guarded Drift Apply

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value |
| ----------- | ----- |
| **Status**  | Draft |
| **Date**    | 2026-05-24 |
| **Phase**   | P2 |
| **Related** | [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md) |

**Goal:** Add the operator-gated `--apply --session-id <id>` path that deletes only safe D1-side orphan pending rows after re-diagnosis.

**Architecture:** Build on the read-only classifier from [IMP-ADR009-02](IMP-ADR009-02-drift-diagnose-readonly-cli.md). The apply path reuses the same verdict logic, refuses every non-`SAFE_TO_APPLY` state, executes fixed DELETE predicates, and appends an audit JSONL record.

**Tech Stack:** Python 3.11+, argparse, sqlite3-shaped D1 facade, JSONL audit records, pytest.

**Source spec:** [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), D5.

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

---

## Task 2: Add Safety-Rail Tests

**Files:**
- Modify: `tests/unit/test_drift_diagnose.py`

- [ ] **Step 1: Add tests for ADR-009 D5 rails.**

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

---

## Task 3: Implement Guarded Apply

**Files:**
- Modify: `javdb/storage/drift_diagnose.py`
- Modify: `apps/cli/db/drift_diagnose.py`

- [ ] **Step 1: Add apply arguments.**

Support:

```text
--apply
--session-id <SessionId>
--max-deletes <n>    default 100
```

- [ ] **Step 2: Re-run classification at apply time.**

The apply path must construct a single-session suspect and call the same classification logic used by diagnose mode. Never trust a stale suggested command from earlier output.

- [ ] **Step 3: Enforce apply exit codes.**

Use ADR-009 apply semantics:

| Exit | Meaning |
| --- | --- |
| 0 | Apply succeeded. |
| 1 | Current verdict is not `SAFE_TO_APPLY`. |
| 2 | Argument error, missing/non-committed session, unreadable status, or `--max-deletes` exceeded. |

- [ ] **Step 4: Execute only fixed DELETE predicates.**

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

Add:

```markdown
`--apply --session-id <id>` re-runs diagnosis and deletes only D1 orphan `Pending*` rows guarded by `SessionId = ? AND ApplyState = 'pending'`.
```

- [ ] **Step 2: Document operator commands.**

English and Chinese SOPs must both include:

```bash
python3 -m apps.cli.db.drift_diagnose --since 24
python3 -m apps.cli.db.drift_diagnose --since 24 --json
python3 -m apps.cli.db.drift_diagnose --apply --session-id <SessionId>
```

Both SOPs must keep CLI names, env vars, JSON fields, and SQL predicates untranslated.

---

## Task 5: Verify P2

- [ ] **Step 1: Run the guarded apply tests.**

```bash
pytest tests/unit/test_drift_diagnose.py -k "apply" -v
```

Expected: PASS.

- [ ] **Step 2: Run the full ADR-009 drift diagnose test file.**

```bash
pytest tests/unit/test_drift_diagnose.py -v
```

Expected: PASS.

- [ ] **Step 3: Verify docs pairing.**

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

