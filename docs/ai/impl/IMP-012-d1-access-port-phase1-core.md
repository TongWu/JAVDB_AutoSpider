# IMP-012: ADR-010 Phase 1 — D1 Access Port Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-010 Phase 1: route `D1Connection` through a Python-internal `D1AccessPort`, emit D1 port metrics, default the existing pending commit bulk path on, and land recovery inspect tooling without enabling outbox soft-success.

**Architecture:** `D1AccessPort` owns HTTP POSTs, retry/backoff parity, schema metadata caching, and summary metrics. `D1Connection` remains the sqlite3-compatible facade. Recovery outbox files and CLI are introduced as inert tooling; queueing/replay success semantics are left to IMP-013.

**Tech Stack:** Python 3.11, requests, pytest, JSON/JSONL, GitHub Actions YAML, Markdown docs.

**Source spec:** [ADR-010](../adr/ADR-010-d1-access-port.md), D1-D4, D8-D10 Phase 1.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/storage/d1_port.py` | New transport-only port with retry parity, schema metadata cache, and `d1_port_summary.json` writer. |
| `javdb/storage/d1_client.py` | Delegate D1 HTTP execution to `D1AccessPort` while keeping the current facade. |
| `javdb/storage/d1_recovery.py` | Define recovery outbox event/policy records and compact helpers; queueing remains disabled. |
| `apps/cli/db/d1_recovery.py` | Read-only inspect + compact CLI for future outbox files. |
| `javdb/storage/db/db.py` | Default `COMMIT_SESSION_BULK` on with opt-out. |
| `tests/unit/test_d1_port.py` | New tests for port retry, schema cache, and metrics. |
| `tests/unit/test_d1_recovery.py` | New tests for outbox model and CLI inspect/compact. |
| `tests/unit/test_d1_dual.py` | Tests proving `D1Connection` delegates through the port. |
| `tests/unit/test_commit_session_bulk.py` | Tests for bulk commit default-on and env opt-out. |
| `apps/cli/db/README.md` | Document the new D1 recovery CLI. |
| `README.md`, `README_CN.md`, `docs/en/self-hoster/configuration.md`, `docs/zh/self-hoster/configuration.md` | Document Phase 1 controls and future-gated variables. |

Do not move business SQL into `d1_port.py`.

---

## Task 1: D1AccessPort Transport Core

**Files:**
- Create: `javdb/storage/d1_port.py`
- Create: `tests/unit/test_d1_port.py`

- [ ] **Step 1: Write failing tests for request delegation and retry parity**

Create `tests/unit/test_d1_port.py`:

```python
from __future__ import annotations

import json

import pytest

from javdb.storage.d1_client import D1PermanentError, D1TransientError
from javdb.storage.d1_port import D1AccessPort, D1PortConfig


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakePoster:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _port(poster, *, max_retries=2):
    return D1AccessPort(
        url="https://example.test/query",
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        config=D1PortConfig(timeout=3, batch_limit=50, max_retries=max_retries, retry_base_sec=0, retry_max_sleep_sec=0),
        post_request=poster,
        sleep=lambda _seconds: None,
        jitter=lambda: 0,
    )


def test_execute_posts_single_statement_body():
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 1}, "results": []}]})
    ])
    port = _port(poster)

    cursors = port.execute("SELECT 1", [])

    assert len(cursors) == 1
    assert poster.calls[0]["json"] == {"sql": "SELECT 1", "params": []}
    assert port.summary()["http_posts"] == 1


def test_transient_error_retries_then_succeeds():
    poster = FakePoster([
        FakeResponse(status_code=500, payload={"success": False, "errors": [{"message": "temporary"}]}),
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 0}, "results": [{"n": 1}]}]}),
    ])
    port = _port(poster, max_retries=2)

    cursors = port.execute("SELECT 1", [])

    assert cursors[0].fetchone() == {"n": 1}
    assert len(poster.calls) == 2
    assert port.summary()["retries"] == 1
    assert port.summary()["retry_successes"] == 1


