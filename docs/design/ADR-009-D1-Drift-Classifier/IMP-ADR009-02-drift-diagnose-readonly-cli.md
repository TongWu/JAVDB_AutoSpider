# IMP-ADR009-02: ADR-009 Phase 1 - Read-Only Drift Diagnose CLI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value |
| ----------- | ----- |
| **Status**  | Draft |
| **Date**    | 2026-05-24 |
| **Phase**   | P1 |
| **Related** | [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), [IMP-ADR009-01](IMP-ADR009-01-d1-transient-classifier-fix.md) |

**Goal:** Ship the read-only `drift_diagnose` CLI that discovers suspect sessions and classifies each as `CLEAN`, `SAFE_TO_APPLY`, `ESCALATE_LIVE_DIVERGENCE`, or `UNEXPECTED_PATTERN`.

**Architecture:** Put diagnosis behavior in `javdb.storage.drift_diagnose`; keep `apps.cli.db.drift_diagnose` as a thin canonical entrypoint. Diagnose mode may read D1, SQLite, and `d1_drift.jsonl`, but must not mutate any database or audit file.

**Tech Stack:** Python 3.11+, argparse, sqlite3-shaped D1 facade, JSON/JSONL, pytest.

**Source spec:** [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md), D2-D4.

---

## Files

| Path | Responsibility |
| --- | --- |
| `javdb/storage/drift_diagnose.py` | Read-only discovery, merge, verdict classification, live-table comparison, exit-code calculation. |
| `apps/cli/db/drift_diagnose.py` | Thin CLI wrapper around the storage service. |
| `tests/unit/test_drift_diagnose.py` | Unit coverage for JSONL discovery, D1 sweep, verdicts, JSON/text output, and CLI smoke behavior. |
| `apps/cli/db/README.md` | Lists the new canonical db CLI and read-only exit codes. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md` | Records P1 status after implementation. |
| `docs/design/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.zh.md` | Chinese mirror of P1 status. |

---

## Task 1: Verify P0 Baseline

- [ ] **Step 1: Run the classifier regression before adding P1.**

```bash
pytest tests/unit/test_d1_dual.py::test_400_network_connection_lost_treated_as_transient -v
```

Expected: PASS. If this fails, stop and repair [IMP-ADR009-01](IMP-ADR009-01-d1-transient-classifier-fix.md) first.

- [ ] **Step 2: Confirm the new CLI is not already partially present.**

```bash
test ! -f apps/cli/db/drift_diagnose.py
test ! -f javdb/storage/drift_diagnose.py
```

Expected for a fresh P1 branch: both commands exit 0. If either file exists, inspect it and adapt the remaining tasks to the existing implementation instead of replacing it blindly.

---

## Task 2: Implement Verify-Log Suspect Discovery

**Files:**
- Create: `javdb/storage/drift_diagnose.py`
- Create: `tests/unit/test_drift_diagnose.py`

- [ ] **Step 1: Write the failing JSONL discovery tests.**

Create `tests/unit/test_drift_diagnose.py` with:

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from javdb.storage import drift_diagnose as diag


def _write_jsonl(path, records):
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_discover_verify_suspects_filters_window_and_zero_residuals(tmp_path):
    now = datetime(2026, 5, 24, 1, 0, tzinfo=timezone.utc)
    jsonl = tmp_path / "d1_drift.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "kind": "pending_session_verify",
                "ts": (now - timedelta(hours=1)).isoformat(),
                "session_id": "sid-clean",
                "pending_residual_count": 0,
            },
            {
                "kind": "pending_session_verify",
                "ts": (now - timedelta(hours=2)).isoformat(),
                "session_id": "sid-drift",
                "pending_residual_count": 2,
            },
            {
                "kind": "pending_session_verify",
                "ts": (now - timedelta(hours=48)).isoformat(),
                "session_id": "sid-old",
                "pending_residual_count": 5,
            },
        ],
    )

    suspects = diag.discover_suspects_from_verify_log(jsonl, since_hours=24, now=now)

    assert suspects == {
        "sid-drift": {
            "pending_residual_count": 2,
            "ts": (now - timedelta(hours=2)).isoformat(),
        }
    }
```

- [ ] **Step 2: Run the focused test and observe the expected failure.**

```bash
pytest tests/unit/test_drift_diagnose.py::test_discover_verify_suspects_filters_window_and_zero_residuals -v
```

Expected: FAIL because `javdb.storage.drift_diagnose` or `discover_suspects_from_verify_log` does not exist yet.

- [ ] **Step 3: Implement JSONL parsing and verify-log discovery.**

`discover_suspects_from_verify_log()` must:

- ignore missing files;
- skip malformed JSONL lines;
- accept ISO timestamps with trailing `Z`;
- require `kind == "pending_session_verify"`;
- require `pending_residual_count > 0`;
- require records inside the lookback window;
- keep the highest residual count if the same session appears more than once.

- [ ] **Step 4: Re-run the discovery test.**

```bash
pytest tests/unit/test_drift_diagnose.py::test_discover_verify_suspects_filters_window_and_zero_residuals -v
```

Expected: PASS.

---

## Task 3: Implement D1 Sweep And Verdict Classification

**Files:**
- Modify: `javdb/storage/drift_diagnose.py`
- Modify: `tests/unit/test_drift_diagnose.py`

