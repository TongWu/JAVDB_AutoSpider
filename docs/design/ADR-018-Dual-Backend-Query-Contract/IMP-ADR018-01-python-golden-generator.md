# Dual-Backend Query Contract — Phase 1: Python Golden Generator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Contract Golden mechanism in the Python repo and pin the Python side of the dynamic query builders against it. After this phase, any change to a covered builder that is not accompanied by a regenerated golden fails `pytest`.

**Architecture:** A Python CLI tool (`apps/cli/ops/dump_query_contract.py`, mirroring `dump_openapi.py`) runs each covered builder over a fixed set of parameter cases, normalizes the emitted SQL, and writes a language-neutral golden JSON to `docs/api/contract/`. A unit test re-runs the builders over the cases embedded in the golden and asserts equality. The golden is consumed cross-repo by the TypeScript backend in **Phase 2 (IMP-ADR018-02)** — out of scope here.

**Tech Stack:** Python 3.11+, pytest. Single repo (`JAVDB_AutoSpider_CICD`). No TS changes, no D1, no network.

**Related:** [ADR-018](ADR-018-dual-backend-query-contract.md)

**Status:** Proposed

---

## Scope & deviation from the ADR-018 roadmap

The ADR Phase-1 row names "history/sessions/stats". On inspection, only **history** exposes clean, importable builders today:

- `javdb/storage/repos/history_repo.py` — `_build_movie_filters(...)` (L211), `_build_torrent_filters(...)` (L272), both module-level, keyword-only, returning `Tuple[str, List]` `(where_clause, params)`.
- `javdb/storage/repos/sessions_repo.py` — `SessionsRepo.list(...)` (L69) assembles SQL **inline** and executes it; it does not return SQL. Phase 1 extracts a returnable `_build_session_query(...)` (behavior-preserving) so it can be pinned.
- **stats is deferred.** Python's stats aggregations live inline in `apps/api/routers/stats.py` (router-level `GROUP BY`), not in a repo builder. Pinning them needs a router→builder extraction first → tracked as **Phase 2 / follow-up**, not this IMP.

This deviation is reflected back into the ADR roadmap (Phase 1 = history + sessions; stats → Phase 2).

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `apps/cli/ops/query_contract_cases.py` | The fixed parameter cases + `normalize_sql()` helper (single source for generator + test) |
| Create | `apps/cli/ops/dump_query_contract.py` | CLI generator: run builders over cases, write golden JSON |
| Create | `docs/api/contract/query-builders.golden.json` | The committed golden artifact (generated output) |
| Modify | `javdb/storage/repos/sessions_repo.py` | Extract module-level `_build_session_query(...) -> Tuple[str, List]`; `SessionsRepo.list` delegates to it |
| Create | `tests/unit/test_query_contract_golden.py` | Re-run builders over golden cases, assert normalized SQL + bindings match |

---

## Task 1: Cases + normalization helper (single source of truth)

Both the generator and the test must use the **identical** case set and normalizer, or the guard guards nothing.

- [ ] Create `apps/cli/ops/query_contract_cases.py`:

```python
"""Shared fixtures for the dual-backend query Contract Golden (ADR-018)."""
from __future__ import annotations
import re

# The golden's `version` is a content hash computed by the generator (ADR-018 D6),
# not a hand-bumped constant — avoids the forgotten-bump footgun.

def normalize_sql(sql: str) -> str:
    """Canonical form so formatting differences don't cause false drift.

    Collapse all runs of whitespace to a single space and strip ends.
    Keep this trivial and identical to the TS-side normalizer (Phase 2).
    """
    return re.sub(r"\s+", " ", sql).strip()

# Each case: (builder_id, case_name, kwargs)
MOVIE_FILTER_CASES = [
    ("movie_filters", "empty", {}),
    ("movie_filters", "q_only", {"q": "ABC-123"}),
    ("movie_filters", "q_and_perfect_match", {"q": "ABC", "perfect_match": True}),
    ("movie_filters", "actor_hires_session", {"actor": "Jane", "hi_res": True, "session_id": "S1"}),
    ("movie_filters", "date_range_cursor", {"date_from": "2026-01-01", "date_to": "2026-02-01", "cursor_id": 42}),
]
TORRENT_FILTER_CASES = [
    ("torrent_filters", "empty", {}),
    ("torrent_filters", "q_only", {"q": "ABC-123"}),
    ("torrent_filters", "resolution_subtitle_uncensored", {"resolution_type": 1, "has_subtitle": True, "uncensored": False}),
    # extend to cover every filter branch in _build_torrent_filters
]
SESSION_QUERY_CASES = [
    ("session_query", "default", {"state": None, "cursor": None, "limit": 50}),
    ("session_query", "state_only", {"state": "committed", "cursor": None, "limit": 50}),
    ("session_query", "state_and_cursor", {"state": "failed", "cursor": "<ENCODED>", "limit": 20}),
]
```

- [ ] Cover **every** filter branch (one case per `if` in each builder) so a removed/changed branch is caught. Cross-check against `_build_movie_filters` (L211-269) and `_build_torrent_filters` (L272+).
- [ ] For `state_and_cursor`, generate the `<ENCODED>` value at runtime via `sessions_repo._encode_cursor(...)` rather than hardcoding — keeps the cursor scheme itself in the loop.

**Verify:** `python -c "from apps.cli.ops.query_contract_cases import normalize_sql; print(normalize_sql('  a   b\n c '))"` → `a b c`.

---

## Task 2: Extract a returnable session-query builder

`SessionsRepo.list` (sessions_repo.py:69) builds SQL inline. Extract the assembly so it is pin-able, preserving behavior.

- [ ] Add a module-level function:

```python
def _build_session_query(*, state: str | None, cursor: str | None, limit: int) -> Tuple[str, list]:
    """Assemble the ReportSessions list SQL + params. Pure; no DB access."""
    sql = (
        "SELECT Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
        "ReportType, ReportDate, FailureReason FROM ReportSessions"
    )
    params: list = []
    clauses: list[str] = []
    if state:
        clauses.append("Status = ?"); params.append(state)
    if cursor:
        clauses.append("Id < ?"); params.append(_decode_cursor(cursor))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY Id DESC LIMIT ?"
    params.append(limit + 1)  # over-fetch one row: SessionsRepo.list trims to `limit` and uses len(rows) > limit to set next_cursor — binding plain `limit` would drop pagination
    return sql, params
```

- [ ] Refactor `SessionsRepo.list` to call `_build_session_query(...)` then execute — **no behavior change** (same SQL, same params, same ordering/limit as today). Match the exact current SQL skeleton (including the existing `ORDER BY Id DESC LIMIT ?`).
- [ ] **Verify (regression):** `pytest tests/unit -k "session" -q` — existing sessions tests pass unchanged.

---

## Task 3: The golden generator CLI

- [ ] Create `apps/cli/ops/dump_query_contract.py`, mirroring `dump_openapi.py` (REPO_ROOT bootstrap, `main() -> int`, `__main__`):

