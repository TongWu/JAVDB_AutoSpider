# IMP-ADR037-03: Golden-Run Record/Replay Diff (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-037](ADR-037-deterministic-pipeline-test-harness.md) (umbrella) — this is **Phase 3** (optional) of three. Builds on [IMP-ADR037-01](IMP-ADR037-01-harness-core.md) and [IMP-ADR037-02](IMP-ADR037-02-scenario-library-record-seams.md).

**Goal:** Layer a **golden-run record/replay diff** on top of the composable harness — capture a clean daily run's *canonical, normalized outputs* into a committed `snapshot.json`, then in CI replay the same scenario and **diff** the fresh outputs against it, failing on any drift. A regression net that catches pipeline behaviour changes the per-scenario assertions don't pin (ADR-037 D7 Phase 3).

**Architecture:** A standard golden-file ("bless") loop. `capture_snapshot(harness, result)` projects a run's authoritative state — history (video_code/href), torrents (magnet), qB queued hashes, acquisition outcomes (hash/state), and the event-type sequence — into a deterministic dict with **all nondeterministic fields excluded** (no session_id, timestamps, run ids, autoincrement ids). `golden_run.py` persists/loads that dict as `tests/harness/scenarios/golden_runs/<name>/snapshot.json`. One test both blesses (regenerates the committed snapshot when `JAVDB_HARNESS_BLESS=1`) and, normally, diffs the live capture against the committed snapshot. The run's *inputs* are the existing `golden_daily()` cassette (already committed Python); a live-recorded cassette from IMP-02 record mode can be substituted for higher fidelity.

**Tech Stack:** Python 3, `pytest`, `sqlite3`, the shipped `tests/harness/` package (`pipeline_harness`, `golden_daily`, `HarnessResult`, `acquisition_outcomes()`, `events()`).

---

## Confirmed facts (verified against the code at plan time)

