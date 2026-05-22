# IMP-ADR010-02: ADR-010 Phase 2 — D1 Recovery Outbox Enablement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable ADR-010 Phase 2 recovery outbox behavior behind `D1_RECOVERY_OUTBOX_ENABLED=1` while preserving `STORAGE_BACKEND=d1` strong consistency and `STRICT_DUAL_WRITE=1` precedence.

**Architecture:** `D1AccessPort` may queue retry-exhausted safe writes into `reports/D1/d1_recovery_outbox.jsonl`, but only when a `RecoveryPolicy` permits it and the env flag is enabled. Dual-mode recovery must drain relevant ordering keys before session finalization. Dead letters block their ordering key.

**Tech Stack:** Python 3.11, pytest, JSONL recovery files, GitHub Actions YAML, Markdown docs.

**Source spec:** [ADR-010](ADR-010-d1-access-port.md), D5-D7, D10 Phase 2, D11.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/storage/d1_port.py` | Queue safe retry-exhausted operations to outbox when gated on. |
| `javdb/storage/d1_recovery.py` | Add drain/replay helpers, dead-letter transitions, and ordering-key status queries. |
| `javdb/storage/dual_connection.py` | Preserve strict-mode behavior and expose finalize-drain checks where needed. |
| `javdb/storage/db/db.py` | Drain related recovery keys before pending session finalization/commit. |
| `apps/cli/db/d1_recovery.py` | Add replay command for selected ordering key or all pending work. |
| `tests/unit/test_d1_port.py` | Queueing, disabled gate, d1-mode failure, dual-mode soft-success tests. |
| `tests/unit/test_d1_recovery.py` | Replay, dead-letter, compact, and ordering-key status tests. |
| `tests/unit/test_d1_dual.py` | Strict dual-write precedence tests. |
| `.github/workflows/*.yml` | Stage active/processed recovery outbox and summary files. |
| `.github/workflows/publish-to-public.yml` | Fail closed when full recovery outbox payload exists before public publishing. |
| `docs/handbook/en/ops/d1-rollback.md`, `docs/handbook/zh/ops/d1-rollback.md` | Operator runbook for inspect/replay/dead letters. |

---

## Task 1: Gate Outbox Queueing in D1AccessPort

**Files:**
- Modify: `javdb/storage/d1_port.py`
- Modify: `tests/unit/test_d1_port.py`

- [ ] **Step 1: Add tests for enabled and disabled queueing**

Append to `tests/unit/test_d1_port.py`:

```python
from javdb.storage.d1_recovery import RecoveryPolicy, load_latest_events


def _policy():
    return RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key="history:s1:seq1",
        ordering_key="history:s1",
        recovery_allowed=True,
        max_attempts=3,
    )


def test_retry_exhaustion_queues_safe_operation_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("D1_RECOVERY_OUTBOX_ENABLED", "1")
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    poster = FakePoster([
        FakeResponse(status_code=429, payload={"success": False, "errors": [{"message": "overloaded"}]}),
    ])
    port = _port(poster, max_retries=1)
    port._outbox_path = outbox

    with pytest.raises(D1TransientError):
        port.execute("INSERT INTO PendingMovieHistoryWrites (Seq) VALUES (?)", ["seq1"], policy=_policy())

    latest = load_latest_events(outbox)
    assert latest["history:s1:seq1"].state == "queued"
    assert port.summary()["outbox_queued"] == 1


def test_outbox_disabled_does_not_write_file(tmp_path, monkeypatch):
    monkeypatch.delenv("D1_RECOVERY_OUTBOX_ENABLED", raising=False)
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    poster = FakePoster([
        FakeResponse(status_code=429, payload={"success": False, "errors": [{"message": "overloaded"}]}),
    ])
    port = _port(poster, max_retries=1)
    port._outbox_path = outbox

    with pytest.raises(D1TransientError):
        port.execute("INSERT INTO PendingMovieHistoryWrites (Seq) VALUES (?)", ["seq1"], policy=_policy())

    assert not outbox.exists()
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_d1_port.py::test_retry_exhaustion_queues_safe_operation_when_enabled tests/unit/test_d1_port.py::test_outbox_disabled_does_not_write_file -v
```

Expected: FAIL until queueing is implemented.

- [ ] **Step 3: Implement env gate and outbox path**

In `javdb/storage/d1_port.py`, add:

```python
from javdb.storage.d1_recovery import RecoveryEvent, RecoveryPolicy, append_event


def recovery_outbox_path(reports_dir: str | None = None) -> Path:
    root = reports_dir or os.environ.get("REPORTS_DIR", "reports")
    return Path(root) / "D1" / "d1_recovery_outbox.jsonl"


def recovery_outbox_enabled() -> bool:
    raw = os.environ.get("D1_RECOVERY_OUTBOX_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

Set `self._outbox_path = recovery_outbox_path()` in `D1AccessPort.__init__`.

- [ ] **Step 4: Queue safe operations on retry exhaustion**

Change `_post_with_retry` to accept `policy`, `sql`, and `params`. When retry is exhausted and the gate is enabled, append `RecoveryEvent.queued(...)` before raising the transient error. `STORAGE_BACKEND=d1` must still raise; queueing is diagnostic there.

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_d1_port.py tests/unit/test_d1_recovery.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/d1_port.py tests/unit/test_d1_port.py
git commit -m "feat(storage): enable gated d1 recovery queueing"
```

---

## Task 2: Replay and Dead-Letter Semantics

**Files:**
- Modify: `javdb/storage/d1_recovery.py`
- Modify: `apps/cli/db/d1_recovery.py`
- Modify: `tests/unit/test_d1_recovery.py`

- [ ] **Step 1: Add replay tests**

Append to `tests/unit/test_d1_recovery.py`:

```python
def test_replay_marks_success_and_compacts(tmp_path):
    from javdb.storage.d1_recovery import replay_ordering_key

    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(outbox, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"))
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result["replayed"] == 1
    assert calls == [("INSERT INTO x VALUES (?)", ["a"])]
    assert "replayed" in processed.read_text(encoding="utf-8")


def test_replay_dead_letters_permanent_failure(tmp_path):
    from javdb.storage.d1_recovery import replay_ordering_key

    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(outbox, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"))

    class Conn:
        def execute(self, sql, params=()):
            raise RuntimeError("permanent")

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result["dead_lettered"] == 1
    latest = load_latest_events(outbox)
    assert latest["history:s1:seq1"].state == "dead_lettered"
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_d1_recovery.py::test_replay_marks_success_and_compacts tests/unit/test_d1_recovery.py::test_replay_dead_letters_permanent_failure -v
```

Expected: FAIL until replay helper exists.

- [ ] **Step 3: Implement `replay_ordering_key`**

Add a helper that loads pending events for one key, appends `attempting`, calls `conn.execute(event.sql, event.params)`, appends `replayed` on success, appends `dead_lettered` on exception, and compacts replayed events to processed.

- [ ] **Step 4: Add CLI replay command**

Extend `apps/cli/db/d1_recovery.py`:

```text
python3 -m apps.cli.db.d1_recovery replay --ordering-key history:s1
```

The command constructs `make_d1_connection(logical_db)` from each event's `logical_db`. It refuses to replay multiple logical DBs in one ordering-key command unless all pending events share the same logical DB.

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_d1_recovery.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/d1_recovery.py apps/cli/db/d1_recovery.py tests/unit/test_d1_recovery.py
git commit -m "feat(storage): replay d1 recovery outbox events"
```

---

## Task 3: Enforce Backend and Strict Semantics

**Files:**
- Modify: `javdb/storage/d1_port.py`
- Modify: `javdb/storage/dual_connection.py`
- Modify: `tests/unit/test_d1_port.py`
- Modify: `tests/unit/test_d1_dual.py`

- [ ] **Step 1: Add d1 strong-consistency test**

Append:

```python
def test_d1_mode_does_not_turn_outbox_into_success(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    monkeypatch.setenv("D1_RECOVERY_OUTBOX_ENABLED", "1")
    poster = FakePoster([
        FakeResponse(status_code=429, payload={"success": False, "errors": [{"message": "overloaded"}]}),
    ])
    port = _port(poster, max_retries=1)
    port._outbox_path = tmp_path / "outbox.jsonl"

    with pytest.raises(D1TransientError):
        port.execute("INSERT INTO PendingMovieHistoryWrites (Seq) VALUES (?)", ["seq1"], policy=_policy())
```

- [ ] **Step 2: Add strict dual test**

Append to `tests/unit/test_d1_dual.py`:

```python
def test_strict_dual_write_still_raises_when_d1_failure_is_outboxable(monkeypatch, sqlite_conn):
    monkeypatch.setenv("STRICT_DUAL_WRITE", "1")
    monkeypatch.setenv("D1_RECOVERY_OUTBOX_ENABLED", "1")

    class FailingD1(FakeD1Connection):
        def execute(self, sql, params=()):
            self.executed.append((sql, list(params)))
            if not _is_read(sql):
                raise RuntimeError("simulated D1 write failure")
            return FakeD1Cursor(rows=[{"n": 1}])

    dual = DualConnection(sqlite_conn, FailingD1(), logical_name="history")
    dual.execute("INSERT INTO t (v) VALUES (?)", ("x",))

    with pytest.raises(_dual_module.DualWriteStrictError):
        dual.commit()
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_d1_port.py::test_d1_mode_does_not_turn_outbox_into_success tests/unit/test_d1_dual.py::test_strict_dual_write_still_raises_when_d1_failure_is_outboxable -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_d1_port.py tests/unit/test_d1_dual.py
git commit -m "test(storage): lock d1 recovery consistency semantics"
```

---

## Task 4: Drain Recovery Before Pending Session Commit

**Files:**
- Modify: `javdb/storage/db/db.py`
- Modify: `tests/unit/test_rollback_pending_mode.py` or create `tests/unit/test_d1_recovery_commit_gate.py`

- [ ] **Step 1: Add commit-blocking test**

Create `tests/unit/test_d1_recovery_commit_gate.py`:

```python
from __future__ import annotations

import pytest

import javdb.storage.db.db as db_mod
from javdb.storage.d1_recovery import RecoveryEvent, RecoveryPolicy, append_event


def test_pending_commit_refuses_unresolved_recovery_key(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    sid = db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-19",
        csv_filename="recovery-block.csv",
        write_mode="pending",
    )
    policy = RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key=f"history:{sid}:seq1",
        ordering_key=f"history:{sid}",
        recovery_allowed=True,
        max_attempts=3,
    )
    append_event(
        tmp_path / "D1" / "d1_recovery_outbox.jsonl",
        RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"),
    )

    with pytest.raises(RuntimeError, match="unresolved D1 recovery"):
        db_mod.db_commit_session_history(sid)
```

- [ ] **Step 2: Run test**

```bash
pytest tests/unit/test_d1_recovery_commit_gate.py -v
```

Expected: FAIL until the commit gate exists.

- [ ] **Step 3: Implement commit gate**

In `javdb/storage/db/db.py`, before `db_finish_commit_session(...)`, check:

```python
from javdb.storage.d1_recovery import pending_by_ordering_key
from javdb.storage.d1_port import recovery_outbox_path

pending_recovery = pending_by_ordering_key(recovery_outbox_path())
if f"history:{session_id}" in pending_recovery:
    raise RuntimeError(
        f"unresolved D1 recovery work for ordering key history:{session_id}; "
        "drain it before committing the session"
    )
```

Keep this check narrow to pending history commit. Broader ordering-key gates can be added when more operation types opt in.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_d1_recovery_commit_gate.py tests/unit/test_rollback_pending_mode.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/db/db.py tests/unit/test_d1_recovery_commit_gate.py
git commit -m "fix(storage): block pending commit on unresolved d1 recovery"
```

---

## Task 5: Workflow Staging and Public Publish Guard

**Files:**
- Modify: `.github/workflows/DailyIngestion.yml`
- Modify: `.github/workflows/AdHocIngestion.yml`
- Modify: `.github/workflows/RcloneManager.yml`
- Modify: `.github/workflows/WeeklyDedup.yml`
- Modify: `.github/workflows/Migration.yml`
- Modify: `.github/workflows/publish-to-public.yml`

- [ ] **Step 1: Stage D1 recovery state files**

In each workflow that already stages `d1_drift.jsonl`, replace individual `git add "$REPORTS_DIR/D1/..."` lines with:

```bash
for D1_STATE_FILE in \
  "$REPORTS_DIR/D1/d1_drift.jsonl" \
  "$REPORTS_DIR/D1/d1_drift.processed.jsonl" \
  "$REPORTS_DIR/D1/d1_recovery_outbox.jsonl" \
  "$REPORTS_DIR/D1/d1_recovery_outbox.processed.jsonl" \
  "$REPORTS_DIR/D1/d1_port_summary.json"
do
  git add "$D1_STATE_FILE" 2>/dev/null || true
done
```

- [ ] **Step 2: Add public publish guard**

In `.github/workflows/publish-to-public.yml`, before publish copy/filter work:

```bash
if find reports/D1 -maxdepth 1 \( \
  -name 'd1_recovery_outbox.jsonl' -o \
  -name 'd1_recovery_outbox.processed.jsonl' \
\) -type f -size +0c | grep -q .; then
  echo "::error::D1 recovery outbox contains full SQL params; refusing public publish."
  exit 1
fi
```

- [ ] **Step 3: Verify workflow text**

```bash
rg -n "d1_recovery_outbox|d1_port_summary|refusing public publish" .github/workflows
```

Expected: private workflow staging hits plus `publish-to-public.yml` guard.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/DailyIngestion.yml .github/workflows/AdHocIngestion.yml .github/workflows/RcloneManager.yml .github/workflows/WeeklyDedup.yml .github/workflows/Migration.yml .github/workflows/publish-to-public.yml
git commit -m "ci(workflows): stage d1 recovery state and guard public publish"
```

---

## Task 6: Phase 2 Docs and Verification

**Files:**
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [ ] **Step 1: Add operator docs**

Add English section:

```markdown
## D1 Recovery Outbox

ADR-010 adds `reports/D1/d1_recovery_outbox.jsonl` for safe, recoverable D1 write failures. In `STORAGE_BACKEND=d1`, queued outbox work is diagnostic only; the write still fails. In `STORAGE_BACKEND=dual`, safe operations may queue for recovery, but the related session cannot be committed until its ordering key drains.

Inspect pending work:

```bash
python3 -m apps.cli.db.d1_recovery inspect
```

Replay one ordering key:

```bash
python3 -m apps.cli.db.d1_recovery replay --ordering-key history:<session_id>
```

Compact replayed work:

```bash
python3 -m apps.cli.db.d1_recovery compact
```
```

Add the Chinese equivalent to `docs/handbook/zh/ops/d1-rollback.md`.

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_d1_port.py tests/unit/test_d1_recovery.py tests/unit/test_d1_dual.py tests/unit/test_d1_recovery_commit_gate.py -v
```

Expected: PASS.

- [ ] **Step 3: Run doc grep**

```bash
rg -n "d1_recovery_outbox|d1_recovery replay|D1 Recovery Outbox" docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
```

Expected: hits in both docs.

- [ ] **Step 4: Commit**

```bash
git add docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
git commit -m "docs(d1): document recovery outbox operations"
```

---

## Verification Gate

- [ ] **Run focused suite**

```bash
pytest tests/unit/test_d1_port.py tests/unit/test_d1_recovery.py tests/unit/test_d1_dual.py tests/unit/test_d1_recovery_commit_gate.py -v
```

Expected: PASS.

- [ ] **Run storage regression suite**

```bash
pytest tests/unit/test_reconcile_d1_drift.py tests/unit/test_sync_d1_to_sqlite.py tests/unit/test_rollback_pending_mode.py tests/unit/test_batch_c_movie_history_id.py -v
```

Expected: PASS.
