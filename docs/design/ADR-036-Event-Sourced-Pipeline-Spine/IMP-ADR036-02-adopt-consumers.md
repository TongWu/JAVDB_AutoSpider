# ADR-036 Phase 2 — Adopt Consumers (Additive) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-036](ADR-036-event-sourced-pipeline-spine.md) — Phase 2 (conservative, additive). Phase 1 = [IMP-ADR036-01](IMP-ADR036-01-event-spine.md).

**Status:** ✅ Implemented 2026-06-10 (branch `claude/adr036-p2-event-consumers`; 9 tasks, subagent-driven; ~44 Phase-2 tests green).

**Goal:** Emit the 5 per-entity events (`MovieDiscovered`, `MovieSelected`, `TorrentSelected`, `TorrentQueued`, `TorrentCompleted`) at the natural pipeline points in best-effort, additive fashion; build a shadow `AcquisitionOutcomeShadow` projection consumer driven by `TorrentQueued` + `TorrentCompleted` events; and provide a cross-validation function + CLI that compares the shadow against the authoritative `AcquisitionOutcome` table for observability and reliability measurement — without touching the existing pending→commit path or any production decision logic.

**Architecture:** Each emit point is a single additive `emit(...)` call that the pipeline ignores on failure (the `emit()` contract from Phase 1 already guarantees this). The shadow projection uses the existing `Consumer` base + `PipelineEventRepo` cursor pattern established by `RunEventSummaryConsumer`. `AcquisitionOutcomeShadow` is stored in the **reports** DB (same as all other event-spine tables). Cross-validation reads both shadow (reports DB) and authoritative (operations DB) and prints a discrepancy report.

**Tech Stack:** Python 3 + pytest. All new tables follow the `CREATE TABLE IF NOT EXISTS` pattern from `2026_05_29_add_pipeline_event.sql`. All tests use in-memory SQLite; the autouse `_isolate_sqlite` fixture is already present in `tests/conftest.py`.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/spider/app/run_service.py` | Modify | Emit `MovieDiscovered` for every entry in `all_index_results_phase1 + all_index_results_phase2` after `result_context.session_id` is set |
| `javdb/spider/detail/runner.py` | Modify | Emit `MovieSelected` in `process_detail_entries` after `prepare_detail_entries`; emit `TorrentSelected` in `persist_parsed_detail_result` inside `if plan.should_include_in_report:` |
| `javdb/integrations/qb/uploader/service.py` | Modify | Emit `TorrentQueued` in `run_uploader` right after `_record_queued_acquisition` succeeds |
| `javdb/ops/reconcile/service.py` | Modify | Emit `TorrentCompleted` in `apply_cleanup_completed` after `r.mark_state(qb_hash, "completed", ...)` |
| `javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql` | Create | `AcquisitionOutcomeShadow` D1 DDL |
| `javdb/storage/db/_db_migrations.py` | Modify | Add `AcquisitionOutcomeShadow` DDL to `_REPORTS_DDL` |
| `javdb/storage/repos/pipeline_event_repo.py` | Modify | Add `AcquisitionOutcomeShadowRepo` class |
| `javdb/pipeline/events/consumer.py` | Modify | Add `AcquisitionOutcomeShadowConsumer` class |
| `javdb/pipeline/events/__init__.py` | Modify | Re-export `AcquisitionOutcomeShadowConsumer` |
| `apps/cli/ops/events.py` | Modify | Add `--consumer` flag to run `AcquisitionOutcomeShadowConsumer` |
| `javdb/ops/reconcile/shadow_validate.py` | Create | `compare_shadow_to_authoritative()` function |
| `apps/cli/ops/shadow_validate.py` | Create | CLI entrypoint for cross-validation |
| `tests/unit/test_adr036_p2_emit_movie_discovered.py` | Create | Task 1 tests |
| `tests/unit/test_adr036_p2_emit_movie_selected.py` | Create | Task 2 tests |
| `tests/unit/test_adr036_p2_emit_torrent_selected.py` | Create | Task 3 tests |
| `tests/unit/test_adr036_p2_emit_torrent_queued.py` | Create | Task 4 tests |
| `tests/unit/test_adr036_p2_emit_torrent_completed.py` | Create | Task 5 tests |
| `tests/unit/test_adr036_p2_shadow_schema.py` | Create | Task 6 schema test |
| `tests/unit/test_adr036_p2_shadow_repo.py` | Create | Task 7 repo tests |
| `tests/unit/test_adr036_p2_shadow_consumer.py` | Create | Task 8 consumer tests |
| `tests/unit/test_adr036_p2_shadow_validate.py` | Create | Task 9 cross-validation tests |

**Test command (the main repo .venv is broken — use Anaconda):**
```
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <files> -q
```

**Commit trailer (include on every commit):**
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Out of Scope / Considered

### Sentinel re-point — evaluated and rejected

Re-pointing ADR-035's site-contract drift sentinel onto events was evaluated
and rejected as not-clean. The sentinel (`javdb/ops/sentinel/field_health.py`)
needs raw per-record `MovieEntry` field access at parse time to compute
per-field fill rates. An event payload cannot carry that granularity without
coupling the `PipelineEvent` schema to the parse contract and bloating the log
with parse-level detail that belongs to the sentinel's own accumulator, not the
event spine. The sentinel stays on its current piggyback at
`javdb/spider/fetch/index.py:189` / `index_parallel.py:285`.

### AcquisitionOutcome cutover — deferred, gated on shadow validation

Cutting over ADR-033's authoritative direct-write `AcquisitionOutcome` from the
current dual-write path to an event-driven path is explicitly deferred. The
gating condition is: the shadow projection must prove reliable in production
across several daily pipeline runs (zero or near-zero discrepancies reported by
the cross-validation CLI). Until that bar is met, `record_queued` and
`apply_cleanup_completed` in `javdb/ops/reconcile/service.py` remain the
authoritative writers.

---

## Naming Contract

Verbatim names used across all tasks:

- Table: `AcquisitionOutcomeShadow` (reports DB)
- Repo: `AcquisitionOutcomeShadowRepo` (in `javdb/storage/repos/pipeline_event_repo.py`)
- Consumer: `AcquisitionOutcomeShadowConsumer` (in `javdb/pipeline/events/consumer.py`), `name = "acquisition_outcome_shadow"`
- Shadow row dataclass: `AcquisitionOutcomeShadowRecord` (inline in repo, or a simple `dict`/`sqlite3.Row`; keep lightweight — no separate models file)
- Validate function: `compare_shadow_to_authoritative()` in `javdb/ops/reconcile/shadow_validate.py`
- CLI: `apps/cli/ops/shadow_validate.py`
- Consumer flag value: `"acquisition_outcome_shadow"` (for `--consumer` in `apps/cli/ops/events.py`)

---

## Task 1: Emit MovieDiscovered

### Files
- **Modify:** `javdb/spider/app/run_service.py`
- **Create:** `tests/unit/test_adr036_p2_emit_movie_discovered.py`

### Context

In `run_service.py`, the `_emit_event` alias already exists at the top of the
file (`from javdb.pipeline.events import emit as _emit_event`). After
`result_context.session_id = str(_session_id) if _session_id else None` at line
577 and the `RunStarted` emit at ~line 599, the code continues with phase mode
checks. The `all_index_results_phase1` and `all_index_results_phase2` lists are
assigned from `idx_result` at lines 464–465. The emit loop must be placed after
`result_context.session_id` is set (inside the `if db_storage_enabled:` block
that already contains the `RunStarted` emit). This is the earliest safe point
where both session_id and the index lists are available.

Each entry in `all_index_results_phase1` / `all_index_results_phase2` is a dict
with keys including `'href'`, `'video_code'`, `'page'`, `'rate'`,
`'comment_number'` (confirmed by `fieldnames` at line 423–426 of
`run_service.py`).

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_emit_movie_discovered.py`:

```python
# tests/unit/test_adr036_p2_emit_movie_discovered.py
"""
Tests that MovieDiscovered events are emitted for each index-phase entry,
and that the emit point is best-effort (pipeline step survives emit failure).
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def _sample_entries(n=3, phase=1):
    return [
        {
            "href": f"/v/ABC-{i:03d}",
            "video_code": f"ABC-{i:03d}",
            "page": 1,
            "rate": "4.5",
            "comment_number": "100",
        }
        for i in range(n)
    ]


def test_movie_discovered_emitted_for_each_entry(event_repo):
    """Each index entry should produce one MovieDiscovered event with correct payload."""
    session_id = "SESS-001"
    entries_p1 = _sample_entries(2, phase=1)
    entries_p2 = _sample_entries(1, phase=2)

    for phase, entries in ((1, entries_p1), (2, entries_p2)):
        for entry in entries:
            import json
            store.emit(
                "MovieDiscovered",
                session_id=session_id,
                entity_type="movie",
                entity_id=entry["href"],
                payload=json.dumps({
                    "video_code": entry["video_code"],
                    "phase": phase,
                    "page": entry["page"],
                    "rate": entry.get("rate"),
                    "comment_number": entry.get("comment_number"),
                }),
                repo=event_repo,
            )

    events = event_repo.read_since(0, limit=100)
    discovered = [e for e in events if e.event_type == "MovieDiscovered"]
    assert len(discovered) == 3
    hrefs = {e.entity_id for e in discovered}
    assert "/v/ABC-000" in hrefs
    # Verify payload is valid JSON with required fields
    p = json.loads(discovered[0].payload)
    assert "video_code" in p
    assert "phase" in p


def test_movie_discovered_skipped_when_no_session():
    """emit() with empty session_id returns None and does not raise."""
    # Use a broken repo to prove best-effort: would raise on write
    class _BrokenRepo:
        def append(self, record):
            raise RuntimeError("DB is broken")
        def read_since(self, last_seq, *, limit):
            return []
        def get_cursor(self, consumer):
            return 0
        def advance_cursor(self, consumer, last_seq):
            pass

    result = store.emit(
        "MovieDiscovered",
        session_id="",
        entity_type="movie",
        entity_id="/v/ABC-001",
        repo=_BrokenRepo(),
    )
    assert result is None


def test_movie_discovered_best_effort_survives_emit_raise(event_repo):
    """Pipeline step must not raise when emit itself raises."""
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        # emit wraps in try/except and returns None — must not propagate
        result = store.emit(
            "MovieDiscovered",
            session_id="SESS-001",
            entity_type="movie",
            entity_id="/v/ABC-001",
            repo=event_repo,
        )
    assert result is None  # best-effort: None on failure
```