```python
"""Dump the dual-backend query Contract Golden to docs/api/contract/ (ADR-018)."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.cli.ops.query_contract_cases import (  # noqa: E402
    normalize_sql,
    MOVIE_FILTER_CASES, TORRENT_FILTER_CASES, SESSION_QUERY_CASES,
)
from javdb.storage.repos.history_repo import _build_movie_filters, _build_torrent_filters  # noqa: E402
from javdb.storage.repos.sessions_repo import _build_session_query, _encode_cursor  # noqa: E402

OUT = REPO_ROOT / "docs" / "api" / "contract" / "query-builders.golden.json"

_BUILDERS = {
    "movie_filters": _build_movie_filters,
    "torrent_filters": _build_torrent_filters,
    "session_query": _build_session_query,
}

def _run_case(builder_id: str, kwargs: dict):
    kw = dict(kwargs)
    if kw.get("cursor") == "<ENCODED>":
        kw["cursor"] = _encode_cursor("99999")
    sql, bindings = _BUILDERS[builder_id](**kw)
    return normalize_sql(sql), list(bindings), kw

def main() -> int:
    cases = []
    for builder_id, name, kwargs in (*MOVIE_FILTER_CASES, *TORRENT_FILTER_CASES, *SESSION_QUERY_CASES):
        sql, bindings, resolved = _run_case(builder_id, kwargs)
        cases.append({"builder": builder_id, "name": name,
                      "params": resolved, "sql": sql, "bindings": bindings})
    version = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:16]
    doc = {"version": version,  # content hash (D6); rides the Phase-2 repository_dispatch payload
           "normalization": "collapse-whitespace-runs-to-single-space-and-trim",
           "cases": cases}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(cases)} cases)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Generate:** `python -m apps.cli.ops.dump_query_contract` → writes `docs/api/contract/query-builders.golden.json`.
- [ ] Eyeball the golden: confirm the movie `q_and_perfect_match` case shows the headline duplicated clause `(m.VideoCode LIKE ? OR m.ActorName LIKE ? OR m.SupportingActors LIKE ?) AND m.PerfectMatchIndicator = ?` with bindings `["%ABC%","%ABC%","%ABC%",1]`.
- [ ] Commit the golden (it is a reviewed artifact, like `openapi.json`).

---

## Task 4: pytest pins the Python side to the golden

- [ ] Create `tests/unit/test_query_contract_golden.py`:

```python
"""ADR-018 Phase 1: pin Python query builders to the committed Contract Golden."""
import json
from pathlib import Path
import pytest

from apps.cli.ops.query_contract_cases import normalize_sql
from apps.cli.ops.dump_query_contract import _BUILDERS, _run_case, OUT as GOLDEN_PATH

GOLDEN = json.loads(Path(GOLDEN_PATH).read_text(encoding="utf-8"))

@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: f'{c["builder"]}:{c["name"]}')
def test_builder_matches_golden(case):
    sql, bindings, _ = _run_case(case["builder"], case["params"])
    assert sql == case["sql"], f'SQL drift in {case["builder"]}:{case["name"]}'
    assert bindings == case["bindings"], f'bindings drift in {case["builder"]}:{case["name"]}'

def test_golden_covers_all_builders():
    seen = {c["builder"] for c in GOLDEN["cases"]}
    assert seen == set(_BUILDERS), f"golden missing builders: {set(_BUILDERS) - seen}"
```

> Note: `case["params"]` is already the resolved kwargs (cursor pre-encoded) written by the generator, so the test re-runs the live builder against the same inputs the golden was built from. A builder change without regen → `sql`/`bindings` mismatch → red.

- [ ] **Verify:** `pytest tests/unit/test_query_contract_golden.py -v` — all cases green.

---

## Task 5: Verification gates

- [ ] `python -m apps.cli.ops.dump_query_contract` runs clean and is idempotent (re-running produces no git diff).
- [ ] `pytest tests/unit/test_query_contract_golden.py -v` — green.
- [ ] `pytest tests/unit -k "session or history" -q` — existing builder/repo tests unaffected by the Task-2 extraction.
- [ ] **Negative check (manual, revert after):** temporarily change a clause in `_build_movie_filters` (e.g. `m.ActorName` → `m.Actor`), run the test **without** regenerating → confirm it fails with a clear `SQL drift` message. Revert.
- [ ] Update this IMP's `Status` to `Completed` and check off `IMP-ADR018-01` in the ADR roadmap.

---

## Out of scope (this phase)

- TS-side consumption + cross-repo fetch/versioning → **IMP-ADR018-02** (Phase 2).
- stats aggregation builders (need router→builder extraction first) → Phase 2 / follow-up.
- Shared filter-spec codegen ("eliminate") → IMP-ADR018-03 (deferred, D7).
