# IMP-015: ADR-010 Phase 4 — D1 Startup Replay

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable ADR-010 Phase 4 startup replay behind `D1_STARTUP_REPLAY_ENABLED=1`, draining non-dead-lettered D1 recovery work before normal D1 operations begin.

**Architecture:** Startup replay is opt-in and bounded. It scans `reports/D1/d1_recovery_outbox.jsonl`, groups pending work by ordering key, skips dead letters, and replays safe work through normal `D1Connection`/`D1AccessPort` mechanics. It never hides replay failures; dead letters remain visible and block their ordering key.

**Tech Stack:** Python 3.11, pytest, JSONL recovery files, existing D1 connection factory.

**Source spec:** [ADR-010](../adr/ADR-010-d1-access-port.md), D5-D7, D10 Phase 4.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/storage/d1_recovery.py` | Startup drain coordinator and bounded replay result model. |
| `javdb/storage/db/db_connection.py` | Invoke startup replay once per process when enabled. |
| `apps/cli/db/d1_recovery.py` | Expose `startup-drain` command for manual parity with automatic behavior. |
| `tests/unit/test_d1_recovery.py` | Startup drain grouping, bounds, and dead-letter skip tests. |
| `tests/unit/test_d1_dual.py` or new `tests/unit/test_d1_startup_replay.py` | Ensure connection creation triggers startup drain once when enabled. |
| `docs/handbook/en/ops/d1-rollback.md`, `docs/handbook/zh/ops/d1-rollback.md` | Document startup replay switch and failure response. |

---

## Task 1: Add Startup Drain Coordinator

**Files:**
- Modify: `javdb/storage/d1_recovery.py`
- Modify: `tests/unit/test_d1_recovery.py`

- [ ] **Step 1: Add startup drain tests**

Append:

```python
def test_startup_drain_skips_dead_letters(tmp_path):
    from javdb.storage.d1_recovery import startup_drain

    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(outbox, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"))
    append_event(outbox, RecoveryEvent.dead_lettered(policy, attempt=1, error="permanent"))

    result = startup_drain(outbox, processed, connection_factory=lambda _db: object())

    assert result["replayed"] == 0
    assert result["dead_lettered"] == 1


def test_startup_drain_replays_pending_key(tmp_path):
    from javdb.storage.d1_recovery import startup_drain

    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(outbox, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"))
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))

    result = startup_drain(outbox, processed, connection_factory=lambda _db: Conn())

    assert result["replayed"] == 1
    assert calls == [("INSERT INTO x VALUES (?)", ["a"])]
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_d1_recovery.py::test_startup_drain_replays_pending_key -v
```

Expected: FAIL until `startup_drain` exists.

- [ ] **Step 3: Implement `startup_drain`**

Add a function that iterates `pending_by_ordering_key`, skips keys whose latest event is `dead_lettered`, creates a D1 connection via `connection_factory(logical_db)`, and calls `replay_ordering_key` for each key. Return aggregate counts.

- [ ] **Step 4: Run recovery tests**

```bash
pytest tests/unit/test_d1_recovery.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/d1_recovery.py tests/unit/test_d1_recovery.py
git commit -m "feat(storage): add d1 startup recovery drain"
```

---

## Task 2: Wire Startup Drain to Connection Setup

**Files:**
- Modify: `javdb/storage/db/db_connection.py`
- Create or modify: `tests/unit/test_d1_startup_replay.py`

- [ ] **Step 1: Add startup gate test**

Create `tests/unit/test_d1_startup_replay.py`:

```python
from __future__ import annotations

import javdb.storage.db.db_connection as db_conn


def test_startup_replay_runs_once_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setenv("D1_STARTUP_REPLAY_ENABLED", "1")
    monkeypatch.setattr(db_conn, "_startup_recovery_drain", lambda: calls.append("drain"))

    db_conn._maybe_startup_recovery_drain()
    db_conn._maybe_startup_recovery_drain()

    assert calls == ["drain"]
```

- [ ] **Step 2: Run test**

```bash
pytest tests/unit/test_d1_startup_replay.py -v
```

Expected: FAIL until helper exists.

- [ ] **Step 3: Implement startup gate**

In `javdb/storage/db/db_connection.py`, add module-level flag:

```python
_startup_recovery_drained = False
```

Add:

```python
def _startup_replay_enabled() -> bool:
    raw = os.environ.get("D1_STARTUP_REPLAY_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _startup_recovery_drain() -> None:
    from javdb.storage.d1_recovery import startup_drain
    from javdb.storage.d1_port import recovery_outbox_path
    from javdb.storage.d1_recovery import processed_outbox_path
    from javdb.storage.d1_client import make_d1_connection

    startup_drain(
        recovery_outbox_path(),
        processed_outbox_path(),
        connection_factory=make_d1_connection,
    )


def _maybe_startup_recovery_drain() -> None:
    global _startup_recovery_drained
    if _startup_recovery_drained or not _startup_replay_enabled():
        return
    _startup_recovery_drained = True
    _startup_recovery_drain()
```

Call `_maybe_startup_recovery_drain()` before constructing a D1 or Dual connection in `_get_connection`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_d1_startup_replay.py tests/unit/test_d1_dual.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/db/db_connection.py tests/unit/test_d1_startup_replay.py
git commit -m "feat(storage): gate d1 startup recovery replay"
```

---

## Task 3: CLI and Docs

**Files:**
- Modify: `apps/cli/db/d1_recovery.py`
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [ ] **Step 1: Add CLI command**

Add:

```text
python3 -m apps.cli.db.d1_recovery startup-drain
```

It calls the same `startup_drain(...)` helper and prints aggregate JSON.

- [ ] **Step 2: Document startup replay**

Add to English rollback docs:

```markdown
### Startup Replay

`D1_STARTUP_REPLAY_ENABLED=1` drains non-dead-lettered recovery work when the process first opens a D1 or Dual connection. Dead-lettered work is skipped and continues to block its ordering key. Use this only after Phase 2 recovery outbox behavior has baked cleanly.
```

Add the Chinese equivalent.

- [ ] **Step 3: Run tests and grep**

```bash
pytest tests/unit/test_d1_recovery.py tests/unit/test_d1_startup_replay.py -v
rg -n "startup-drain|D1_STARTUP_REPLAY_ENABLED|Startup Replay" apps/cli/db/d1_recovery.py docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
```

Expected: tests pass and grep finds command/docs.

- [ ] **Step 4: Commit**

```bash
git add apps/cli/db/d1_recovery.py docs/handbook/en/ops/d1-rollback.md docs/handbook/zh/ops/d1-rollback.md
git commit -m "docs(d1): document startup recovery replay"
```

---

## Verification Gate

- [ ] **Run focused tests**

```bash
pytest tests/unit/test_d1_recovery.py tests/unit/test_d1_startup_replay.py tests/unit/test_d1_dual.py -v
```

Expected: PASS.

- [ ] **Run empty startup-drain command**

```bash
python3 -m apps.cli.db.d1_recovery startup-drain
```

Expected: exits `0` with zero replay/dead-letter counts when no outbox exists.