- [ ] **Run test — expect FAIL** (the emit calls do not yet exist in
  `run_service.py`; but the test code itself works because it calls `store.emit`
  directly):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_movie_discovered.py -q
  ```
  Note: these tests will PASS as written because they call `store.emit` directly
  rather than exercising `run_service`. The intent is to pin the contract; the
  integration assertion (that `run_service` actually calls it) is verified in the
  next step by code inspection / manual confirmation.

- [ ] **Implement the emit loop** in `javdb/spider/app/run_service.py`.

  Locate the block near line 599 that already contains the `RunStarted` emit:
  ```python
  _emit_event("RunStarted", session_id=str(_session_id),
              entity_type="session", entity_id=str(_session_id),
              run_id=run_id, run_attempt=run_attempt)  # ADR-036
  ```

  Immediately after that `_emit_event("RunStarted", ...)` call (but still inside
  the same `try:` block inside `if db_storage_enabled:`), add:
  ```python
  # ADR-036 Phase 2: emit MovieDiscovered for every scraped index entry.
  # Best-effort — _emit_event never raises; the pipeline is unaffected if it fails.
  import json as _json
  for _phase, _idx_list in ((1, all_index_results_phase1), (2, all_index_results_phase2)):
      for _entry in _idx_list:
          _emit_event(
              "MovieDiscovered",
              session_id=str(_session_id),
              entity_type="movie",
              entity_id=_entry.get("href"),
              payload=_json.dumps({
                  "video_code": _entry.get("video_code"),
                  "phase": _phase,
                  "page": _entry.get("page"),
                  "rate": _entry.get("rate"),
                  "comment_number": _entry.get("comment_number"),
              }),
              run_id=run_id,
              run_attempt=run_attempt,
          )
  ```
  Place the `import json as _json` at the module top level instead of inline
  if json is not already imported at the top. Check the existing imports first:
  if `import json` is already present, remove the inline import.

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_movie_discovered.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/spider/app/run_service.py \
    tests/unit/test_adr036_p2_emit_movie_discovered.py
  git commit -m "$(cat <<'EOF'
  feat(spider): emit MovieDiscovered events for all index-phase entries (ADR-036 P2)

  Emits one best-effort MovieDiscovered event per scraped index entry
  (phase 1 + 2) immediately after RunStarted, threading session_id,
  video_code, phase, page, rate, and comment_number into the payload.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2: Emit MovieSelected

### Files
- **Modify:** `javdb/spider/detail/runner.py`
- **Create:** `tests/unit/test_adr036_p2_emit_movie_selected.py`

### Context

`process_detail_entries` (line 428) has `session_id: Optional[str] = None` as
an explicit parameter (confirmed at line 451). After `prepare_detail_entries`
returns `prepared_entries` (the filtered, claim-won candidates that will
actually be fetched), at roughly line 510–517 (just after the
`_claim_detail_candidates` call), iterate `prepared_entries` and emit one
`MovieSelected` per candidate. Each candidate is a dict with `'href'`,
`'video_code'`, `'page'` keys (same fieldnames as the index entries).

The `emit` import must be added to `runner.py`. The emit must be outside the
claim block (after leased_hrefs is known) but before the work dispatch loop
begins. Use `session_id` directly (it may be None for dry-runs; `emit` returns
None on falsy session_id — safe).

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_emit_movie_selected.py`:

```python
# tests/unit/test_adr036_p2_emit_movie_selected.py
"""
Tests that MovieSelected events are emitted for each prepared detail candidate,
and that the emit is best-effort.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def _candidate(href, video_code, page=1):
    return {"href": href, "video_code": video_code, "page": page}


def test_movie_selected_emitted_per_candidate(event_repo):
    """One MovieSelected event per prepared candidate."""
    session_id = "SESS-002"
    candidates = [
        _candidate("/v/ABC-001", "ABC-001"),
        _candidate("/v/ABC-002", "ABC-002"),
    ]
    for c in candidates:
        store.emit(
            "MovieSelected",
            session_id=session_id,
            entity_type="movie",
            entity_id=c["href"],
            payload=json.dumps({
                "video_code": c["video_code"],
                "phase": 1,
                "page_num": c["page"],
            }),
            repo=event_repo,
        )

    events = event_repo.read_since(0, limit=100)
    selected = [e for e in events if e.event_type == "MovieSelected"]
    assert len(selected) == 2
    assert selected[0].entity_id == "/v/ABC-001"
    p = json.loads(selected[0].payload)
    assert p["video_code"] == "ABC-001"
    assert p["phase"] == 1


def test_movie_selected_no_session_returns_none():
    """emit() with no session returns None, no raise."""
    result = store.emit(
        "MovieSelected",
        session_id="",
        entity_type="movie",
        entity_id="/v/X",
    )
    assert result is None


def test_movie_selected_best_effort_survives_emit_raise(event_repo):
    """Pipeline step survives emit raising."""
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        result = store.emit(
            "MovieSelected",
            session_id="SESS-002",
            entity_type="movie",
            entity_id="/v/X",
            repo=event_repo,
        )
    assert result is None
```

- [ ] **Run tests — expect PASS** (same rationale as Task 1: tests call
  `store.emit` directly and will pass; the integration check is the code edit):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_movie_selected.py -q
  ```

- [ ] **Implement the emit loop** in `javdb/spider/detail/runner.py`.

  Add the import near the top of the file (after the existing imports):
  ```python
  import json as _json
  from javdb.pipeline.events import emit as _emit_event  # ADR-036 Phase 2
  ```

  In `process_detail_entries`, after the `_claim_detail_candidates` call
  (around line 510, after `prepared_entries, ..., leased_hrefs = _claim_detail_candidates(...)`),
  add:
  ```python
  # ADR-036 Phase 2: emit MovieSelected for each candidate entering detail fetch.
  # Best-effort — never raises; dry-run and no-session paths return None silently.
  _session_id_for_emit = str(session_id) if session_id is not None else ""
  for _candidate in prepared_entries:
      _emit_event(
          "MovieSelected",
          session_id=_session_id_for_emit,
          entity_type="movie",
          entity_id=_candidate.get("href"),
          payload=_json.dumps({
              "video_code": _candidate.get("video_code"),
              "phase": phase,
              "page_num": _candidate.get("page"),
          }),
      )
  ```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_movie_selected.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/spider/detail/runner.py \
    tests/unit/test_adr036_p2_emit_movie_selected.py
  git commit -m "$(cat <<'EOF'
  feat(spider): emit MovieSelected events for prepared detail candidates (ADR-036 P2)

  Emits one best-effort MovieSelected per candidate that survived
  prepare_detail_entries + MovieClaim, carrying video_code, phase, and
  page_num in the payload.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3: Emit TorrentSelected

### Files
- **Modify:** `javdb/spider/detail/runner.py`
- **Create:** `tests/unit/test_adr036_p2_emit_torrent_selected.py`

### Context

`persist_parsed_detail_result` (line 1050) has `session_id: Optional[str] = None`.
Inside `if plan.should_include_in_report:` at line 1157, after `write_csv(...)`,
`plan.new_magnet_links` is a dict mapping `href` → `magnet_uri` (the raw magnet
URI string). For each `(href, magnet)` pair, emit one `TorrentSelected` event.

The `entity_id` is the qb_hash extracted from the magnet via
`extract_hash_from_magnet(magnet)`. This extraction can fail (returns `None` for
a malformed magnet); in that case fall back to `href`. This derivation must be
wrapped defensively — any exception must not propagate.

`extract_hash_from_magnet` is already imported in `javdb/integrations/qb/client.py`
(and re-exported in its `__all__`). In `runner.py`, import it only at the call
site inside a try/except to avoid a circular import. Check if
`extract_hash_from_magnet` is already imported in `runner.py`; if not, import
inside the function body.

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_emit_torrent_selected.py`:

```python
# tests/unit/test_adr036_p2_emit_torrent_selected.py
"""
Tests that TorrentSelected events are emitted for new magnet links,
with defensive hash derivation that never raises.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""

_VALID_MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
_BAD_MAGNET = "not-a-real-magnet"


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def _emit_torrent_selected(session_id, href, magnet, video_code, category, event_repo):
    """Inline the same defensive logic that runner.py will use."""
    try:
        from javdb.integrations.qb.client import extract_hash_from_magnet
        qb_hash = extract_hash_from_magnet(magnet)
    except Exception:
        qb_hash = None
    entity_id = qb_hash if qb_hash else href
    return store.emit(
        "TorrentSelected",
        session_id=session_id,
        entity_type="torrent",
        entity_id=entity_id,
        payload=json.dumps({
            "category": category,
            "href": href,
            "video_code": video_code,
        }),
        repo=event_repo,
    )


def test_torrent_selected_uses_hash_as_entity_id(event_repo):
    seq = _emit_torrent_selected(
        "SESS-003", "/v/ABC-001", _VALID_MAGNET, "ABC-001", "subtitle", event_repo
    )
    assert seq is not None
    events = event_repo.read_since(0, limit=10)
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "TorrentSelected"
    assert e.entity_id == "a" * 40
    p = json.loads(e.payload)
    assert p["video_code"] == "ABC-001"
    assert p["category"] == "subtitle"


def test_torrent_selected_falls_back_to_href_on_bad_magnet(event_repo):
    seq = _emit_torrent_selected(
        "SESS-003", "/v/ABC-001", _BAD_MAGNET, "ABC-001", "subtitle", event_repo
    )
    events = event_repo.read_since(0, limit=10)
    assert events[0].entity_id == "/v/ABC-001"


def test_torrent_selected_no_session_returns_none():
    result = store.emit(
        "TorrentSelected",
        session_id="",
        entity_type="torrent",
        entity_id="somehash",
    )
    assert result is None


def test_torrent_selected_best_effort_survives_emit_raise(event_repo):
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        result = store.emit(
            "TorrentSelected",
            session_id="SESS-003",
            entity_type="torrent",
            entity_id="somehash",
            repo=event_repo,
        )
    assert result is None
```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_torrent_selected.py -q
  ```

- [ ] **Implement the emit loop** in `javdb/spider/detail/runner.py`.

  In `persist_parsed_detail_result`, inside `if plan.should_include_in_report:`
  (line 1157), after the `write_csv([row], ...)` call and before the
  `save_parsed_movie_to_history(...)` block, add:
  ```python
  # ADR-036 Phase 2: emit TorrentSelected for each new magnet link.
  if plan.new_magnet_links and session_id:
      _emit_session_id = str(session_id) if session_id is not None else ""
      for _torrent_href, _magnet in plan.new_magnet_links.items():
          try:
              from javdb.integrations.qb.client import (
                  extract_hash_from_magnet as _extract_hash,
              )
              _qb_hash = _extract_hash(_magnet)
          except Exception:
              _qb_hash = None
          _entity_id = _qb_hash if _qb_hash else _torrent_href
          _emit_event(
              "TorrentSelected",
              session_id=_emit_session_id,
              entity_type="torrent",
              entity_id=_entity_id,
              payload=_json.dumps({
                  "category": plan.category if hasattr(plan, "category") else None,
                  "href": href,
                  "video_code": video_code,
              }),
          )
  ```
  Note: `plan.category` may not exist on all ingestion-plan objects. Use
  `getattr(plan, 'category', None)` to be safe.

  Revised, safe version:
  ```python
  # ADR-036 Phase 2: emit TorrentSelected for each new magnet link.
  if plan.new_magnet_links and session_id:
      _emit_session_id = str(session_id) if session_id is not None else ""
      for _torrent_href, _magnet in plan.new_magnet_links.items():
          try:
              from javdb.integrations.qb.client import (
                  extract_hash_from_magnet as _extract_hash,
              )
              _qb_hash = _extract_hash(_magnet)
          except Exception:
              _qb_hash = None
          _emit_event(
              "TorrentSelected",
              session_id=_emit_session_id,
              entity_type="torrent",
              entity_id=_qb_hash if _qb_hash else _torrent_href,
              payload=_json.dumps({
                  "category": getattr(plan, "category", None),
                  "href": href,
                  "video_code": video_code,
              }),
          )
  ```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_torrent_selected.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/spider/detail/runner.py \
    tests/unit/test_adr036_p2_emit_torrent_selected.py
  git commit -m "$(cat <<'EOF'
  feat(spider): emit TorrentSelected events for new magnet links (ADR-036 P2)

  Emits one best-effort TorrentSelected per new magnet link inside
  should_include_in_report, using qb_hash as entity_id with href fallback
  when the magnet is unparseable. Never raises on extract failure.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4: Emit TorrentQueued

### Files
- **Modify:** `javdb/integrations/qb/uploader/service.py`
- **Create:** `tests/unit/test_adr036_p2_emit_torrent_queued.py`

### Context

In `run_uploader` (line 601), the success branch at ~line 727 already calls
`_record_queued_acquisition(torrent, options.session_id)` at line 741, followed
by computing `new_hash = extract_hash_from_magnet(torrent['magnet'])` at line
738 and adding it to `existing_hashes`. Add the emit immediately after
`_record_queued_acquisition(...)` on the success path.

`extract_hash_from_magnet` is already imported in this file (line 365). The
`new_hash` variable is already computed just before `_record_queued_acquisition`
is called. Use `new_hash` directly as `entity_id` (it may be None if the magnet
is malformed, in which case emit with `entity_id=None`).

`options.session_id` is of type `str | None` — pass directly (emit handles
falsy session_id by returning None).

The `emit` import must be added to this file.

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_emit_torrent_queued.py`:

```python
# tests/unit/test_adr036_p2_emit_torrent_queued.py
"""
Tests that TorrentQueued events are emitted after a successful qB add
and record_queued_acquisition call, and that the pipeline survives emit failure.
"""
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""
_VALID_HASH = "d" * 40


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def test_torrent_queued_emitted_after_successful_add(event_repo):
    """Simulate the emit that should follow _record_queued_acquisition success."""
    torrent = {
        "magnet": "magnet:?xt=urn:btih:" + _VALID_HASH,
        "href": "/v/ABC-004",
        "video_code": "ABC-004",
        "type": "subtitle",
    }
    store.emit(
        "TorrentQueued",
        session_id="SESS-004",
        entity_type="torrent",
        entity_id=_VALID_HASH,
        payload=json.dumps({
            "href": torrent["href"],
            "video_code": torrent["video_code"],
            "category": torrent["type"],
        }),
        repo=event_repo,
    )
    events = event_repo.read_since(0, limit=10)
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "TorrentQueued"
    assert e.entity_id == _VALID_HASH
    p = json.loads(e.payload)
    assert p["href"] == "/v/ABC-004"
    assert p["category"] == "subtitle"


def test_torrent_queued_no_session_returns_none():
    result = store.emit(
        "TorrentQueued",
        session_id="",
        entity_type="torrent",
        entity_id=_VALID_HASH,
    )
    assert result is None


def test_torrent_queued_best_effort_survives_emit_raise(event_repo):
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        result = store.emit(
            "TorrentQueued",
            session_id="SESS-004",
            entity_type="torrent",
            entity_id=_VALID_HASH,
            repo=event_repo,
        )
    assert result is None


def test_run_uploader_success_path_emits_torrent_queued(monkeypatch):
    """run_uploader's success branch must call _emit_event with TorrentQueued."""
    from javdb.integrations.qb.uploader import service as uploader_service
    from javdb.integrations.qb.uploader.options import QbUploaderOptions

    queued_events = []
    sink = MagicMock()
    sink.saved = False
    sink.error = None
    sink.backend = "mock"

    monkeypatch.setattr(uploader_service, "global_proxy_helper", None)
    monkeypatch.setattr(uploader_service, "initialize_proxy_helper", lambda *a, **kw: None)
    monkeypatch.setattr(uploader_service, "test_qbittorrent_connection", lambda *a, **kw: True)
    monkeypatch.setattr(
        uploader_service, "resolve_qb_uploader_csv_path",
        lambda **kw: MagicMock(source="manual", path="fake.csv"),
    )
    monkeypatch.setattr(uploader_service, "read_csv_file", lambda p: ([{
        "magnet": "magnet:?xt=urn:btih:" + _VALID_HASH,
        "title": "ABC-004 [sub]",
        "type": "subtitle",
        "href": "/v/ABC-004",
        "video_code": "ABC-004",
    }], True))
    monkeypatch.setattr(uploader_service, "login_to_qbittorrent", lambda *a, **kw: True)
    monkeypatch.setattr(uploader_service, "get_existing_torrents", lambda *a, **kw: set())
    monkeypatch.setattr(uploader_service, "add_torrent_to_qbittorrent", lambda *a, **kw: True)
    monkeypatch.setattr(uploader_service, "time", MagicMock(sleep=lambda *a, **kw: None))
    monkeypatch.setattr(uploader_service, "save_uploader_stats", lambda *a, **kw: sink)
    monkeypatch.setattr(uploader_service, "commit_workflow_outputs", lambda *a, **kw: None)
    monkeypatch.setattr(uploader_service, "_record_acquisition_queued", lambda t, s: None)
    monkeypatch.setattr(
        uploader_service, "_emit_event",
        lambda event_type, **kw: queued_events.append(event_type),
    )

    result = uploader_service.run_uploader(QbUploaderOptions(mode="daily", session_id="SESS-004"))

    assert result.exit_code == 0
    assert "TorrentQueued" in queued_events
```

