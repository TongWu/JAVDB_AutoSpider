# BFR-020: D1 recovery-outbox replay resurrects writes for a rolled-back session

**Status**: Mitigated
**Date**: 2026-06-15
**Severity**: Medium
**Affected**: `javdb/storage/d1_port.py`, `javdb/storage/d1_recovery.py`, `javdb/storage/db/_db_rollback.py`, `javdb/storage/db/_db_connection.py`
**Related**: [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [BFR-015](../BFR-015-Session-Orphan-Cleanup-And-Rollback-Policy/BFR-015-session-orphan-cleanup-and-rollback-policy.md), [run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092)

---

## Symptom

The scheduled [Daily Ingestion run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092)
failed in **Step 1 - Run Spider** during Phase-1 detail processing. The spider
crashed (exit 1) on a transient Cloudflare D1 outage while flushing a batched
history stage-write:

```
javdb.storage.d1_client.D1TransientError: D1 API returned HTTP 500:
{"errors":[{"code":7500,"message":"internal error; reference = e_PVSMDp_b09a92a1761b4856a7e1e8bc6132a8b5"}]}
```

That crash is **not** the bug documented here — it is a Cloudflare-side blip
(`code 7500` + `internal error` + a CF incident `reference`), the D1 port already
retried 5× with exponential backoff (`D1_MAX_RETRIES=5`), and a re-run succeeds.
D1 was healthy when investigated (`SELECT 1` served by `SIN`), and no other run
in the prior ~10 days hit it.

The bug surfaced **after** the automatic `cleanup-on-failure` rollback ran. The
rollback reported success and `"history": {"PendingMovieHistoryWrites": 0}`, the
`ReportSessions` row for the session was gone, yet a direct D1 query found **one
orphaned `pending` row left behind**:

| Table | Row | `ApplyState` | `CreatedAt` | Parent `ReportSessions`? |
|---|---|---|---|---|
| `PendingMovieHistoryWrites` | `ALDN-131` (`SessionId=20260615T140318.589362Z-60d0-0000`) | `pending` | `2026-06-15 22:04:22` | **absent (rolled back)** |

It was the only orphaned pending row in the entire history DB.

## Root Cause

A **time-ordering hazard between the D1 recovery outbox and session rollback**.
There is no guard that ties an outbox replay to its parent session still being
alive, so a replay can re-materialise a write for a session that was already
rolled back.

Step by step, grounded in the run's evidence:

1. History stage-writes use a **batched, recovery-eligible** policy
   (`_pending_stage_policy`, [_db_history_write.py:92](../../../javdb/storage/db/_db_history_write.py#L92)):
   `ordering_key="history:<session_id>"`, `batching_allowed=True`,
   `recovery_allowed=True`.
2. The batch flush hit the transient HTTP 500. `D1Port.flush()`'s
   `except D1TransientError` handler ([d1_port.py:267](../../../javdb/storage/d1_port.py#L267))
   durably queued the failed batch — including `ALDN-131`'s `INSERT` — into the
   recovery outbox (`reports/D1/d1_recovery_outbox.jsonl`). The exception then
   re-raised and crashed the spider. **At this point nothing was committed to D1.**
3. `cleanup-on-failure` ran the rollback. `_rollback_pending_in_progress`
   ([_db_rollback.py:230](../../../javdb/storage/db/_db_rollback.py#L230)) issued
   `DELETE FROM PendingMovieHistoryWrites WHERE SessionId=?` with `policy=None`,
   so the statement executed **immediately** (it is not batched — see
   `_should_queue`, [d1_port.py:558](../../../javdb/storage/d1_port.py#L558)) and
   returned `rowcount=0`. A `0` from an immediate delete proves the row **was not
   in D1 at rollback time**. `_rollback_reports`
   ([_db_rollback.py:261](../../../javdb/storage/db/_db_rollback.py#L261)) then
   deleted the `ReportSessions` row.
4. **After** the rollback, a recovery-outbox replay
   (`startup_drain` / `replay_ordering_key`, [d1_recovery.py](../../../javdb/storage/d1_recovery.py),
   gated by `D1_STARTUP_REPLAY_ENABLED=true`) re-applied the queued `INSERT`,
   writing `ALDN-131` to D1 **carrying its original staging timestamp
   `2026-06-15 22:04:22`** — which is why the timestamp predates the rollback even
   though the row landed afterwards. Its parent `ReportSessions` row no longer
   exists, so it is an orphan.

The design flaw is that **rollback and the recovery outbox do not coordinate**:

- `db_rollback_session` deletes pending rows and the `ReportSessions` row but does
  **not** invalidate / drain the session's outbox events
  (`ordering_key="history:<session_id>"`).
- The replay path has **no precondition** that the parent session still exists or
  is not rolled back before it applies a queued write.

The resulting orphan is also **un-reapable by existing tooling**: a future
`db_commit_session_history` filters by a live session (and returns early when
`db_get_session_status` is `None`), and `StaleSessionCleanup` iterates
`ReportSessions` — neither can see a pending row whose parent session is gone.
This is the same *class* of orphan as [BFR-015](../BFR-015-Session-Orphan-Cleanup-And-Rollback-Policy/BFR-015-session-orphan-cleanup-and-rollback-policy.md)
(session-tagged rows with no parent), but a **new mechanism**: BFR-015's orphans
were FK children left by legacy/manual deletes; this one is *created* by the
recovery-outbox replay racing the rollback.

## Fix

Immediate debris cleanup only (root-cause code fix is Follow-Up). The single
orphan was deleted directly on D1 with the canonical-source-of-truth in mind:

```sql
DELETE FROM PendingMovieHistoryWrites
WHERE SessionId='20260615T140318.589362Z-60d0-0000';   -- changes=1
```

Post-cleanup verification: `0` rows for that session and `0` pending movie rows
DB-wide. `ALDN-131` already exists in committed `MovieHistory` (a 2026-04-10
entry), so no real history was lost — the orphan was inert litter.

The failed run was re-run (`gh run rerun 27551555092 --failed`); the retry
proceeds normally now that D1 is healthy.

## Side Effects

- None from the cleanup. The deleted row was an inert `pending` orphan with no
  parent session; the committed `MovieHistory` row for `ALDN-131` is untouched.
- The local SQLite mirror was not re-synced — per the D1-canonical policy it is a
  read-only mirror, re-aligned by the next
  `sync_d1_to_sqlite --force-overwrite-all`.
- No recurrence guard is in place yet, so a future transient D1 failure on a
  batched, recovery-eligible write **can reproduce this orphan** (see Follow-Up).

## Follow-Up

- [ ] **Guard the replay**: before `replay_ordering_key` applies a history write,
      verify the parent `ReportSessions` row still exists (and is not rolled
      back); otherwise skip the event and mark it processed / dead-letter rather
      than resurrecting it.
- [ ] **Coordinate rollback with the outbox**: have `db_rollback_session` drain or
      tombstone the session's outbox events (`ordering_key="history:<session_id>"`)
      so a later replay cannot re-apply them.
- [ ] **Add a reaper / coverage** for pending rows whose `SessionId` has no
      `ReportSessions` parent (extend
      `javdb/migrations/tools/cleanup_orphaned_session_rows.py` and add a periodic
      check), since neither commit nor `StaleSessionCleanup` covers this case.
- [ ] **Regression test** reproducing replay-after-rollback and asserting no
      orphan pending row remains.