- [ ] **Step 1: Add sqlite-shaped fixtures and verdict tests.**

Use an in-memory sqlite connection with these tables:

```sql
CREATE TABLE ReportSessions (
    Id TEXT PRIMARY KEY,
    Status TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL
);
CREATE TABLE PendingMovieHistoryWrites (
    Seq INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL,
    Href TEXT NOT NULL,
    ApplyState TEXT NOT NULL
);
CREATE TABLE PendingTorrentHistoryWrites (
    Seq INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL,
    Href TEXT NOT NULL,
    ApplyState TEXT NOT NULL
);
CREATE TABLE MovieHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT,
    Href TEXT UNIQUE,
    ActorName TEXT,
    DateTimeCreated TEXT
);
CREATE TABLE TorrentHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    MovieHistoryId INTEGER NOT NULL,
    MagnetUri TEXT,
    SubtitleIndicator INTEGER,
    CensorIndicator INTEGER,
    Size TEXT,
    DateTimeCreated TEXT
);
```

Cover these cases:

- committed D1 session with D1-only pending orphan and matching live rows -> `SAFE_TO_APPLY`;
- matching suspect with no actual pending rows -> `CLEAN`;
- live `MovieHistory` or `TorrentHistory` mismatch -> `ESCALATE_LIVE_DIVERGENCE`;
- missing or non-`committed` `ReportSessions` row -> `UNEXPECTED_PATTERN`;
- SQLite still has pending rows -> `UNEXPECTED_PATTERN`;
- D1 pending-table query failure -> `UNEXPECTED_PATTERN`, not `CLEAN`.

- [ ] **Step 2: Run the verdict tests and observe the expected failure.**

```bash
pytest tests/unit/test_drift_diagnose.py -k "classify or sweep" -v
```

Expected: FAIL until D1 sweep and classification are implemented.

- [ ] **Step 3: Implement D1 sweep.**

`discover_suspects_from_d1_sweep()` must:

- query committed sessions from `ReportSessions` inside the lookback window;
- count only pending rows with `ApplyState = 'pending'`;
- treat pending-count query failure as unknown, not zero;
- keep the affected session as suspect when count state is unknown.

Required count predicates:

```sql
SELECT COUNT(*) AS cnt FROM PendingMovieHistoryWrites
WHERE SessionId = ? AND ApplyState = 'pending'
```

```sql
SELECT COUNT(*) AS cnt FROM PendingTorrentHistoryWrites
WHERE SessionId = ? AND ApplyState = 'pending'
```

- [ ] **Step 4: Implement verdict classification.**

Use these verdict constants:

```python
VERDICT_CLEAN = "CLEAN"
VERDICT_SAFE_TO_APPLY = "SAFE_TO_APPLY"
VERDICT_ESCALATE = "ESCALATE_LIVE_DIVERGENCE"
VERDICT_UNEXPECTED = "UNEXPECTED_PATTERN"
```

Use these diagnose-mode exit mappings:

```python
{
    VERDICT_CLEAN: 0,
    VERDICT_SAFE_TO_APPLY: 1,
    VERDICT_ESCALATE: 2,
    VERDICT_UNEXPECTED: 2,
}
```

Classification must check `ReportSessions.Status` first when the reports connection is available. Missing, unreadable, or non-`committed` status is `UNEXPECTED_PATTERN`.

- [ ] **Step 5: Re-run the verdict tests.**

```bash
pytest tests/unit/test_drift_diagnose.py -k "classify or sweep" -v
```

Expected: PASS.

---

## Task 4: Add CLI Wrapper, Output, And Read-Only Docs

**Files:**
- Create: `apps/cli/db/drift_diagnose.py`
- Modify: `javdb/storage/drift_diagnose.py`
- Modify: `apps/cli/db/README.md`
- Modify: `tests/unit/test_drift_diagnose.py`

- [ ] **Step 1: Add CLI tests.**

Cover:

- `--help` exits 0;
- no suspects exits 0;
- at least one `SAFE_TO_APPLY` exits 1;
- at least one `ESCALATE_LIVE_DIVERGENCE` or `UNEXPECTED_PATTERN` exits 2;
- `--json` emits valid JSON with `suspects` or `sessions`;
- human output includes the suggested command only for `SAFE_TO_APPLY`.

- [ ] **Step 2: Create the canonical wrapper.**

`apps/cli/db/drift_diagnose.py`:

```python
from __future__ import annotations

import sys

from javdb.storage.drift_diagnose import main


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Implement CLI arguments.**

Support:

```text
--since <hours>                 default 24
--json                          machine-readable output
--drift-log <path>              default $REPORTS_DIR/D1/d1_drift.jsonl
--history-db <path>             default $REPORTS_DIR/history.db
--log-level <level>             default INFO
```

Do not expose `--apply` in this phase.

- [ ] **Step 4: Update `apps/cli/db/README.md`.**

Add:

```markdown
| `drift_diagnose.py` | ADR-009 D1 drift diagnostic. Diagnose mode is read-only and exits 0/1/2 for clean/fix-ready/escalate. |
```

- [ ] **Step 5: Verify P1.**

```bash
python3 -m apps.cli.db.drift_diagnose --help
pytest tests/unit/test_drift_diagnose.py -v
```

Expected: help exits 0 and all P1 tests pass.