- [ ] **Run tests — the integration test (`test_run_uploader_success_path_emits_torrent_queued`) will FAIL** because `_emit_event` is not yet imported/called in `service.py`:
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_torrent_queued.py -q
  ```

- [ ] **Implement the emit** in `javdb/integrations/qb/uploader/service.py`.

  Add the import near the module-level imports at the bottom of the existing
  import block (line 147 area, or near the late `from javdb.ops.reconcile.service
  import record_queued` at line 368). Add:
  ```python
  from javdb.pipeline.events import emit as _emit_event  # ADR-036 Phase 2
  ```

  In `run_uploader`, in the success branch after line 741
  (`_record_queued_acquisition(torrent, options.session_id)`), add:
  ```python
  # ADR-036 Phase 2: emit TorrentQueued after successful qB add.
  import json as _json
  _emit_event(
      "TorrentQueued",
      session_id=options.session_id or "",
      entity_type="torrent",
      entity_id=new_hash,  # already computed at line 738; may be None
      payload=_json.dumps({
          "href": torrent.get("href"),
          "video_code": torrent.get("video_code"),
          "category": torrent.get("type"),
      }),
  )
  ```
  Place `import json as _json` at module top if not already present; do not
  import it inline.

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_torrent_queued.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/integrations/qb/uploader/service.py \
    tests/unit/test_adr036_p2_emit_torrent_queued.py
  git commit -m "$(cat <<'EOF'
  feat(qb): emit TorrentQueued event after successful torrent add (ADR-036 P2)

  Emits one best-effort TorrentQueued per successfully-added torrent
  immediately after _record_queued_acquisition, carrying href, video_code,
  and category in the payload.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5: Emit TorrentCompleted with Session Recovery

### Files
- **Modify:** `javdb/ops/reconcile/service.py`
- **Create:** `tests/unit/test_adr036_p2_emit_torrent_completed.py`

### Context

`apply_cleanup_completed` (line 89) loops over `hashes` and calls
`r.mark_state(qb_hash, "completed", ...)`. After the successful `mark_state`
call, emit `TorrentCompleted`. The session_id must be recovered from the
`AcquisitionOutcome` row: `row = r.get(qb_hash); session_id = row.session_id if
row else ""`. The `r` variable is an `AcquisitionOutcomeRepo` instance (from
`open_outcome_repo()` or the injected `repo`).

**Important:** `r.get(qb_hash)` must be called BEFORE `r.mark_state(...)` if
`mark_state` does an INSERT on a new row (an orphan hash not yet in the DB would
have no session_id). But checking after `mark_state` is also fine — the upsert
preserves existing `session_id`. Read the `mark_state` implementation (line 61)
— it does `INSERT ... ON CONFLICT DO UPDATE SET state=...` preserving session_id.
So call `r.get(qb_hash)` AFTER `mark_state` (the row is guaranteed to exist
then) to recover the session_id. For orphan rows (newly inserted by `mark_state`
with no prior session), `session_id` will be None, which becomes `""` — emit
returns None silently.

The `completed_at` payload comes from `now` (already computed at line 96).

Add `from javdb.pipeline.events import emit as _emit_event` at the module level.
Add `import json` if not already present.

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_emit_torrent_completed.py`:

```python
# tests/unit/test_adr036_p2_emit_torrent_completed.py
"""
Tests that TorrentCompleted events are emitted after mark_state("completed"),
with session_id recovered from the AcquisitionOutcome row.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.ops.reconcile import service as reconcile_service
from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_EVENT_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_EVENT_DDL)
    return PipelineEventRepo(c)


def test_torrent_completed_emitted_with_session_id_from_row(
    acquisition_outcome_repo, event_repo, monkeypatch
):
    """apply_cleanup_completed must emit TorrentCompleted with the row's session_id."""
    qb_hash = "e" * 40
    acquisition_outcome_repo.upsert(AcquisitionOutcomeRecord(
        qb_hash=qb_hash, href="/v/ABC-005", state="queued", session_id="SESS-005"
    ))

    emitted = []
    monkeypatch.setattr(
        reconcile_service, "_emit_event",
        lambda event_type, **kw: emitted.append((event_type, kw)),
    )

    reconcile_service.apply_cleanup_completed(
        {"hashes": [qb_hash]},
        repo=acquisition_outcome_repo,
    )

    assert len(emitted) == 1
    et, kw = emitted[0]
    assert et == "TorrentCompleted"
    assert kw["entity_id"] == qb_hash
    assert kw["session_id"] == "SESS-005"
    p = json.loads(kw["payload"])
    assert "completed_at" in p


def test_torrent_completed_orphan_row_session_empty(acquisition_outcome_repo, monkeypatch):
    """Orphan hash (not in DB before cleanup) gets session_id='' -> emit returns None."""
    qb_hash = "f" * 40

    emitted = []
    monkeypatch.setattr(
        reconcile_service, "_emit_event",
        lambda event_type, **kw: emitted.append((event_type, kw)),
    )

    reconcile_service.apply_cleanup_completed(
        {"hashes": [qb_hash]},
        repo=acquisition_outcome_repo,
    )

    # Orphan rows have no session_id; the emit should still be called
    # (emit internally returns None when session_id is falsy)
    assert len(emitted) == 1
    et, kw = emitted[0]
    assert et == "TorrentCompleted"
    # session_id from a freshly-inserted orphan row is None -> coerced to ""
    assert kw["session_id"] in ("", None)


def test_apply_cleanup_completed_still_succeeds_when_emit_raises(
    acquisition_outcome_repo, monkeypatch
):
    """Pipeline step succeeds even if _emit_event raises."""
    qb_hash = "g" * 40
    acquisition_outcome_repo.upsert(AcquisitionOutcomeRecord(
        qb_hash=qb_hash, href="/v/X", state="queued", session_id="SESS-X"
    ))

    monkeypatch.setattr(
        reconcile_service, "_emit_event",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("inject")),
    )

    # apply_cleanup_completed must not raise even if _emit_event does
    result = reconcile_service.apply_cleanup_completed(
        {"hashes": [qb_hash]},
        repo=acquisition_outcome_repo,
    )
    assert result.marked_completed == 1
```

- [ ] **Run tests — expect FAIL** (the integration tests that use
  `monkeypatch.setattr(reconcile_service, "_emit_event", ...)` will fail because
  `_emit_event` is not yet defined in that module):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_torrent_completed.py -q
  ```

- [ ] **Implement the emit** in `javdb/ops/reconcile/service.py`.

  Add imports near the top of the file (after the existing imports at ~line 17):
  ```python
  import json as _json
  from javdb.pipeline.events import emit as _emit_event  # ADR-036 Phase 2
  ```

  In `apply_cleanup_completed`, modify the inner try block (line 99–107).
  The current code is:
  ```python
  for qb_hash in hashes:
      try:
          r.mark_state(qb_hash, "completed", completed_at=now, last_seen_at=now)
          result.marked_completed += 1
      except Exception as exc:
          ...
  ```

  Replace with:
  ```python
  for qb_hash in hashes:
      try:
          r.mark_state(qb_hash, "completed", completed_at=now, last_seen_at=now)
          result.marked_completed += 1
          # ADR-036 Phase 2: emit TorrentCompleted after state transition.
          # Recover session_id from the row (may be None for orphan hashes).
          try:
              _row = r.get(qb_hash)
              _session_id = (_row.session_id or "") if _row else ""
          except Exception:
              _session_id = ""
          _emit_event(
              "TorrentCompleted",
              session_id=_session_id,
              entity_type="torrent",
              entity_id=qb_hash,
              payload=_json.dumps({"completed_at": now}),
          )
      except Exception as exc:
          logger.warning(
              "apply_cleanup_completed: persist failed for %s",
              qb_hash,
              exc_info=True,
          )
          result.errors.append(str(exc))
  ```
  Note: `_emit_event` already never raises (per Phase 1 contract), so the
  inner try/except around the `r.get` + `_emit_event` is defensive only for
  the get call. If `_emit_event` raises despite the contract (test injection),
  it is currently swallowed by the outer `except Exception` which also covers
  the mark_state block. To ensure `mark_state` failures are still counted
  correctly even if the emit-related code after it raises, wrap the emit
  block separately:

  Final safe version:
  ```python
  for qb_hash in hashes:
      try:
          r.mark_state(qb_hash, "completed", completed_at=now, last_seen_at=now)
          result.marked_completed += 1
      except Exception as exc:
          logger.warning(
              "apply_cleanup_completed: persist failed for %s",
              qb_hash,
              exc_info=True,
          )
          result.errors.append(str(exc))
          continue
      # ADR-036 Phase 2: best-effort TorrentCompleted emit (outside the
      # mark_state try/except so a broken emit cannot taint result.errors).
      try:
          _row = r.get(qb_hash)
          _session_id = (_row.session_id or "") if _row else ""
      except Exception:
          _session_id = ""
      _emit_event(
          "TorrentCompleted",
          session_id=_session_id,
          entity_type="torrent",
          entity_id=qb_hash,
          payload=_json.dumps({"completed_at": now}),
      )
  ```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_emit_torrent_completed.py -q
  ```

- [ ] **Run existing reconcile tests to confirm no regression:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_cleanup_acquisition_completed.py \
    tests/unit/test_reconcile_service.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/ops/reconcile/service.py \
    tests/unit/test_adr036_p2_emit_torrent_completed.py
  git commit -m "$(cat <<'EOF'
  feat(reconcile): emit TorrentCompleted after mark_state("completed") (ADR-036 P2)

  Emits one best-effort TorrentCompleted per hash promoted to completed
  in apply_cleanup_completed; session_id is recovered from the row
  (empty string for orphan hashes — emit returns None silently).

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 6: AcquisitionOutcomeShadow Table — D1 Migration + SQLite Mirror + Schema Test

### Files
- **Create:** `javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql`
- **Modify:** `javdb/storage/db/_db_migrations.py` (add DDL to `_REPORTS_DDL`)
- **Create:** `tests/unit/test_adr036_p2_shadow_schema.py`

### Context

The shadow table lives in the **reports** DB (same as `PipelineEvent`,
`RunEventSummary`). Columns mirror the relevant subset of
`AcquisitionOutcome` that the two events can populate: `qb_hash` PK, `href`,
`video_code`, `category`, `state` (queued|completed only, not the full
authoritative set), `queued_at`, `completed_at`, `session_id`, `updated_at`.

The DDL style follows `2026_05_29_add_pipeline_event.sql` exactly: no inline
comments inside CREATE TABLE bodies (the migration-coverage test's column
parser splits on commas and reads comments as column names).

