# BFR-015: Orphaned session-tagged D1 rows — debris cleanup + rollback-table policy

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: Medium
**Affected**: `javdb/storage/db/_db_rollback.py`, `javdb/migrations/tools/cleanup_orphaned_session_rows.py`
**Related**: [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

---

## Symptom

The three D1 databases held session-tagged rows whose parent `ReportSessions.Id`
no longer existed. They surfaced as **14 `PRAGMA foreign_key_check` violations**
in the reports DB plus dangling rows elsewhere. A full scan found **5 orphaned
session ids / 97 rows**:

| Session id | Orphan rows |
|---|---|
| `332` (legacy int) | MovieHistory(2), TorrentHistory(9), PikpakHistory(2), ReportMovies(10), SpiderStats(1), UploaderStats(1), PikpakStats(1) |
| `1820929777505280` | SpiderStats(1) |
| `1821066622578688` | MovieHistory(1), TorrentHistory(1) |
| `1821066695619584` | MovieHistory(2), TorrentHistory(2) |
| `20260531T122613.805503Z-8382-0000` (failed today) | PipelineEvent(2), ParseRunFieldFill(6), AcquisitionOutcome(56) |

All 14 FK violations came from the four FK children of the two legacy integer
sessions (`ReportMovies(10) + SpiderStats(2) + UploaderStats(1) + PikpakStats(1)`).

## Root Cause

Two *different* things were both showing up as "orphans", and conflating them is
the actual trap:

1. **Genuine debris (a real bug, already prevented going forward).** The four
   reports FK children (`ReportMovies` / `SpiderStats` / `UploaderStats` /
   `PikpakStats`) declare `REFERENCES ReportSessions(Id)`. For the two legacy
   integer sessions their parent row was removed by pre-current tooling /
   manual ops while the children stayed, producing the 14 FK violations. The
   *current* `db_rollback_session` already deletes these four tables, so new
   failed runs do not reproduce this.

2. **By-design provenance (NOT a bug).** The newer ADR-033/035/036 tables tag
   rows with `session_id` as **provenance, not ownership**, and are explicitly
   decoupled from session/rollback:
   - `AcquisitionOutcome` — ADR-033 **D10**: "Enrichment writes bypass
     session/rollback … `session_id` is provenance only." Keyed by `qb_hash`;
     it tracks the real fate of a torrent that genuinely sits in qB.
   - `ParseRunFieldFill` — ADR-035: "Enrichment, off the Pending→Commit path";
     the commit-gate baseline reads only `committed=1` rows.
   - `OpsIncidents` — ADR-035 D3 raises a critical incident *as part of* the
     failed-session path; it is the run's own diagnosis.
   - `PipelineEvent` / `RunEventSummary` — ADR-036 append-only event spine
     (records `SessionFailed`) and its projection.
   For a rolled-back session these rows legitimately remain; they carry no FK
   to `ReportSessions`, so they never violate `foreign_key_check`.

The defect was the **absence of an explicit, enforced policy** separating (1)
from (2). Nothing tied "a table has a session id" to a deliberate
clear-vs-preserve decision — which is exactly what let the first iteration of
this fix wrongly cascade a rollback into the provenance tables (see Side
Effects).

## Fix

1. **One-off debris cleanup** — `javdb/migrations/tools/cleanup_orphaned_session_rows.py`
   deleted the genuine orphans (the four FK children + the legacy history rows,
   via a movie-aggregate cascade that includes a NULL-`SessionId` torrent child)
   from D1. Post-cleanup `PRAGMA foreign_key_check` returns **0** in all three
   DBs.

2. **Explicit policy + recurrence guard** (`javdb/storage/db/_db_rollback.py`).
   The clear-vs-preserve buckets are now module-level constants
   (`ROLLBACK_REPORTS_TABLES`, `ROLLBACK_OPERATIONS_TABLES`,
   `ROLLBACK_HISTORY_PENDING_TABLES`, `ROLLBACK_PRESERVED_TABLES`). Rollback
   clears **only** the FK children + the pre-existing session-owned ops/pending
   tables; the six provenance tables (`PipelineEvent`, `RunEventSummary`,
   `ParseRunFieldFill`, `OpsIncidents`, `AcquisitionOutcome`,
   `EmailNotificationHistory`) plus the durable dedup history
   (`MovieHistory`/`TorrentHistory`) are preserved. `tests/unit/test_rollback_table_coverage.py`
   asserts every session-tagged table in the schema is in exactly one bucket, so
   a new table fails CI until its disposition is decided.

3. **Docs** — [d1-rollback.md](../../handbook/en/ops/d1-rollback.md) (en + zh)
   rollback-table now lists the provenance tables as **NOT rolled back**.

## Side Effects

- **Self-corrected over-cascade.** The first iteration of this fix wrongly added
  `RunEventSummary` / `ParseRunFieldFill` / `OpsIncidents` / `AcquisitionOutcome`
  to the rollback cascade, and the cleanup tool deleted `ParseRunFieldFill(6)` +
  `AcquisitionOutcome(56)` for the recent failed session. A code review (Codex,
  P2 on `AcquisitionOutcome`) caught it; cross-checking ADR-033 D10 / ADR-035
  confirmed the misclassification. Reverted — these tables are now preserved.
- **Data loss (bounded).** The over-cascade emptied `AcquisitionOutcome` (it had
  only that one session's 56 rows). The ADR-033 reconcile loop re-derives
  `AcquisitionOutcome` from qB's live torrents by `qb_hash` on its next run, so
  tracking self-heals for torrents still in qB; the historical `session_id`
  provenance for those rows is lost (acceptable — it was provenance only). The
  6 deleted `ParseRunFieldFill` rows were `committed=0` telemetry the baseline
  already ignores.
- No committed run is affected; the `Status='committed'` rollback guard is
  unchanged.
- The local SQLite mirror was not re-synced — per the D1-canonical policy it is
  a read-only mirror, re-aligned by the next `sync_d1_to_sqlite --force-overwrite-all`.

## Follow-Up

- [x] Clear genuine orphans on D1; confirm `foreign_key_check` empty
- [x] Establish clear-vs-preserve policy + coverage guard
- [x] Revert the over-cascade after review; preserve the provenance tables
- [x] Update [d1-rollback.md](../../handbook/en/ops/d1-rollback.md) (en + zh)
- [ ] Let the next `reconcile.run()` re-populate `AcquisitionOutcome` from qB
- [ ] (Optional) Re-align the local SQLite mirror from D1 at next convenient sync