def test_permanent_error_does_not_retry():
    poster = FakePoster([
        FakeResponse(status_code=400, payload={"success": False, "errors": [{"message": "no such table: x"}]})
    ])
    port = _port(poster, max_retries=3)

    with pytest.raises(D1PermanentError):
        port.execute("SELECT * FROM x", [])

    assert len(poster.calls) == 1
    assert port.summary()["permanent_errors"] == 1


def test_transient_error_exhaustion_raises_transient():
    poster = FakePoster([
        FakeResponse(status_code=429, payload={"success": False, "errors": [{"message": "overloaded"}]}),
        FakeResponse(status_code=429, payload={"success": False, "errors": [{"message": "overloaded"}]}),
    ])
    port = _port(poster, max_retries=2)

    with pytest.raises(D1TransientError):
        port.execute("SELECT 1", [])

    assert port.summary()["transient_errors"] == 2
```

- [ ] **Step 2: Run the tests and verify the expected failure**

```bash
pytest tests/unit/test_d1_port.py -v
```

Expected: `ModuleNotFoundError: No module named 'javdb.storage.d1_port'`.

- [ ] **Step 3: Implement `javdb/storage/d1_port.py`**

Create `javdb/storage/d1_port.py`:

```python
"""Unified Cloudflare D1 access port.

This module owns transport concerns only: HTTP POSTs, retry/backoff,
schema metadata cache, metrics, and recovery hooks. Business SQL remains
in the storage DB and Repo layers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import random
import time
from typing import Any, Callable, Iterable, Optional, Sequence

import requests

from javdb.storage.d1_client import (
    D1Cursor,
    D1PermanentError,
    D1TransientError,
    _EXPORT_LOCK_BACKOFF_FLOOR_SEC,
    _EXPORT_LOCK_KEYWORDS,
    _TRANSIENT_ERROR_KEYWORDS,
    _matches_keyword,
    _params_for_d1_json,
)


@dataclass(frozen=True)
class D1PortConfig:
    timeout: int
    batch_limit: int
    max_retries: int
    retry_base_sec: float
    retry_max_sleep_sec: float


def d1_summary_path(reports_dir: str | None = None) -> Path:
    root = reports_dir or os.environ.get("REPORTS_DIR", "reports")
    return Path(root) / "D1" / "d1_port_summary.json"


class D1AccessPort:
    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        config: D1PortConfig,
        post_request: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.5),
    ):
        self._url = url
        self._headers = headers
        self._config = config
        self._session = requests.Session()
        self._post_request = post_request or self._session.post
        self._sleep = sleep
        self._jitter = jitter
        self._schema_cache: dict[tuple[str, tuple[Any, ...]], list[D1Cursor]] = {}
        self._summary = {
            "http_posts": 0,
            "sql_statements": 0,
            "batches": 0,
            "batch_statements": 0,
            "retries": 0,
            "retry_successes": 0,
            "transient_errors": 0,
            "permanent_errors": 0,
            "schema_cache_hits": 0,
            "schema_cache_misses": 0,
            "outbox_queued": 0,
            "outbox_replayed": 0,
            "outbox_dead_lettered": 0,
        }

    def execute(self, sql: str, params: Iterable[Any] = (), *, policy=None) -> list[D1Cursor]:
        params_tuple = tuple(params)
        key = self._schema_cache_key(sql, params_tuple)
        if key is not None and key in self._schema_cache:
            self._summary["schema_cache_hits"] += 1
            return self._clone_cursors(self._schema_cache[key])
        if key is not None:
            self._summary["schema_cache_misses"] += 1

        cursors = self._post_with_retry(
            {"sql": sql, "params": _params_for_d1_json(params_tuple)}
        )
        self._summary["sql_statements"] += 1
        if key is not None:
            self._schema_cache[key] = self._clone_cursors(cursors)
        return cursors

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]], *, policy=None) -> list[D1Cursor]:
        statements = [
            {"sql": sql, "params": _params_for_d1_json(params)}
            for params in seq_of_params
        ]
        cursors: list[D1Cursor] = []
        for chunk in self._split(statements, self._config.batch_limit):
            cursors.extend(self._post_with_retry({"batch": chunk}))
            self._summary["batches"] += 1
            self._summary["batch_statements"] += len(chunk)
            self._summary["sql_statements"] += len(chunk)
        return cursors

    def batch_execute(self, statements: Sequence[tuple[str, Sequence[Any]]], *, policy=None) -> list[D1Cursor]:
        body_statements = [
            {"sql": sql, "params": _params_for_d1_json(params)}
            for sql, params in statements
        ]
        cursors: list[D1Cursor] = []
        for chunk in self._split(body_statements, self._config.batch_limit):
            cursors.extend(self._post_with_retry({"batch": chunk}))
            self._summary["batches"] += 1
            self._summary["batch_statements"] += len(chunk)
            self._summary["sql_statements"] += len(chunk)
        return cursors

    def flush(self, *, ordering_key: str | None = None) -> None:
        return None

    def drain_recovery(self, *, ordering_key: str | None = None, max_batches: int | None = None) -> dict[str, int]:
        return {"replayed": 0, "dead_lettered": 0}

    def summary(self) -> dict[str, int]:
        return dict(self._summary)

    def write_summary(self, path: str | os.PathLike[str] | None = None) -> None:
        target = Path(path) if path is not None else d1_summary_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.summary(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def _post_with_retry(self, body: dict) -> list[D1Cursor]:
        last_exc: D1TransientError | None = None
        for attempt in range(self._config.max_retries):
            try:
                result = self._post(body)
                if attempt > 0:
                    self._summary["retry_successes"] += 1
                return result
            except D1PermanentError:
                self._summary["permanent_errors"] += 1
                raise
            except D1TransientError as exc:
                self._summary["transient_errors"] += 1
                last_exc = exc
                if attempt >= self._config.max_retries - 1:
                    break
                self._summary["retries"] += 1
                self._sleep(self._compute_backoff(attempt, exc))
        assert last_exc is not None
        raise last_exc

    def _post(self, body: dict) -> list[D1Cursor]:
        self._summary["http_posts"] += 1
        try:
            response = self._post_request(
                self._url,
                headers=self._headers,
                json=body,
                timeout=self._config.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise D1TransientError(f"D1 network error: {exc}") from exc
        except requests.RequestException as exc:
            raise D1TransientError(f"D1 HTTP request failed: {exc}") from exc

        status = response.status_code
        if status == 429 or 500 <= status < 600:
            err = D1TransientError(f"D1 API returned HTTP {status}: {response.text[:500]}")
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                err.retry_after = retry_after  # type: ignore[attr-defined]
            raise err
        if 400 <= status < 500:
            errors = self._extract_errors(response)
            if errors is not None:
                err_text = str(errors)
                if _matches_keyword(err_text, _TRANSIENT_ERROR_KEYWORDS):
                    transient = D1TransientError(
                        f"D1 API returned HTTP {status} (transient): {errors}"
                    )
                    if _matches_keyword(err_text, _EXPORT_LOCK_KEYWORDS):
                        transient.is_export_lock = True  # type: ignore[attr-defined]
                    raise transient
                raise D1PermanentError(f"D1 API returned HTTP {status}: {errors}")
            raise D1PermanentError(f"D1 API returned HTTP {status}: {response.text[:500]}")
        if status != 200:
            raise D1PermanentError(
                f"D1 API returned unexpected HTTP {status}: {response.text[:500]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise D1TransientError(f"D1 returned non-JSON response: {response.text[:500]}") from exc

        if not payload.get("success"):
            errors = payload.get("errors") or payload.get("messages") or []
            err_text = str(errors)
            if _matches_keyword(err_text, _TRANSIENT_ERROR_KEYWORDS):
                transient = D1TransientError(f"D1 API transient error: {errors}")
                if _matches_keyword(err_text, _EXPORT_LOCK_KEYWORDS):
                    transient.is_export_lock = True  # type: ignore[attr-defined]
                raise transient
            raise D1PermanentError(f"D1 API error: {errors}")

        return [D1Cursor(item) for item in payload.get("result") or []]

    def _compute_backoff(self, attempt: int, exc: D1TransientError) -> float:
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
        base = self._config.retry_base_sec * (2 ** attempt)
        if getattr(exc, "is_export_lock", False):
            base = max(base, _EXPORT_LOCK_BACKOFF_FLOOR_SEC)
        return min(base, self._config.retry_max_sleep_sec) + self._jitter()

    @staticmethod
    def _extract_errors(response) -> list | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload.get("errors") or payload.get("messages") or []

    @staticmethod
    def _split(seq: Sequence[Any], size: int):
        for i in range(0, len(seq), size):
            yield seq[i:i + size]

    @staticmethod
    def _schema_cache_key(sql: str, params: Iterable[Any]) -> tuple[str, tuple[Any, ...]] | None:
        normalized = " ".join(sql.strip().lower().split())
        if normalized.startswith("pragma table_info") or "from sqlite_master" in normalized:
            return (normalized, tuple(params))
        return None

    @staticmethod
    def _clone_cursors(cursors: Sequence[D1Cursor]) -> list[D1Cursor]:
        return [
            D1Cursor({
                "meta": {"last_row_id": cur.lastrowid, "changes": cur.rowcount},
                "results": cur.fetchall(),
            })
            for cur in cursors
        ]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_d1_port.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/d1_port.py tests/unit/test_d1_port.py
git commit -m "feat(storage): add d1 access port core"
```

---

## Task 2: Delegate D1Connection Through the Port

**Files:**
- Modify: `javdb/storage/d1_client.py`
- Modify: `tests/unit/test_d1_dual.py`

- [ ] **Step 1: Add failing delegation test**

Append to `tests/unit/test_d1_dual.py`:

```python
def test_d1_connection_delegates_execute_to_port():
    calls = []

    class FakePort:
        def execute(self, sql, params=(), *, policy=None):
            calls.append(("execute", sql, list(params), policy))
            return [D1Cursor({"meta": {"changes": 0}, "results": [{"n": 1}]})]

        def executemany(self, sql, seq_of_params, *, policy=None):
            calls.append(("executemany", sql, [list(p) for p in seq_of_params], policy))
            return [D1Cursor({"meta": {"changes": 1}, "results": []})]

        def batch_execute(self, statements, *, policy=None):
            calls.append(("batch_execute", list(statements), policy))
            return [D1Cursor({"meta": {"changes": 0}, "results": []})]

        def write_summary(self):
            calls.append(("write_summary",))

        def close(self):
            calls.append(("close",))

    conn = D1Connection("acct", "db", "token")
    conn._port = FakePort()

    row = conn.execute("SELECT 1 AS n").fetchone()

    assert row == {"n": 1}
    assert calls[0] == ("execute", "SELECT 1 AS n", [], None)
```

- [ ] **Step 2: Run the new test**

```bash
pytest tests/unit/test_d1_dual.py::test_d1_connection_delegates_execute_to_port -v
```

Expected: FAIL because `D1Connection.execute()` still calls `_post_with_retry()` directly.

- [ ] **Step 3: Wire `D1Connection.__init__`**

In `javdb/storage/d1_client.py`, import:

```python
from javdb.storage.d1_port import D1AccessPort, D1PortConfig
```

After `self._post_request = self._session.post`, add:

```python
self._port = D1AccessPort(
    url=self._url,
    headers=self._headers,
    config=D1PortConfig(
        timeout=self._timeout,
        batch_limit=_BATCH_LIMIT,
        max_retries=_MAX_RETRIES,
        retry_base_sec=_RETRY_BASE_SEC,
        retry_max_sleep_sec=_RETRY_MAX_SLEEP_SEC,
    ),
    post_request=self._post_request,
)
```

- [ ] **Step 4: Delegate `execute`, `executemany`, `batch_execute`, and `executescript`**

Change `execute()` to call:

```python
cursors = self._port.execute(sql, params)
```

Change `executemany()` to call:

```python
cursors = self._port.executemany(sql, seq_of_params)
```

Change `batch_execute()` to call:

```python
cursors = self._port.batch_execute(statements)
```

Change `executescript()` to:

```python
self.batch_execute([(statement, []) for statement in statements])
```

Keep existing `D1PermanentError` guard for empty result lists in `execute()`.

- [ ] **Step 5: Write summary on close**

In `D1Connection.close()`, call `self._port.write_summary()` before closing:

```python
try:
    self._port.write_summary()
except Exception:
    logger.warning("Failed to write D1 port summary", exc_info=True)
try:
    self._port.close()
finally:
    try:
        self._session.close()
    except Exception:
        pass
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/test_d1_dual.py::test_d1_connection_delegates_execute_to_port tests/unit/test_d1_port.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add javdb/storage/d1_client.py tests/unit/test_d1_dual.py
git commit -m "refactor(storage): route d1 connection through access port"
```

---

## Task 3: Schema Cache and Summary Tests

**Files:**
- Modify: `tests/unit/test_d1_port.py`

- [ ] **Step 1: Add tests**

Append:

```python
def test_schema_metadata_queries_are_cached():
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 0}, "results": [{"name": "Id"}]}]}),
    ])
    port = _port(poster)

    first = port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall()
    second = port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall()

    assert first == [{"name": "Id"}]
    assert second == [{"name": "Id"}]
    assert len(poster.calls) == 1
    assert port.summary()["schema_cache_hits"] == 1


def test_business_select_is_not_cached():
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 0}, "results": [{"n": 1}]}]}),
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 0}, "results": [{"n": 2}]}]}),
    ])
    port = _port(poster)

    assert port.execute("SELECT COUNT(*) AS n FROM MovieHistory")[0].fetchone() == {"n": 1}
    assert port.execute("SELECT COUNT(*) AS n FROM MovieHistory")[0].fetchone() == {"n": 2}
    assert len(poster.calls) == 2


def test_write_summary_creates_json(tmp_path):
    poster = FakePoster([
        FakeResponse(payload={"success": True, "result": [{"meta": {"changes": 0}, "results": []}]})
    ])
    port = _port(poster)
    port.execute("SELECT 1", [])

    path = tmp_path / "d1_port_summary.json"
    port.write_summary(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["http_posts"] == 1
    assert data["sql_statements"] == 1
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_d1_port.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_d1_port.py
git commit -m "test(storage): cover d1 port cache and summary"
```

---

## Task 4: Default Pending Commit Bulk Path On

**Files:**
- Modify: `javdb/storage/db/db.py`
- Modify: `tests/unit/test_commit_session_bulk.py`

- [ ] **Step 1: Add tests**

Append to `tests/unit/test_commit_session_bulk.py`:

```python
def test_commit_session_bulk_defaults_on(monkeypatch):
    monkeypatch.delenv("COMMIT_SESSION_BULK", raising=False)
    assert db_mod._commit_session_bulk_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_commit_session_bulk_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("COMMIT_SESSION_BULK", value)
    assert db_mod._commit_session_bulk_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_commit_session_bulk_accepts_enabled_values(monkeypatch, value):
    monkeypatch.setenv("COMMIT_SESSION_BULK", value)
    assert db_mod._commit_session_bulk_enabled() is True
```

- [ ] **Step 2: Run the default-on test**

```bash
pytest tests/unit/test_commit_session_bulk.py::test_commit_session_bulk_defaults_on -v
```

Expected: FAIL with missing `_commit_session_bulk_enabled`.

- [ ] **Step 3: Add helper and use it**

In `javdb/storage/db/db.py`, near `db_commit_session_history`, add:

```python
def _commit_session_bulk_enabled() -> bool:
    raw = os.getenv("COMMIT_SESSION_BULK")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}
```

Replace the current `use_bulk = os.getenv("COMMIT_SESSION_BULK", "0")...` block with:

```python
use_bulk = _commit_session_bulk_enabled()
```

- [ ] **Step 4: Run bulk tests**

```bash
pytest tests/unit/test_commit_session_bulk.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/db/db.py tests/unit/test_commit_session_bulk.py
git commit -m "feat(storage): default pending commit bulk path on"
```

---

## Task 5: Recovery Model and Inert CLI

**Files:**
- Create: `javdb/storage/d1_recovery.py`
- Create: `apps/cli/db/d1_recovery.py`
- Create: `tests/unit/test_d1_recovery.py`
- Modify: `apps/cli/db/README.md`

- [ ] **Step 1: Add model and CLI tests**

Create `tests/unit/test_d1_recovery.py`:

```python
from __future__ import annotations

from javdb.storage.d1_recovery import (
    RecoveryEvent,
    RecoveryPolicy,
    append_event,
    compact_replayed,
    load_latest_events,
    pending_by_ordering_key,
)


def _policy(key="history:s1:seq1", ordering="history:s1"):
    return RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key=key,
        ordering_key=ordering,
        recovery_allowed=True,
        max_attempts=3,
    )


def test_append_and_load_latest_events(tmp_path):
    path = tmp_path / "d1_recovery_outbox.jsonl"
    policy = _policy()
    append_event(path, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"))
    append_event(path, RecoveryEvent.attempting(policy, attempt=1))

    latest = load_latest_events(path)

    assert latest["history:s1:seq1"].state == "attempting"
    assert latest["history:s1:seq1"].attempt == 1


def test_pending_by_ordering_key_preserves_fifo(tmp_path):
    path = tmp_path / "d1_recovery_outbox.jsonl"
    for idx in range(3):
        policy = _policy(key=f"history:s1:{idx}")
        append_event(path, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", [idx], "timeout"))

    grouped = pending_by_ordering_key(path)

    assert [event.idempotency_key for event in grouped["history:s1"]] == [
        "history:s1:0",
        "history:s1:1",
        "history:s1:2",
    ]


def test_compact_replayed_moves_replayed_events(tmp_path):
    active = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy(key="reports:s1:stats", ordering="reports:s1")
    append_event(active, RecoveryEvent.queued(policy, "INSERT INTO stats VALUES (?)", ["s1"], "timeout"))
    append_event(active, RecoveryEvent.replayed(policy, attempt=1))

    result = compact_replayed(active, processed)

    assert result == {"active": 0, "processed": 2}
    assert active.read_text(encoding="utf-8") == ""
    assert "reports:s1:stats" in processed.read_text(encoding="utf-8")


def test_cli_inspect_outputs_counts(tmp_path, capsys):
    from apps.cli.db import d1_recovery as cli

    path = tmp_path / "d1_recovery_outbox.jsonl"
    policy = _policy()
    append_event(path, RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout"))

    rc = cli.main(["inspect", "--outbox", str(path)])

    assert rc == 1
    assert "history:s1" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/unit/test_d1_recovery.py -v
```

Expected: missing module failures.

- [ ] **Step 3: Implement `javdb/storage/d1_recovery.py`**

Create the module with `RecoveryPolicy`, `RecoveryEvent`, `append_event`, `load_latest_events`, `pending_by_ordering_key`, and `compact_replayed` exactly as specified in [ADR-010](../adr/ADR-010-d1-access-port.md) D5.

- [ ] **Step 4: Implement `apps/cli/db/d1_recovery.py`**

Create commands:

```text
python3 -m apps.cli.db.d1_recovery inspect [--outbox PATH] [--json]
python3 -m apps.cli.db.d1_recovery compact [--outbox PATH] [--processed PATH]
```

`inspect` exits `1` when pending work exists and `0` when none exists. `compact` exits `0` after moving replayed/abandoned events.

- [ ] **Step 5: Update CLI README**

Add:

```markdown
| `d1_recovery.py` | Inspect and compact the D1 recovery outbox introduced by ADR-010. Defaults to read-only `inspect`; `compact` moves replayed/abandoned records to `d1_recovery_outbox.processed.jsonl`. |
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/test_d1_recovery.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add javdb/storage/d1_recovery.py apps/cli/db/d1_recovery.py apps/cli/db/README.md tests/unit/test_d1_recovery.py
git commit -m "feat(storage): add d1 recovery outbox tooling"
```

---

## Task 6: Phase 1 Docs

**Files:**
- Modify: `README.md`
- Modify: `README_CN.md`
- Modify: `docs/en/self-hoster/configuration.md`
- Modify: `docs/zh/self-hoster/configuration.md`

- [ ] **Step 1: Document new env vars**

Add these rows to English config tables:

```markdown
| `D1_RECOVERY_OUTBOX_ENABLED` | unset | Reserved for ADR-010 Phase 2. Set `1` to allow safe dual-mode D1 write failures to queue in `reports/D1/d1_recovery_outbox.jsonl`. |
| `D1_BATCHING_ENABLED` | unset | Reserved for ADR-010 Phase 3 safe-path micro-batching. Ordinary SQL remains synchronous. |
| `D1_FLUSH_INTERVAL_MS` | `250` | Maximum safe-batch wait window when D1 batching is enabled. |
| `D1_STARTUP_REPLAY_ENABLED` | unset | Reserved for ADR-010 Phase 4 startup replay. |
```

Add Chinese equivalents:

```markdown
| `D1_RECOVERY_OUTBOX_ENABLED` | 未设置 | ADR-010 Phase 2 预留。设为 `1` 时，dual 模式下 safe D1 写失败可进入 `reports/D1/d1_recovery_outbox.jsonl`。 |
| `D1_BATCHING_ENABLED` | 未设置 | ADR-010 Phase 3 safe-path micro-batching 预留。普通 SQL 仍同步。 |
| `D1_FLUSH_INTERVAL_MS` | `250` | 启用 D1 batching 后 safe batch 的最大等待窗口。 |
| `D1_STARTUP_REPLAY_ENABLED` | 未设置 | ADR-010 Phase 4 startup replay 预留。 |
```

- [ ] **Step 2: Run doc grep**

```bash
rg -n "D1_RECOVERY_OUTBOX_ENABLED|D1_BATCHING_ENABLED|D1_STARTUP_REPLAY_ENABLED" README.md README_CN.md docs/en/self-hoster/configuration.md docs/zh/self-hoster/configuration.md
```

Expected: hits in all four files.

- [ ] **Step 3: Commit**

```bash
git add README.md README_CN.md docs/en/self-hoster/configuration.md docs/zh/self-hoster/configuration.md
git commit -m "docs(d1): document access port phase controls"
```

---

## Task 7: Phase 1 Verification Gate

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/unit/test_d1_port.py tests/unit/test_d1_recovery.py tests/unit/test_d1_dual.py tests/unit/test_commit_session_bulk.py -v
```

Expected: PASS.

- [ ] **Step 2: Run storage regression tests**

```bash
pytest tests/unit/test_reconcile_d1_drift.py tests/unit/test_sync_d1_to_sqlite.py tests/unit/test_batch_c_movie_history_id.py -v
```

Expected: PASS.

- [ ] **Step 3: Verify CLI empty-outbox behavior**

```bash
python3 -m apps.cli.db.d1_recovery inspect --json
```

Expected: exit `0` and JSON with `pending_count` equal to `0` when no outbox exists.

- [ ] **Step 4: Final status**

```bash
git status --short
```

Expected: empty except user-owned pre-existing changes.