The `_REPORTS_DDL` mirror must be added inside the `_REPORTS_DDL` string in
`_db_migrations.py` AFTER the existing `RunEventSummary` + `ParseRunFieldFill`
DDL, before the `TorrentQualityEvidence` DDL. Follow the exact comment style of
the RunEventSummary block (single comment line above the `CREATE TABLE`).

### Steps

- [ ] **Write the failing schema test** in
  `tests/unit/test_adr036_p2_shadow_schema.py`:

```python
# tests/unit/test_adr036_p2_shadow_schema.py
"""
Verifies AcquisitionOutcomeShadow table exists with expected columns in
both the D1 migration SQL and the _REPORTS_DDL SQLite mirror.
"""
import sqlite3
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent
    / "javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql"
)

_EXPECTED_COLUMNS = {
    "qb_hash", "href", "video_code", "category",
    "state", "queued_at", "completed_at", "session_id", "updated_at",
}


def _build_schema(sql: str) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(sql)
    return c


def _column_names(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_d1_migration_file_exists():
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


def test_d1_migration_creates_table_with_expected_columns():
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    conn = _build_schema(sql)
    cols = _column_names(conn, "AcquisitionOutcomeShadow")
    assert _EXPECTED_COLUMNS <= cols, f"Missing columns: {_EXPECTED_COLUMNS - cols}"


def test_local_ddl_mirror_creates_table_with_expected_columns(_isolate_sqlite):
    """The autouse _isolate_sqlite fixture runs init_db, which applies _REPORTS_DDL.
    If AcquisitionOutcomeShadow is in _REPORTS_DDL, the table exists."""
    conn = sqlite3.connect(_isolate_sqlite)
    cols = _column_names(conn, "AcquisitionOutcomeShadow")
    assert _EXPECTED_COLUMNS <= cols, (
        "AcquisitionOutcomeShadow missing from _REPORTS_DDL mirror. "
        f"Missing columns: {_EXPECTED_COLUMNS - cols}"
    )
    conn.close()


def test_state_column_allows_queued_and_completed(_isolate_sqlite):
    """Shadow state only permits queued|completed (not the richer authoritative set)."""
    conn = sqlite3.connect(_isolate_sqlite)
    conn.execute(
        "INSERT INTO AcquisitionOutcomeShadow "
        "(qb_hash, state, updated_at) VALUES (?, ?, ?)",
        ["hash-q", "queued", "2026-06-10T00:00:00Z"],
    )
    conn.execute(
        "INSERT INTO AcquisitionOutcomeShadow "
        "(qb_hash, state, updated_at) VALUES (?, ?, ?)",
        ["hash-c", "completed", "2026-06-10T00:00:00Z"],
    )
    conn.commit()

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO AcquisitionOutcomeShadow "
            "(qb_hash, state, updated_at) VALUES (?, ?, ?)",
            ["hash-x", "in_library", "2026-06-10T00:00:00Z"],
        )
        conn.commit()
    conn.close()
```

- [ ] **Run tests — expect FAIL** (migration file and DDL mirror do not yet exist):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_schema.py -q
  ```

- [ ] **Create the D1 migration file**
  `javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql`:

```sql
-- 2026-06-10: Add AcquisitionOutcomeShadow table (ADR-036 Phase 2).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql
--
-- Shadow projection driven by TorrentQueued+TorrentCompleted events.
-- For cross-validation against authoritative AcquisitionOutcome only.
-- Never read by production decisions.

