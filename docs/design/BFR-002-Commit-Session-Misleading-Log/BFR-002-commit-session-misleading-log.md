# BFR-002: commit_session reports "already committed" while simultaneously draining 126 pending writes

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Medium
**Affected**: `apps/cli/db/commit_session.py`, `javdb/storage/sessions/commit.py`, `javdb/storage/db/_db_history_write.py`
**Related**: [Issue #106](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/106), ADR-005 (pending-only write mode)

---

## Symptom

DailyIngestion run 26512511043 (2026-05-27) logged two contradictory messages 2 seconds apart:

```
21:07:03 Pending session committed: id=20260527T130201.940249Z-f385-0000 mode=pending
  drain={'movies_upserted': 27, 'torrents_upserted': 48, 'torrents_deleted': 10,
         'pending_marked_applied': 126, 'pending_deleted': 126, ...}
...
21:07:05 Commit done: committed=0 already_committed_or_missing=1 failed=0
```

The session appeared to be simultaneously drained (126 pending rows) and already committed.

## Root Cause

`db_commit_session_history()` is the canonical pending drain entry point and owns the `in_progress → finalizing → committed` transition. After a successful drain, the CLI still calls `db_mark_session_committed()` as an idempotent second step. Because the drain has already flipped the row to `committed`, `db_mark_session_committed()` returns 0 and the CLI used that rowcount alone to classify the session as `already_committed_or_missing`.

The log is misleading, not the state machine. `_db_history_write.py` also has an explicit `status == "committed"` branch that is not a no-op: when called directly on an already committed session, it DELETEs residual Pending rows left behind by a prior incomplete drain and now marks that return payload with `residual_cleanup=True`.

D1 verification confirmed no data loss: both `PendingMovieHistoryWrites` and `PendingTorrentHistoryWrites` have 0 rows for this session.

## Fix

Track whether the pending drain ran successfully. If it did, count the session as `committed` even when the follow-up idempotent `db_mark_session_committed()` returns 0. Also differentiate the "cleanup residual" path from "drain fresh pending" in CLI/service log output so operators can distinguish the two scenarios at a glance.

## Side Effects

None — log wording change only.

## Follow-Up

- [x] Count sessions committed by `db_commit_session_history()` as committed in the CLI summary
- [x] Differentiate log message for residual cleanup vs fresh drain
- [ ] Investigate why Pending rows survived the finalizing→committed transition (D1 batch timing?)
