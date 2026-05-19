# IMP-014: ADR-010 Phase 3 — Safe D1 Micro-Batching

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable ADR-010 Phase 3 safe-path micro-batching behind `D1_BATCHING_ENABLED=1` without delaying arbitrary SQL.

**Architecture:** `D1AccessPort` gains an in-memory safe batch queue keyed by `ordering_key`. Only calls with explicit `RecoveryPolicy` and batching permission enter the queue. Flush occurs on batch size, elapsed interval, explicit `flush()`, connection close, or commit/finalization boundaries.

**Tech Stack:** Python 3.11, pytest, monotonic timers, existing D1 batch API.

**Source spec:** [ADR-010](../adr/ADR-010-d1-access-port.md), D3-D4, D7, D10 Phase 3.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/storage/d1_port.py` | Safe batch queue, env gates, flush boundaries, batch metrics. |
| `javdb/storage/d1_recovery.py` | Extend `RecoveryPolicy` with batching permission if not already present. |
| `javdb/storage/d1_client.py` | Call `flush()` from connection lifecycle. |
| `javdb/storage/db/db.py` | Explicit flush around pending commit/finalization boundaries when D1 batching is enabled. |
| `tests/unit/test_d1_port.py` | Queue, threshold, interval, explicit flush, and non-safe SQL tests. |
| `tests/unit/test_commit_session_bulk.py` | Ensure default bulk path remains compatible with batching gate. |

---

## Task 1: Add Safe Batching Policy Fields

**Files:**
- Modify: `javdb/storage/d1_recovery.py`
- Modify: `tests/unit/test_d1_recovery.py`

- [ ] **Step 1: Add policy serialization test**

Append:

```python
def test_recovery_policy_carries_batching_permission():
    policy = RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key="history:s1:seq1",
        ordering_key="history:s1",
        recovery_allowed=True,
        max_attempts=3,
        batching_allowed=True,
    )

    event = RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout")

    assert event.batching_allowed is True
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/unit/test_d1_recovery.py::test_recovery_policy_carries_batching_permission -v
```

Expected: FAIL until `batching_allowed` exists.

- [ ] **Step 3: Add `batching_allowed`**

Add `batching_allowed: bool = False` to `RecoveryPolicy` and `RecoveryEvent`. Make `RecoveryEvent.policy()` preserve it.

- [ ] **Step 4: Run recovery tests**

```bash
pytest tests/unit/test_d1_recovery.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/d1_recovery.py tests/unit/test_d1_recovery.py
git commit -m "feat(storage): mark d1 recovery policies batch-safe"
```

---

## Task 2: Add D1 Batching Gate and Queue

**Files:**
- Modify: `javdb/storage/d1_port.py`
- Modify: `tests/unit/test_d1_port.py`

- [ ] **Step 1: Add batching tests**

Append:

```python
def _batch_policy(key="history:s1:seq1"):
    return RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key=key,
        ordering_key="history:s1",
        recovery_allowed=True,
        max_attempts=3,
        batching_allowed=True,
    )


def test_batching_disabled_executes_immediately(monkeypatch):
    monkeypatch.delenv("D1_BATCHING_ENABLED", raising=False)
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]})
    ])
    port = _port(poster)

    port.execute("INSERT INTO x VALUES (?)", ["a"], policy=_batch_policy())

    assert len(poster.calls) == 1


def test_batching_enabled_queues_until_flush(monkeypatch):
    monkeypatch.setenv("D1_BATCHING_ENABLED", "1")
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]})
    ])
    port = _port(poster)

    port.execute("INSERT INTO x VALUES (?)", ["a"], policy=_batch_policy())
    assert len(poster.calls) == 0

    port.flush(ordering_key="history:s1")
    assert len(poster.calls) == 1
    assert "batch" in poster.calls[0]["json"]


def test_non_batch_safe_sql_executes_immediately_even_when_enabled(monkeypatch):
    monkeypatch.setenv("D1_BATCHING_ENABLED", "1")
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]})
    ])
    port = _port(poster)

    port.execute("INSERT INTO x VALUES (?)", ["a"], policy=None)

    assert len(poster.calls) == 1
```

- [ ] **Step 2: Run batching tests**

```bash
pytest tests/unit/test_d1_port.py::test_batching_enabled_queues_until_flush -v
```

Expected: FAIL until queueing exists.

- [ ] **Step 3: Implement gates**

In `javdb/storage/d1_port.py`, add:

```python
def d1_batching_enabled() -> bool:
    raw = os.environ.get("D1_BATCHING_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

Add a per-ordering-key queue:

```python
self._batch_queue: dict[str, list[tuple[str, tuple[Any, ...], object]]] = {}
```

When `execute()` receives a policy with `batching_allowed=True` and batching is enabled, store `(sql, params_tuple, policy)` and return a cursor with `rowcount=0` only if the caller does not require immediate rowcount. For initial rollout, only allow batching for operations whose result is ignored. If the caller needs rowcount, do not batch.

- [ ] **Step 4: Implement `flush()`**

`flush(ordering_key=...)` converts queued statements to `batch_execute` calls and clears the queue only after the D1 batch succeeds. It must update `batches`, `batch_statements`, and `sql_statements` metrics.

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_d1_port.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add javdb/storage/d1_port.py tests/unit/test_d1_port.py
git commit -m "feat(storage): add gated d1 safe batching"
```

---

## Task 3: Flush Boundaries

**Files:**
- Modify: `javdb/storage/d1_client.py`
- Modify: `javdb/storage/db/db.py`
- Modify: `tests/unit/test_d1_port.py`

- [ ] **Step 1: Add close-flush test**

Append:

```python
def test_close_flushes_safe_batch(monkeypatch):
    monkeypatch.setenv("D1_BATCHING_ENABLED", "1")
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]})
    ])
    port = _port(poster)
    port.execute("INSERT INTO x VALUES (?)", ["a"], policy=_batch_policy())

    port.close()

    assert len(poster.calls) == 1
```

- [ ] **Step 2: Implement close flush**

In `D1AccessPort.close()`, call `self.flush()` before closing the session.

- [ ] **Step 3: Add pending commit flush**

In `db_commit_session_history`, call `flush()` on the active connection when available before `db_finish_commit_session(...)`:

```python
flush = getattr(conn, "flush", None)
if callable(flush):
    flush(ordering_key=f"history:{session_id}")
```

If `conn` is a `DualConnection`, add a `flush()` method that delegates to the D1 side when present.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_d1_port.py tests/unit/test_commit_session_bulk.py tests/unit/test_rollback_pending_mode.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/d1_port.py javdb/storage/d1_client.py javdb/storage/db/db.py tests/unit/test_d1_port.py
git commit -m "feat(storage): flush d1 safe batches at commit boundaries"
```

---

## Task 4: Phase 3 Verification

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/unit/test_d1_port.py tests/unit/test_d1_recovery.py tests/unit/test_d1_dual.py tests/unit/test_commit_session_bulk.py -v
```

Expected: PASS.

- [ ] **Step 2: Run storage regressions**

```bash
pytest tests/unit/test_rollback_pending_mode.py tests/unit/test_batch_c_movie_history_id.py -v
```

Expected: PASS.