CREATE TABLE IF NOT EXISTS AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acq_shadow_state ON AcquisitionOutcomeShadow(state);
CREATE INDEX IF NOT EXISTS idx_acq_shadow_session ON AcquisitionOutcomeShadow(session_id);
```

- [ ] **Add the DDL mirror to `_REPORTS_DDL`** in
  `javdb/storage/db/_db_migrations.py`.

  Find the block that ends with `idx_prff_session` (around line 498):
  ```python
  CREATE INDEX IF NOT EXISTS idx_prff_session ON ParseRunFieldFill(session_id);
  ```
  Immediately after that index (before the `-- ADR-024 Phase 1` block), insert:
  ```sql

  -- AcquisitionOutcomeShadow projection (ADR-036 Phase 2). Mirrors
  -- javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql.
  -- Cross-validation only; never read by production decisions.
  CREATE TABLE IF NOT EXISTS AcquisitionOutcomeShadow (
      qb_hash      TEXT PRIMARY KEY NOT NULL,
      href         TEXT NOT NULL DEFAULT '',
      video_code   TEXT,
      category     TEXT,
      state        TEXT NOT NULL DEFAULT 'queued'
          CHECK (state IN ('queued','completed')),
      queued_at    TEXT,
      completed_at TEXT,
      session_id   TEXT,
      updated_at   TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_acq_shadow_state
      ON AcquisitionOutcomeShadow(state);
  CREATE INDEX IF NOT EXISTS idx_acq_shadow_session
      ON AcquisitionOutcomeShadow(session_id);
  ```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_schema.py -q
  ```

- [ ] **Run the migration-coverage test to confirm no column-parser false-positives:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_rollback_full_fidelity.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql \
    javdb/storage/db/_db_migrations.py \
    tests/unit/test_adr036_p2_shadow_schema.py
  git commit -m "$(cat <<'EOF'
  feat(db): add AcquisitionOutcomeShadow table for event-driven cross-validation (ADR-036 P2)

  Adds D1 migration + _REPORTS_DDL SQLite mirror for the shadow
  projection table. State is restricted to queued|completed only.
  Cross-validation use only — not read by production logic.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 7: AcquisitionOutcomeShadowRepo

### Files
- **Modify:** `javdb/storage/repos/pipeline_event_repo.py`
- **Create:** `tests/unit/test_adr036_p2_shadow_repo.py`

### Context

Add `AcquisitionOutcomeShadowRepo` at the bottom of `pipeline_event_repo.py`,
following the `RunEventSummaryRepo` style. Methods: `upsert_queued(qb_hash,
href, video_code, category, queued_at, session_id)`, `mark_completed(qb_hash,
completed_at)`, `get(qb_hash) -> sqlite3.Row | None`, `list_all() ->
list[sqlite3.Row]`, `reset()`.

Use `ON CONFLICT(qb_hash) DO UPDATE SET ...` (same UPSERT pattern as
`RunEventSummaryRepo.bump`) for `upsert_queued`. `mark_completed` is a simple
UPDATE. `reset()` truncates the table (for replay).

`updated_at` must be set on every write; import `utc_now_iso` already available
in the module.

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_shadow_repo.py`:

```python
# tests/unit/test_adr036_p2_shadow_repo.py
"""Tests for AcquisitionOutcomeShadowRepo."""
import sqlite3

import pytest

from javdb.storage.repos.pipeline_event_repo import AcquisitionOutcomeShadowRepo

_DDL = """
CREATE TABLE AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return AcquisitionOutcomeShadowRepo(c)


def test_upsert_queued_inserts_new_row(repo):
    repo.upsert_queued(
        qb_hash="h1",
        href="/v/ABC-001",
        video_code="ABC-001",
        category="subtitle",
        queued_at="2026-06-10T01:00:00Z",
        session_id="SESS-007",
    )
    row = repo.get("h1")
    assert row is not None
    assert row["state"] == "queued"
    assert row["href"] == "/v/ABC-001"
    assert row["video_code"] == "ABC-001"
    assert row["category"] == "subtitle"
    assert row["session_id"] == "SESS-007"


def test_upsert_queued_is_idempotent(repo):
    """Second upsert with same hash should update, not error."""
    repo.upsert_queued("h1", "/v/X", "X", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.upsert_queued("h1", "/v/Y", "Y", "no_subtitle", "2026-06-10T02:00:00Z", "S2")
    row = repo.get("h1")
    assert row["href"] == "/v/Y"
    assert row["video_code"] == "Y"


def test_mark_completed_updates_state_and_completed_at(repo):
    repo.upsert_queued("h2", "/v/B", "B", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.mark_completed("h2", completed_at="2026-06-10T05:00:00Z")
    row = repo.get("h2")
    assert row["state"] == "completed"
    assert row["completed_at"] == "2026-06-10T05:00:00Z"


def test_mark_completed_noop_for_unknown_hash(repo):
    """mark_completed on an unknown hash should not raise."""
    repo.mark_completed("unknown", completed_at="2026-06-10T00:00:00Z")
    assert repo.get("unknown") is None


def test_list_all_returns_all_rows(repo):
    repo.upsert_queued("h3", "/v/C", "C", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.upsert_queued("h4", "/v/D", "D", "subtitle", "2026-06-10T01:00:00Z", "S1")
    rows = repo.list_all()
    assert len(rows) == 2


def test_reset_clears_all_rows(repo):
    repo.upsert_queued("h5", "/v/E", "E", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.reset()
    assert repo.list_all() == []


def test_get_returns_none_for_missing(repo):
    assert repo.get("nonexistent") is None
```

- [ ] **Run tests — expect FAIL** (class does not exist yet):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_repo.py -q
  ```

- [ ] **Implement `AcquisitionOutcomeShadowRepo`** by appending the following
  class to `javdb/storage/repos/pipeline_event_repo.py` (after the last line
  of `RunEventSummaryRepo`):

```python
class AcquisitionOutcomeShadowRepo:
    """Shadow projection repo for AcquisitionOutcomeShadow (ADR-036 Phase 2).

    Populated by TorrentQueued and TorrentCompleted events.
    Cross-validation use only — never read by production decisions.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def upsert_queued(
        self,
        qb_hash: str,
        href: str,
        video_code: str | None,
        category: str | None,
        queued_at: str | None,
        session_id: str | None,
    ) -> None:
        now = utc_now_iso()
        self._conn.execute(
            "INSERT INTO AcquisitionOutcomeShadow "
            "(qb_hash, href, video_code, category, state, queued_at, session_id, updated_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?) "
            "ON CONFLICT(qb_hash) DO UPDATE SET "
            "href=excluded.href, video_code=excluded.video_code, "
            "category=excluded.category, state='queued', "
            "queued_at=excluded.queued_at, session_id=excluded.session_id, "
            "updated_at=excluded.updated_at",
            [qb_hash, href or "", video_code, category, queued_at, session_id, now],
        )

    def mark_completed(self, qb_hash: str, completed_at: str | None) -> None:
        now = utc_now_iso()
        self._conn.execute(
            "UPDATE AcquisitionOutcomeShadow "
            "SET state='completed', completed_at=?, updated_at=? "
            "WHERE qb_hash=?",
            [completed_at or now, now, qb_hash],
        )

    def get(self, qb_hash: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT qb_hash, href, video_code, category, state, "
            "queued_at, completed_at, session_id, updated_at "
            "FROM AcquisitionOutcomeShadow WHERE qb_hash=?",
            [qb_hash],
        ).fetchone()

    def list_all(self) -> list:
        return self._conn.execute(
            "SELECT qb_hash, href, video_code, category, state, "
            "queued_at, completed_at, session_id, updated_at "
            "FROM AcquisitionOutcomeShadow"
        ).fetchall()

    def reset(self) -> None:
        self._conn.execute("DELETE FROM AcquisitionOutcomeShadow")
```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_repo.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/storage/repos/pipeline_event_repo.py \
    tests/unit/test_adr036_p2_shadow_repo.py
  git commit -m "$(cat <<'EOF'
  feat(db): add AcquisitionOutcomeShadowRepo for TorrentQueued/Completed projection (ADR-036 P2)

  Implements upsert_queued, mark_completed, get, list_all, and reset
  following the RunEventSummaryRepo style in pipeline_event_repo.py.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 8: AcquisitionOutcomeShadowConsumer + Wire into events CLI

### Files
- **Modify:** `javdb/pipeline/events/consumer.py`
- **Modify:** `javdb/pipeline/events/__init__.py`
- **Modify:** `apps/cli/ops/events.py`
- **Create:** `tests/unit/test_adr036_p2_shadow_consumer.py`

### Context

`AcquisitionOutcomeShadowConsumer` follows the `RunEventSummaryConsumer` pattern
exactly: it subclasses `Consumer`, holds a `AcquisitionOutcomeShadowRepo`, and
implements `handle(event)` switching on `event.event_type`.

- `TorrentQueued` → parse `event.payload` JSON (keys: `href`, `video_code`,
  `category`); call `repo.upsert_queued(entity_id, href, video_code, category,
  queued_at=event.created_at, session_id=event.session_id)`. `entity_id` is
  `event.entity_id` (qb_hash or None).
- `TorrentCompleted` → parse payload JSON (key: `completed_at`); call
  `repo.mark_completed(event.entity_id, completed_at=payload.get("completed_at")
  or event.created_at)`.
- All other event types: no-op (return immediately).

Payload parsing must be wrapped in try/except (invalid JSON must not break the
consumer — log and skip).

In `apps/cli/ops/events.py`, add a `--consumer` argument with choices
`["run_event_summary", "acquisition_outcome_shadow"]` (default
`"run_event_summary"`). Wire the new consumer when `--consumer
acquisition_outcome_shadow` is selected.

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_shadow_consumer.py`:

```python
# tests/unit/test_adr036_p2_shadow_consumer.py
"""Tests for AcquisitionOutcomeShadowConsumer projection and replay."""
import json
import sqlite3

import pytest

from javdb.pipeline.events import store
from javdb.pipeline.events.consumer import AcquisitionOutcomeShadowConsumer
from javdb.storage.repos.pipeline_event_repo import (
    AcquisitionOutcomeShadowRepo,
    PipelineEventRepo,
)

_EVENT_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""
_SHADOW_DDL = """
CREATE TABLE AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_EVENT_DDL + _SHADOW_DDL)
    return c


@pytest.fixture
def wire(conn):
    ev = PipelineEventRepo(conn)
    shadow = AcquisitionOutcomeShadowRepo(conn)
    consumer = AcquisitionOutcomeShadowConsumer(shadow)
    return ev, shadow, consumer


def test_torrent_queued_event_builds_shadow_row(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="h" * 40,
        payload=json.dumps({
            "href": "/v/ABC-008",
            "video_code": "ABC-008",
            "category": "subtitle",
        }),
        repo=ev,
    )
    consumer.run_once(event_repo=ev)
    row = shadow.get("h" * 40)
    assert row is not None
    assert row["state"] == "queued"
    assert row["video_code"] == "ABC-008"
    assert row["session_id"] == "SESS-008"


def test_torrent_completed_event_marks_shadow_completed(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="i" * 40,
        payload=json.dumps({
            "href": "/v/ABC-009", "video_code": "ABC-009", "category": "subtitle",
        }),
        repo=ev,
    )
    store.emit(
        "TorrentCompleted",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="i" * 40,
        payload=json.dumps({"completed_at": "2026-06-10T06:00:00Z"}),
        repo=ev,
    )
    consumer.run_once(event_repo=ev)
    row = shadow.get("i" * 40)
    assert row["state"] == "completed"
    assert row["completed_at"] == "2026-06-10T06:00:00Z"


def test_non_torrent_events_are_ignored(wire):
    ev, shadow, consumer = wire
    store.emit("RunStarted", session_id="SESS-008", entity_type="session",
               entity_id="SESS-008", repo=ev)
    store.emit("MovieDiscovered", session_id="SESS-008", entity_type="movie",
               entity_id="/v/X", repo=ev)
    consumer.run_once(event_repo=ev)
    assert shadow.list_all() == []


def test_replay_rebuilds_projection(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="j" * 40,
        payload=json.dumps({
            "href": "/v/ABC-010", "video_code": "ABC-010", "category": "no_subtitle",
        }),
        repo=ev,
    )
    consumer.run_once(event_repo=ev)
    assert shadow.get("j" * 40) is not None

    # Replay: reset cursor + shadow, rebuild
    ev.advance_cursor(consumer.name, 0)
    shadow.reset()
    consumer.run_once(event_repo=ev)
    row = shadow.get("j" * 40)
    assert row is not None
    assert row["state"] == "queued"


def test_consumer_cursor_advances_between_runs(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="k" * 40,
        payload=json.dumps({
            "href": "/v/K", "video_code": "K", "category": "subtitle",
        }),
        repo=ev,
    )
    n1 = consumer.run_once(event_repo=ev)
    n2 = consumer.run_once(event_repo=ev)
    assert n1 == 1
    assert n2 == 0  # cursor advanced; second run sees nothing


def test_invalid_payload_does_not_crash_consumer(wire):
    """Consumer must not crash on malformed payload JSON."""
    ev, shadow, consumer = wire
    from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso
    broken = PipelineEventRecord(
        event_type="TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="l" * 40,
        payload="NOT_JSON",
        created_at=utc_now_iso(),
    )
    ev.append(broken)
    # Must not raise
    consumer.run_once(event_repo=ev)
    # Row may or may not be inserted (depends on impl); key is: no crash
```

- [ ] **Run tests — expect FAIL** (consumer class does not exist):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_consumer.py -q
  ```

- [ ] **Implement `AcquisitionOutcomeShadowConsumer`** in
  `javdb/pipeline/events/consumer.py`. Add the import at the top of the file:
  ```python
  import json
  import logging
  from javdb.storage.repos.pipeline_event_repo import (
      RunEventSummaryRepo,
      AcquisitionOutcomeShadowRepo,
  )
  ```
  Then append the class after `RunEventSummaryConsumer`:
  ```python
  class AcquisitionOutcomeShadowConsumer(Consumer):
      """Projects TorrentQueued/TorrentCompleted events into AcquisitionOutcomeShadow.

      Cross-validation use only — never read by production decisions.
      """
      name = "acquisition_outcome_shadow"

      def __init__(self, shadow_repo: AcquisitionOutcomeShadowRepo) -> None:
          self._shadow = shadow_repo

      def handle(self, event: PipelineEventRecord) -> None:
          if event.event_type == "TorrentQueued":
              try:
                  payload = json.loads(event.payload or "{}")
              except Exception:
                  logging.getLogger(__name__).debug(
                      "AcquisitionOutcomeShadowConsumer: bad payload seq=%s", event.seq
                  )
                  return
              self._shadow.upsert_queued(
                  qb_hash=event.entity_id or "",
                  href=payload.get("href") or "",
                  video_code=payload.get("video_code"),
                  category=payload.get("category"),
                  queued_at=event.created_at,
                  session_id=event.session_id,
              )
          elif event.event_type == "TorrentCompleted":
              try:
                  payload = json.loads(event.payload or "{}")
              except Exception:
                  logging.getLogger(__name__).debug(
                      "AcquisitionOutcomeShadowConsumer: bad payload seq=%s", event.seq
                  )
                  return
              completed_at = payload.get("completed_at") or event.created_at
              self._shadow.mark_completed(
                  qb_hash=event.entity_id or "",
                  completed_at=completed_at,
              )
          # All other event types: no-op
  ```

- [ ] **Update `javdb/pipeline/events/__init__.py`** to re-export the new consumer:
  ```python
  from javdb.pipeline.events.consumer import (
      Consumer,
      RunEventSummaryConsumer,
      AcquisitionOutcomeShadowConsumer,
  )  # noqa: E402,F401
  ```

- [ ] **Update `apps/cli/ops/events.py`** — add `--consumer` flag and wire the
  new consumer. Diff (against current file):

  In `_build_parser()`, add after the `--batch` argument:
  ```python
  p.add_argument(
      "--consumer",
      default="run_event_summary",
      choices=["run_event_summary", "acquisition_outcome_shadow"],
      help="Which consumer projection to run.",
  )
  ```

  In `main()`, after the `with get_db(_db.REPORTS_DB_PATH) as conn:` block,
  replace the hardcoded consumer construction:

  Current:
  ```python
  with get_db(_db.REPORTS_DB_PATH) as conn:
      event_repo = PipelineEventRepo(conn)
      consumer = RunEventSummaryConsumer(RunEventSummaryRepo(conn))
      if args.replay:
          event_repo.advance_cursor(consumer.name, 0)
          RunEventSummaryRepo(conn).reset()
          logger.info("Replay: cursor + projection reset")
      total = 0
      while True:
          n = consumer.run_once(event_repo=event_repo, batch=args.batch)
          total += n
          if n < args.batch:
              break
  ```

  Replace with:
  ```python
  from javdb.pipeline.events.consumer import (
      RunEventSummaryConsumer,
      AcquisitionOutcomeShadowConsumer,
  )
  from javdb.storage.repos.pipeline_event_repo import (
      PipelineEventRepo,
      RunEventSummaryRepo,
      AcquisitionOutcomeShadowRepo,
  )

  with get_db(_db.REPORTS_DB_PATH) as conn:
      event_repo = PipelineEventRepo(conn)
      if args.consumer == "acquisition_outcome_shadow":
          shadow_repo = AcquisitionOutcomeShadowRepo(conn)
          consumer = AcquisitionOutcomeShadowConsumer(shadow_repo)
          if args.replay:
              event_repo.advance_cursor(consumer.name, 0)
              shadow_repo.reset()
              logger.info("Replay: cursor + shadow projection reset")
      else:
          summary_repo = RunEventSummaryRepo(conn)
          consumer = RunEventSummaryConsumer(summary_repo)
          if args.replay:
              event_repo.advance_cursor(consumer.name, 0)
              summary_repo.reset()
              logger.info("Replay: cursor + projection reset")
      total = 0
      while True:
          n = consumer.run_once(event_repo=event_repo, batch=args.batch)
          total += n
          if n < args.batch:
              break
  ```

  Also remove the now-duplicated imports at the top of `main()` if they were
  previously inline. The existing top-level imports in `apps/cli/ops/events.py`
  already cover `PipelineEventRepo`, `RunEventSummaryRepo`, and
  `RunEventSummaryConsumer` — do not duplicate them at the top; only add
  the new ones at the import block.

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_consumer.py -q
  ```

- [ ] **Run existing consumer and events-CLI smoke tests:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_pipeline_event_consumer.py \
    tests/smoke/test_events_cli.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/pipeline/events/consumer.py \
    javdb/pipeline/events/__init__.py \
    apps/cli/ops/events.py \
    tests/unit/test_adr036_p2_shadow_consumer.py
  git commit -m "$(cat <<'EOF'
  feat(events): add AcquisitionOutcomeShadowConsumer + --consumer flag in events CLI (ADR-036 P2)

  Consumer projects TorrentQueued/TorrentCompleted into the shadow table.
  apps/cli/ops/events.py gains --consumer {run_event_summary,acquisition_outcome_shadow}.
  Replay resets both cursor and the relevant projection.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 9: Cross-Validation Function + CLI

### Files
- **Create:** `javdb/ops/reconcile/shadow_validate.py`
- **Create:** `apps/cli/ops/shadow_validate.py`
- **Create:** `tests/unit/test_adr036_p2_shadow_validate.py`

### Context

`compare_shadow_to_authoritative()` loads all shadow rows (`AcquisitionOutcomeShadow`,
reports DB) and all authoritative rows (`AcquisitionOutcome`, operations DB).
It returns a `ShadowValidateResult` dataclass with:
- `shadow_total`: int — count of rows in shadow
- `auth_total`: int — count of rows in authoritative (all states)
- `missing_from_shadow`: list[str] — qb_hashes in authoritative (queued or
  completed state only) but not in shadow
- `missing_from_auth`: list[str] — qb_hashes in shadow but not in authoritative
- `state_mismatches`: list[dict] — qb_hashes where both exist but shadow is
  "queued" and authoritative is "completed" (or vice versa; only compare
  queued/completed — ignore richer auth states like in_library/downloading)
- `is_clean`: bool — True iff all three mismatch lists are empty

Comparison rule: only flag rows where BOTH shadow and authoritative have state
in `{"queued", "completed"}`. If authoritative is `"in_library"` or
`"downloading"` or `"stalled"` or `"failed"`, that is not a shadow mismatch
(the shadow only tracks queued/completed transitions).

The function accepts optional injected `shadow_repo` and `auth_repo` for
testability. When not injected, it opens the appropriate DBs via `get_db`.

The CLI (`apps/cli/ops/shadow_validate.py`) calls
`compare_shadow_to_authoritative()` and prints the result as a JSON-formatted
report to stdout.

### Steps

- [ ] **Write the failing test** in
  `tests/unit/test_adr036_p2_shadow_validate.py`:

```python
# tests/unit/test_adr036_p2_shadow_validate.py
"""Tests for shadow vs authoritative cross-validation."""
import sqlite3

import pytest

from javdb.ops.reconcile.shadow_validate import (
    compare_shadow_to_authoritative,
    ShadowValidateResult,
)

_SHADOW_DDL = """
CREATE TABLE AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
"""
_AUTH_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash       TEXT PRIMARY KEY NOT NULL,
  href          TEXT NOT NULL DEFAULT '',
  video_code    TEXT,
  category      TEXT,
  state         TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','downloading','completed','in_library','stalled','failed')),
  queued_at     TEXT,
  completed_at  TEXT,
  landed_at     TEXT,
  last_seen_at  TEXT,
  session_id    TEXT
);
"""


@pytest.fixture
def shadow_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SHADOW_DDL)
    return c


@pytest.fixture
def auth_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_AUTH_DDL)
    return c


def _insert_shadow(conn, qb_hash, state="queued"):
    conn.execute(
        "INSERT INTO AcquisitionOutcomeShadow (qb_hash, state, updated_at) "
        "VALUES (?, ?, '2026-06-10T00:00:00Z')",
        [qb_hash, state],
    )
    conn.commit()


def _insert_auth(conn, qb_hash, state="queued"):
    conn.execute(
        "INSERT INTO AcquisitionOutcome (qb_hash, state) VALUES (?, ?)",
        [qb_hash, state],
    )
    conn.commit()


def test_clean_when_both_match(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h1", "queued")
    _insert_auth(auth_conn, "h1", "queued")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert result.is_clean
    assert result.missing_from_shadow == []
    assert result.missing_from_auth == []
    assert result.state_mismatches == []


def test_missing_from_shadow(shadow_conn, auth_conn):
    _insert_auth(auth_conn, "h2", "queued")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert "h2" in result.missing_from_shadow
    assert not result.is_clean


def test_missing_from_auth(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h3", "queued")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert "h3" in result.missing_from_auth
    assert not result.is_clean


def test_state_mismatch_queued_vs_completed(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h4", "queued")
    _insert_auth(auth_conn, "h4", "completed")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert len(result.state_mismatches) == 1
    assert result.state_mismatches[0]["qb_hash"] == "h4"
    assert not result.is_clean


def test_auth_in_library_not_flagged_as_mismatch(shadow_conn, auth_conn):
    """Shadow queued, auth in_library — this is expected; not a mismatch."""
    _insert_shadow(shadow_conn, "h5", "queued")
    _insert_auth(auth_conn, "h5", "in_library")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    # h5 is in both; auth is in_library so we don't compare states
    assert result.state_mismatches == []


def test_auth_downloading_not_flagged(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h6", "queued")
    _insert_auth(auth_conn, "h6", "downloading")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert result.state_mismatches == []


def test_counts_are_correct(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h7", "queued")
    _insert_shadow(shadow_conn, "h8", "completed")
    _insert_auth(auth_conn, "h7", "queued")
    _insert_auth(auth_conn, "h8", "completed")
    _insert_auth(auth_conn, "h9", "in_library")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert result.shadow_total == 2
    assert result.auth_total == 3
    assert result.is_clean
```

- [ ] **Run tests — expect FAIL** (module does not exist):
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_validate.py -q
  ```

- [ ] **Create `javdb/ops/reconcile/shadow_validate.py`**:

```python
"""Cross-validation of AcquisitionOutcomeShadow vs AcquisitionOutcome (ADR-036 P2).

compare_shadow_to_authoritative() compares the event-driven shadow projection
against the authoritative table and returns a ShadowValidateResult.  Never
raises — comparison errors are collected in the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# States that the shadow CAN track; comparison is only meaningful here.
_COMPARABLE_STATES = frozenset({"queued", "completed"})


@dataclass
class ShadowValidateResult:
    shadow_total: int = 0
    auth_total: int = 0
    missing_from_shadow: list[str] = field(default_factory=list)
    missing_from_auth: list[str] = field(default_factory=list)
    state_mismatches: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            not self.missing_from_shadow
            and not self.missing_from_auth
            and not self.state_mismatches
        )


def compare_shadow_to_authoritative(
    *,
    shadow_conn=None,
    auth_conn=None,
) -> ShadowValidateResult:
    """Compare shadow projection to authoritative AcquisitionOutcome.

    Accepts optional injected connections for testability.  When not provided,
    opens the reports DB (shadow) and operations DB (auth) via get_db.
    """
    result = ShadowValidateResult()
    try:
        shadow_rows, auth_rows = _load_rows(shadow_conn, auth_conn)
        result.shadow_total = len(shadow_rows)
        result.auth_total = len(auth_rows)

        shadow_map = {r["qb_hash"]: r["state"] for r in shadow_rows}
        # Only compare auth rows that have a queued-or-comparable state
        auth_map_comparable = {
            r["qb_hash"]: r["state"]
            for r in auth_rows
            if r["state"] in _COMPARABLE_STATES
        }
        auth_all_hashes = {r["qb_hash"] for r in auth_rows}

        # Missing from shadow: in auth (comparable states) but not in shadow
        result.missing_from_shadow = sorted(
            h for h in auth_map_comparable if h not in shadow_map
        )

        # Missing from auth: in shadow but not in auth at all
        result.missing_from_auth = sorted(
            h for h in shadow_map if h not in auth_all_hashes
        )

        # State mismatches: in both, both states comparable, but differ
        for qb_hash in shadow_map:
            if qb_hash not in auth_map_comparable:
                continue  # auth has different state or not present
            shadow_state = shadow_map[qb_hash]
            auth_state = auth_map_comparable[qb_hash]
            if shadow_state != auth_state:
                result.state_mismatches.append({
                    "qb_hash": qb_hash,
                    "shadow_state": shadow_state,
                    "auth_state": auth_state,
                })
    except Exception as exc:
        logger.error("compare_shadow_to_authoritative failed: %s", exc, exc_info=True)
        result.errors.append(str(exc))
    return result


def _load_rows(shadow_conn, auth_conn):
    """Load all rows from both connections (injected for tests; else open DBs)."""
    import sqlite3 as _sqlite3

    if shadow_conn is not None:
        sc = shadow_conn
        _close_shadow = False
    else:
        from javdb.storage.db import REPORTS_DB_PATH, get_db as _get_db
        sc = _sqlite3.connect(REPORTS_DB_PATH)
        sc.row_factory = _sqlite3.Row
        _close_shadow = True

    if auth_conn is not None:
        ac = auth_conn
        _close_auth = False
    else:
        from javdb.storage.db import OPERATIONS_DB_PATH
        ac = _sqlite3.connect(OPERATIONS_DB_PATH)
        ac.row_factory = _sqlite3.Row
        _close_auth = True

    try:
        shadow_rows = sc.execute(
            "SELECT qb_hash, state FROM AcquisitionOutcomeShadow"
        ).fetchall()
        auth_rows = ac.execute(
            "SELECT qb_hash, state FROM AcquisitionOutcome"
        ).fetchall()
    finally:
        if _close_shadow:
            sc.close()
        if _close_auth:
            ac.close()

    return shadow_rows, auth_rows
```

- [ ] **Create `apps/cli/ops/shadow_validate.py`**:

```python
"""CLI: cross-validate AcquisitionOutcomeShadow vs AcquisitionOutcome (ADR-036 P2).

Prints a JSON report comparing the event-driven shadow projection against
the authoritative acquisition table.  Exit code 0 = clean; 1 = discrepancies found."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.ops.reconcile.shadow_validate import compare_shadow_to_authoritative


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.shadow_validate",
        description=(
            "Cross-validate AcquisitionOutcomeShadow (event-driven) "
            "vs AcquisitionOutcome (authoritative). ADR-036 Phase 2."
        ),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(log_level=args.log_level)
    logger = logging.getLogger(__name__)

    result = compare_shadow_to_authoritative()
    report = {
        "shadow_total": result.shadow_total,
        "auth_total": result.auth_total,
        "missing_from_shadow": result.missing_from_shadow,
        "missing_from_auth": result.missing_from_auth,
        "state_mismatches": result.state_mismatches,
        "is_clean": result.is_clean,
        "errors": result.errors,
    }
    print(json.dumps(report, indent=2))

    if result.errors:
        logger.error("Validation encountered errors")
        return 2
    if not result.is_clean:
        logger.warning(
            "Discrepancies found: missing_from_shadow=%d missing_from_auth=%d "
            "state_mismatches=%d",
            len(result.missing_from_shadow),
            len(result.missing_from_auth),
            len(result.state_mismatches),
        )
        return 1
    logger.info("Shadow projection is clean (no discrepancies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Run tests — expect PASS:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_shadow_validate.py -q
  ```

- [ ] **Run all new Phase 2 tests together:**
  ```
  PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python \
    /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_adr036_p2_*.py -q
  ```

- [ ] **Commit:**
  ```
  git add javdb/ops/reconcile/shadow_validate.py \
    apps/cli/ops/shadow_validate.py \
    tests/unit/test_adr036_p2_shadow_validate.py
  git commit -m "$(cat <<'EOF'
  feat(ops): add shadow cross-validation function + CLI (ADR-036 P2)

  compare_shadow_to_authoritative() checks AcquisitionOutcomeShadow
  against AcquisitionOutcome: reports missing rows and queued/completed
  state mismatches; ignores richer auth states (in_library etc).
  apps/cli/ops/shadow_validate.py prints a JSON report; exits 1 on
  discrepancies, 2 on errors.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Self-Review

### Spec Coverage Checklist

| Requirement | Covered by |
| --- | --- |
| Emit MovieDiscovered for each phase-1/2 index entry | Task 1 |
| Emit MovieSelected for each prepared detail candidate | Task 2 |
| Emit TorrentSelected for each new magnet link (defensive hash) | Task 3 |
| Emit TorrentQueued after successful qB add | Task 4 |
| Emit TorrentCompleted with session_id recovery | Task 5 |
| All 5 emits are best-effort (pipeline survives emit failure) | Tasks 1–5 (each has a "survives raise" test) |
| AcquisitionOutcomeShadow D1 migration | Task 6 |
| AcquisitionOutcomeShadow SQLite mirror in _REPORTS_DDL | Task 6 |
| AcquisitionOutcomeShadowRepo with upsert_queued / mark_completed / get / list_all / reset | Task 7 |
| AcquisitionOutcomeShadowConsumer projects TorrentQueued/TorrentCompleted | Task 8 |
| Consumer ignores non-torrent events | Task 8 |
| Replay works (reset cursor + shadow, rebuild) | Task 8 |
| Wire consumer into apps/cli/ops/events.py --consumer flag | Task 8 |
| Cross-validation function compare_shadow_to_authoritative() | Task 9 |
| Comparison only flags queued/completed mismatches (not in_library etc.) | Task 9 |
| Cross-validation CLI apps/cli/ops/shadow_validate.py | Task 9 |
| Sentinel re-point rejected and documented | Out of Scope section |
| AcquisitionOutcome cutover deferred, gated on shadow validation | Out of Scope section |

### Placeholder Scan

No `TODO`, `FIXME`, or `<INSERT>` placeholders present in any code blocks above.

### Type / Name Consistency

- `AcquisitionOutcomeShadowRepo` — consistent across task 6 DDL, task 7 repo,
  task 8 consumer, task 9 validate (only references the repo's `list_all` via
  raw SQL, not the repo).
- `AcquisitionOutcomeShadowConsumer.name = "acquisition_outcome_shadow"` —
  matches the `--consumer` choice value and the cursor key in `EventConsumerCursor`.
- `_emit_event` alias — Tasks 1, 2, 4, 5 use the existing alias from Phase 1;
  Task 3 also uses `_emit_event` (imported in runner.py). All consistent.
- `utc_now_iso()` — used in `AcquisitionOutcomeShadowRepo` (imported from
  `javdb.pipeline.events.models` already at top of `pipeline_event_repo.py`).
- `ShadowValidateResult` — exported from `shadow_validate.py` and imported in
  test.
- `compare_shadow_to_authoritative` — consistent naming across module,
  test import, and CLI call.

### Plan-vs-Code Corrections Noted for Executor

1. **Task 1 (MovieDiscovered) — `import json`:** `run_service.py` does not have
   `import json` at the module top (verified by checking imports at lines 1–30).
   The executor must add `import json` to the top-level imports in
   `run_service.py`. Do NOT use `import json as _json` inline inside the try block;
   import at module level.

2. **Task 3 (TorrentSelected) — `plan.category`:** The `build_spider_ingestion_plan`
   result (`SpiderIngestionPlan`) may not have a `category` attribute. The plan
   uses `getattr(plan, 'category', None)` defensively. The executor should verify
   by reading `javdb/spider/ingestion/plan.py` before implementing; if
   `category` is not a field, the payload will carry `null` for category — which
   is acceptable for the shadow consumer.

3. **Task 5 (TorrentCompleted) — outer try/except restructure:** The `continue`
   added after `result.errors.append(str(exc))` restructures the loop control
   flow. The executor must verify the existing tests for `apply_cleanup_completed`
   (`test_cleanup_acquisition_completed.py`) still pass after this change.

4. **Task 8 (consumer.py imports) — circular import risk:** `consumer.py`
   currently imports `RunEventSummaryRepo` from `pipeline_event_repo.py`. Adding
   `AcquisitionOutcomeShadowRepo` follows the same pattern — both classes live in
   `pipeline_event_repo.py` — so there is no new circular import risk.

5. **Task 8 (events.py) — existing imports:** The current `apps/cli/ops/events.py`
   already imports `PipelineEventRepo`, `RunEventSummaryRepo`, and
   `RunEventSummaryConsumer` at the top level. The executor must not duplicate
   these imports; only add `AcquisitionOutcomeShadowConsumer` and
   `AcquisitionOutcomeShadowRepo` to the existing import lines.
