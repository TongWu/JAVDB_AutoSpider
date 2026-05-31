# BFR-015: Rollback misses newer session-tagged tables, leaving orphans

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: Medium
**Affected**: `javdb/storage/db/_db_rollback.py`, `javdb/migrations/tools/cleanup_orphaned_session_rows.py`
**Related**: [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

---

## Symptom

The three D1 databases accumulated orphaned, session-tagged rows whose parent
`ReportSessions.Id` no longer existed (left behind by session rollbacks /
deletes). They surfaced as **14 `PRAGMA foreign_key_check` violations** in the
reports DB plus dangling rows elsewhere:

```
PRAGMA foreign_key_check (reports) → 14 rows
  ReportMovies(10) + SpiderStats(2) + UploaderStats(1) + PikpakStats(1)
```

Enumerating every `SessionId` / `session_id` column across the three DBs found
**5 orphaned session ids / 97 rows**:

| Session id | Orphan rows |
|---|---|
| `332` (legacy int) | MovieHistory(2), TorrentHistory(9), PikpakHistory(2), ReportMovies(10), SpiderStats(1), UploaderStats(1), PikpakStats(1) |
| `1820929777505280` (legacy) | SpiderStats(1) |
| `1821066622578688` (legacy) | MovieHistory(1), TorrentHistory(1) |
| `1821066695619584` (legacy) | MovieHistory(2), TorrentHistory(2) |
| `20260531T122613.805503Z-8382-0000` (recent, **failed today**) | PipelineEvent(2), ParseRunFieldFill(6), AcquisitionOutcome(56) |

The recent one is the smoking gun: its `PipelineEvent` log holds a `SessionFailed`
event at `2026-05-31T12:49:32Z`, i.e. it rolled back **today**, yet left rows in
`ParseRunFieldFill` and `AcquisitionOutcome` behind.

## Root Cause

`db_rollback_session` (`javdb/storage/db/_db_rollback.py`) deletes a failed
session's rows table-by-table from a **hard-coded list**, then deletes the
`ReportSessions` parent row. That list predated three later ADRs that each added
new session-tagged tables **without wiring them into rollback**:

- ADR-036 → `PipelineEvent`, `RunEventSummary` (reports)
- ADR-035 → `ParseRunFieldFill` (reports)
- ADR-027/035 → `OpsIncidents` (reports)
- ADR-033 → `AcquisitionOutcome` (operations)
- ADR-020 → `EmailNotificationHistory` (operations)

Because `_rollback_reports` / `_rollback_operations` never touched these tables,
every failed run that wrote to them deleted the `ReportSessions` parent and left
the children dangling. The four FK children (`ReportMovies` / `SpiderStats` /
`UploaderStats` / `PikpakStats`) **do** declare `REFERENCES ReportSessions(Id)`,
so their orphans became `foreign_key_check` violations; the newer tables carry no
FK (cross-DB or FK-free), so their orphans accumulated silently.

The deeper flaw is **structural**: nothing tied "a table has a session id" to "a
table has a rollback decision," so the gap re-opens every time a new
session-tagged table is added. The legacy-int orphans (`332` etc.) are older
debris from the pre-text-id era (their `ReportSessions` rows were removed by
earlier tooling while committed history persisted), but the same class of gap
produced today's `20260531…8382` orphans.

## Fix

1. **Forward fix** — `_rollback_reports` now also clears `RunEventSummary`,
   `ParseRunFieldFill`, `OpsIncidents`; `_rollback_operations` now clears
   `AcquisitionOutcome` (its column is `session_id`, not `SessionId`). The table
   lists are lifted to module-level single-source-of-truth constants
   (`ROLLBACK_REPORTS_TABLES`, `ROLLBACK_OPERATIONS_TABLES`,
   `ROLLBACK_HISTORY_PENDING_TABLES`, `ROLLBACK_PRESERVED_TABLES`).

2. **Deliberate exceptions** — `PipelineEvent` (ADR-036 append-only event spine;
   it records the `SessionFailed` event itself) and `EmailNotificationHistory`
   (logs emails really sent — an external action a rollback cannot undo) are
   **kept**. Their session id is provenance, not an ownership FK; they carry no
   FK to `ReportSessions` so they never violate `foreign_key_check`.
   `MovieHistory` / `TorrentHistory` remain durable dedup memory rollback
   preserves (only `Pending*` writes are undone).

3. **Recurrence guard** — `tests/unit/test_rollback_table_coverage.py` enumerates
   every session-tagged table in the schema and asserts each is either cleared by
   rollback or listed in `ROLLBACK_PRESERVED_TABLES`. A new session-tagged table
   now fails CI until its rollback disposition is decided.

4. **One-off data cleanup** — `javdb/migrations/tools/cleanup_orphaned_session_rows.py`
   deleted the 96 coupled orphan rows from D1 (history uses a movie-aggregate
   cascade: torrent children — including one with a NULL `SessionId` — before the
   movie). The 2 `PipelineEvent` rows were intentionally retained. Post-cleanup
   `PRAGMA foreign_key_check` (reports) returns **0**.

## Side Effects

- Rollback now deletes more rows per failed session (the four newer coupled
  tables). For an in-progress/failed session this is the intended cleanup; no
  committed run is affected (the `Status='committed'` guard is unchanged).
- The one-off cleanup deleted durable `MovieHistory` / `TorrentHistory` dedup
  rows for 3 dead legacy sessions (operator-approved). Those few old movies
  (e.g. `PFES-133`) would be re-evaluated if ever re-encountered by a scrape.
- The local SQLite mirror was not re-synced; per the D1-canonical policy it is a
  read-only mirror and will be re-aligned by the next
  `sync_d1_to_sqlite --force-overwrite-all`.

## Follow-Up

- [x] Clear existing orphans on D1; confirm `foreign_key_check` empty
- [x] Extend rollback cascade + add recurrence guard test
- [x] Update [d1-rollback.md](../../handbook/en/ops/d1-rollback.md) (en + zh)
- [ ] (Optional) Re-align the local SQLite mirror from D1 at next convenient sync