- **Stable identity columns** (everything else is timestamps/ids → excluded): `MovieHistory(VideoCode, Href, …, SessionId)` and `TorrentHistory(MagnetUri, …, SessionId)` (`javdb/storage/db/_db_migrations.py`). Both live in the history DB; `get_db()` with no arg defaults to `HISTORY_DB_PATH`, which `_isolate_sqlite` collapses onto the one temp DB.
- **Harness surface to reuse** (shipped + IMP-02): `pipeline_harness.run_daily(golden_daily()) -> HarnessResult`; `result.qb.all_hashes() -> set`; `pipeline_harness.acquisition_outcomes() -> [{"qb_hash","state"}]`; `pipeline_harness.events() -> [event_type]`. A clean golden run yields (probe-verified): 4 `TorrentHistory` rows of which **2 carry a non-null `MagnetUri`** (the other 2 are NULL-magnet rows the snapshot filters out → `len(torrents) == 2`), 2 `MovieHistory` rows, qB hashes `{"a"*40, "b"*40}`, **2 `queued` acquisition outcomes**, and `events() == ["RunStarted"]` (per IMP-02's confirmed event boundary — the API commit does not emit `SessionCommitted`).
- **Prerequisite from IMP-02:** the 2 `queued` acquisition outcomes are only harness-visible because IMP-02's `_install` repoints the ops-persistence DB paths (the stale-import fix). IMP-03 depends on IMP-02 being complete; without that repoint `acquisition_outcomes()` is `[]` and the snapshot's `acquisition` key would be empty.
- **Determinism:** the golden fixtures (`tests/harness/scenarios/golden_daily.py`) hard-code video codes `ABC-001`/`ABC-002`, hrefs `/v/AAA111`/`/v/BBB222`, and magnets `btih:aaaa…`/`btih:bbbb…`, so the normalized projection is byte-stable across runs. The autouse `_isolate_sqlite` gives each run a fresh DB, so there is no cross-run contamination.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `tests/harness/snapshot.py` | Create | `capture_snapshot(harness, result)` (normalized projection) + `diff_snapshots(expected, actual)` |
| `tests/harness/golden_run.py` | Create | `golden_path(name)`, `save_golden(name, snapshot)`, `load_golden(name)` |
| `tests/harness/scenarios/golden_runs/daily/snapshot.json` | Create (blessed) | The committed canonical snapshot for the clean daily run |
| `tests/harness/test_snapshot.py` | Create | `capture_snapshot`/`diff_snapshots` unit + normalization tests |
| `tests/harness/test_golden_run.py` | Create | Round-trip + the bless/diff regression test |
| `tests/harness/__init__.py` | Modify | Re-export the new public surface |
| `docs/handbook/en/developer/pipeline-test-harness.md` | Modify | Document the golden-run diff + bless workflow |
| `docs/handbook/zh/developer/pipeline-test-harness.md` | Modify | Paired zh translation |

**Naming contract (verbatim across tasks):**
`capture_snapshot(pipeline_harness, result) -> dict` returning keys `{"movies","torrents","qb_hashes","acquisition","events"}`; `diff_snapshots(expected: dict, actual: dict) -> list[str]`; `golden_path(name: str) -> str`; `save_golden(name: str, snapshot: dict) -> None`; `load_golden(name: str) -> dict`. Bless env var: `JAVDB_HARNESS_BLESS`.

---

## Task 1: `capture_snapshot` + `diff_snapshots`

**Files:**
- Create: `tests/harness/snapshot.py`
- Test: `tests/harness/test_snapshot.py`

`capture_snapshot` projects a run into a normalized dict. It reads `MovieHistory`/`TorrentHistory` directly and reuses the harness's `acquisition_outcomes()`/`events()` helpers. Every field that varies run to run (session id, all timestamps, autoincrement ids, seq) is excluded by construction.

- [ ] **Step 1: Write the failing test**

```python
# tests/harness/test_snapshot.py
import json

from tests.harness.scenarios.golden_daily import golden_daily
from tests.harness.snapshot import capture_snapshot, diff_snapshots


def test_capture_snapshot_is_normalized(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())
    snap = capture_snapshot(pipeline_harness, result)

    # Stable, known canonical outputs of the clean daily run.
    assert snap["qb_hashes"] == ["a" * 40, "b" * 40]
    assert snap["events"] == ["RunStarted"]
    assert len(snap["movies"]) == 2
    assert len(snap["torrents"]) == 2
    assert sorted(o["state"] for o in snap["acquisition"]) == ["queued", "queued"]

    # No nondeterministic fields leaked into the structured parts.
    for movie in snap["movies"]:
        assert set(movie) == {"video_code", "href"}
    for outcome in snap["acquisition"]:
        assert set(outcome) == {"qb_hash", "state"}
    blob = json.dumps(snap)
    assert "SessionId" not in blob and "DateTime" not in blob


def test_diff_snapshots_reports_only_differences():
    a = {"qb_hashes": ["x"], "events": ["RunStarted"]}
    assert diff_snapshots(a, dict(a)) == []
    diffs = diff_snapshots(a, {"qb_hashes": ["y"], "events": ["RunStarted"]})
    assert len(diffs) == 1
    assert "qb_hashes" in diffs[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/harness/test_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: tests.harness.snapshot`

- [ ] **Step 3: Write `snapshot.py`**

```python
# tests/harness/snapshot.py
"""Normalized golden-run snapshot capture + diff (ADR-037 D7, Phase 3).

``capture_snapshot`` projects a harness run into a deterministic dict of the
pipeline's authoritative outputs, EXCLUDING every nondeterministic field
(session id, timestamps, autoincrement ids, event seq). ``diff_snapshots``
returns a human-readable list of the keys that differ — empty means identical.
"""

from __future__ import annotations

from typing import Any


def capture_snapshot(pipeline_harness, result) -> dict:
    """Project a run into a normalized, diff-stable snapshot."""
    from javdb.storage.db import get_db

    # MovieHistory / TorrentHistory live in the history DB; get_db() defaults to
    # HISTORY_DB_PATH, which _isolate_sqlite collapses onto the one test DB.
    with get_db() as conn:
        movies = [
            {"video_code": row[0], "href": row[1]}
            for row in conn.execute(
                "SELECT VideoCode, Href FROM MovieHistory ORDER BY Href"
            ).fetchall()
        ]
        torrents = sorted(
            row[0]
            for row in conn.execute(
                "SELECT MagnetUri FROM TorrentHistory"
            ).fetchall()
            if row[0]
        )

    acquisition = sorted(
        ({"qb_hash": o["qb_hash"], "state": o["state"]}
         for o in pipeline_harness.acquisition_outcomes()),
        key=lambda d: d["qb_hash"],
    )

    return {
        "movies": movies,
        "torrents": torrents,
        "qb_hashes": sorted(result.qb.all_hashes()),
        "acquisition": acquisition,
        "events": list(pipeline_harness.events()),  # seq order; semantically a sequence
    }


def diff_snapshots(expected: dict, actual: dict) -> list[str]:
    """Return one readable line per differing top-level key (empty == equal)."""
    diffs: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        exp: Any = expected.get(key)
        act: Any = actual.get(key)
        if exp != act:
            diffs.append(f"{key}: expected {exp!r}, got {act!r}")
    return diffs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/harness/test_snapshot.py -v`
Expected: PASS (2 passed). If `snap["movies"]` is empty, the golden fixtures did not parse — run `pytest tests/harness/test_golden_scenario.py -v` first (it is the upstream guarantee).

- [ ] **Step 5: Commit**

```bash
git add tests/harness/snapshot.py tests/harness/test_snapshot.py
git commit -m "test(harness): add normalized golden-run snapshot capture + diff (ADR-037 Phase 3)"
```

---

## Task 2: `golden_run.py` — persist / load the committed snapshot

**Files:**
- Create: `tests/harness/golden_run.py`
- Test: `tests/harness/test_golden_run.py`

Stores each golden snapshot as `tests/harness/scenarios/golden_runs/<name>/snapshot.json` (sorted-keys JSON for diff-friendly commits).

- [ ] **Step 1: Write the failing round-trip test**

```python
# tests/harness/test_golden_run.py
from tests.harness.golden_run import golden_path, load_golden, save_golden


def test_golden_round_trip(tmp_path, monkeypatch):
    import tests.harness.golden_run as gr
    monkeypatch.setattr(gr, "_GOLDEN_DIR", str(tmp_path / "golden_runs"))
    snap = {"qb_hashes": ["a" * 40], "events": ["RunStarted"]}
    save_golden("unit", snap)
    assert load_golden("unit") == snap
    assert golden_path("unit").endswith("unit/snapshot.json")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/harness/test_golden_run.py -v`
Expected: FAIL — `ModuleNotFoundError: tests.harness.golden_run`

- [ ] **Step 3: Write `golden_run.py`**

```python
# tests/harness/golden_run.py
"""Persist / load committed golden-run snapshots (ADR-037 D7, Phase 3).

A golden run lives at scenarios/golden_runs/<name>/snapshot.json. Snapshots are
written sorted-keys + indented so a behaviour change shows up as a small, legible
diff in review."""

from __future__ import annotations

import json
import os

_GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "scenarios", "golden_runs")


def golden_path(name: str) -> str:
    return os.path.join(_GOLDEN_DIR, name, "snapshot.json")


def save_golden(name: str, snapshot: dict) -> None:
    path = golden_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def load_golden(name: str) -> dict:
    with open(golden_path(name), "r", encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/harness/test_golden_run.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/harness/golden_run.py tests/harness/test_golden_run.py
git commit -m "test(harness): add golden-run snapshot persistence (ADR-037 Phase 3)"
```

---

## Task 3: The bless/diff regression test + commit the blessed snapshot

**Files:**
- Modify: `tests/harness/test_golden_run.py` (append the regression test)
- Create (blessed): `tests/harness/scenarios/golden_runs/daily/snapshot.json`

One test does both jobs: with `JAVDB_HARNESS_BLESS=1` it regenerates the committed snapshot; normally it diffs the live capture against the committed snapshot and fails on drift.

- [ ] **Step 1: Append the bless/diff test**

```python
# tests/harness/test_golden_run.py  (append)
import os

import pytest

from tests.harness.golden_run import load_golden, save_golden
from tests.harness.scenarios.golden_daily import golden_daily
from tests.harness.snapshot import capture_snapshot, diff_snapshots

_GOLDEN_NAME = "daily"


def test_golden_daily_run_matches_snapshot(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())
    actual = capture_snapshot(pipeline_harness, result)

    if os.environ.get("JAVDB_HARNESS_BLESS"):
        save_golden(_GOLDEN_NAME, actual)
        pytest.skip(f"blessed golden snapshot '{_GOLDEN_NAME}'")

    expected = load_golden(_GOLDEN_NAME)
    diffs = diff_snapshots(expected, actual)
    assert diffs == [], "golden-run drift detected:\n" + "\n".join(diffs)
```

- [ ] **Step 2: Bless the initial committed snapshot**

The committed `snapshot.json` does not exist yet, so the diff branch would `FileNotFoundError`. Generate it once:

Run: `JAVDB_HARNESS_BLESS=1 pytest tests/harness/test_golden_run.py::test_golden_daily_run_matches_snapshot -v`
Expected: `SKIPPED (blessed golden snapshot 'daily')`, and `tests/harness/scenarios/golden_runs/daily/snapshot.json` now exists.

- [ ] **Step 3: Inspect the blessed snapshot, then verify the diff path is green**

Read `tests/harness/scenarios/golden_runs/daily/snapshot.json` and sanity-check it: `qb_hashes` = the two 40-hex magnets, `events` = `["RunStarted"]`, 2 `movies` with codes `ABC-001`/`ABC-002`, 2 `torrents`, 2 `queued` acquisition outcomes. Then run normally (no bless):

Run: `pytest tests/harness/test_golden_run.py -v`
Expected: PASS — the live capture matches the committed snapshot.

- [ ] **Step 4: Commit (test + blessed artifact together)**

```bash
git add tests/harness/test_golden_run.py tests/harness/scenarios/golden_runs/daily/snapshot.json
git commit -m "test(harness): golden daily-run record/replay diff + blessed snapshot (ADR-037 Phase 3)"
```

---

## Task 4: Re-exports, docs, full gate

**Files:**
- Modify: `tests/harness/__init__.py`, `docs/handbook/en/developer/pipeline-test-harness.md`, `docs/handbook/zh/developer/pipeline-test-harness.md`

- [ ] **Step 1: Re-export the new public surface**

Append to `tests/harness/__init__.py`:

```python
from tests.harness.snapshot import capture_snapshot, diff_snapshots  # noqa: E402,F401
from tests.harness.golden_run import golden_path, load_golden, save_golden  # noqa: E402,F401
```

- [ ] **Step 2: Document the golden-run diff + bless workflow** in `docs/handbook/en/developer/pipeline-test-harness.md`: a "Golden-run diff" section explaining what the snapshot captures (and what it excludes), how the diff test guards against drift, and the bless command (`JAVDB_HARNESS_BLESS=1 pytest tests/harness/test_golden_run.py -k golden_daily_run_matches`) with the rule "review the snapshot diff before re-blessing — a changed snapshot is a behaviour change, not a chore". Mirror into the paired `docs/handbook/zh/developer/pipeline-test-harness.md` in the same commit (keep code/paths/env-var names verbatim; translate prose + comments).

- [ ] **Step 3: Full gate**

Run:
```bash
pytest tests/harness/ -v
```
Expected: all PASS (Phase 1 + Phase 2 + Phase 3), including `test_snapshot.py` and `test_golden_run.py`'s diff path. The bless test runs the diff branch (not bless) and matches.

- [ ] **Step 4: Commit**

```bash
git add tests/harness/__init__.py docs/handbook
git commit -m "test(harness): re-exports + docs for ADR-037 Phase 3 golden-run diff"
```

- [ ] **Step 5: Close out ADR-037 (status log + archival)**

In `ADR-037-deterministic-pipeline-test-harness.md` (and `.zh.md`, same commit): change the Phase-3 roadmap row from "IMP-ADR037-03 (stub)" to a link to this file; flip **Status** from "Proposed" to **Completed** (all three phases shipped); append a Status Log line summarising Phase 3 (golden-run snapshot capture + bless/diff regression net). Per CLAUDE.md whole-folder archival: once Status is Completed AND all three IMPs are done, move the entire `docs/design/ADR-037-Pipeline-Test-Harness/` folder into `docs/design/_archive/ADR-037-Pipeline-Test-Harness/` and fix any incoming references (`grep -rn "ADR-037-Pipeline-Test-Harness" docs/ --include=*.md | grep -v _archive` and insert `_archive/` into those paths). Internal ADR↔IMP links are filename-only and need no change.

```bash
git add docs/design
git commit -m "docs(adr-037): mark Completed, link IMP-ADR037-03, archive folder"
```

---

## Plan Self-Review

**Spec coverage (ADR-037 Phase-3 roadmap row + D7):**
- "record a real run's inputs+outputs" → `capture_snapshot` (outputs) + the `golden_daily()` cassette (inputs); a live-recorded cassette from IMP-02 can be substituted (noted). ✓
- "replay + diff in CI" → Task 3's bless/diff test diffs the live capture against the committed `snapshot.json`. ✓
- Normalized, deterministic snapshot (no session id / timestamps / ids) → Task 1, enforced by per-key structure assertions. ✓
- Docs + ADR close-out/archival → Task 4. ✓

**Why this is "on top of" the harness, not instead of it (ADR D7 / Alternatives):** the per-scenario tests (IMP-01/02) assert *specific* behaviours; this snapshot is a *broad* net that catches unintended changes to the whole canonical output set. It is deliberately Phase 3 and optional — it depends on the composable fakes underneath (the ADR rejected golden-run-only as the primary form).

**Determinism guards:** the snapshot excludes session id, all `DateTime*`/`*_at` timestamps, autoincrement ids, and event `seq`; lists are sorted (movies by href, torrents/qb_hashes lexically, acquisition by hash). Events stay in seq order (a semantic sequence — for the clean run it is the single `["RunStarted"]`). The `JAVDB_HARNESS_BLESS` escape hatch makes intentional changes a one-command, reviewed re-bless rather than a hand-edit.

**Type consistency:** `capture_snapshot(pipeline_harness, result) -> dict` (keys `movies/torrents/qb_hashes/acquisition/events`), `diff_snapshots(expected, actual) -> list[str]`, `golden_path/save_golden/load_golden(name)` — used identically across Tasks 1–4. `_GOLDEN_DIR` is monkeypatched in the round-trip test to avoid writing into the committed tree.

**No production changes:** all edits land under `tests/harness/**` (plus docs). The harness stays test-support only (ADR-037 D2).
