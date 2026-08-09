# BFR-021: Session creation does not verify its write landed, masking a silent D1 write loss

**Status**: Fixed
**Date**: 2026-06-19
**Severity**: Medium
**Affected**: `javdb/storage/db/_db_reports.py`, `javdb/spider/app/run_service.py`
**Related**: [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [BFR-002](../_archive/BFR-002-Commit-Session-Misleading-Log/BFR-002-commit-session-misleading-log.md), [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.md), [run 27810377978](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27810377978)

---

## Symptom

The manual **Ad-Hoc Ingestion** [run 27810377978](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27810377978)
(session `20260619T065506.230983Z-d5f5-0000`) ran the spider, qB uploader,
file-filter, PikPak bridge and rclone dedup to completion, then failed only at
the final **Mark sessions as committed** step:

```
✗ __main__  Failed to commit session 20260619T065506.230983Z-d5f5-0000:
            20260619T065506.230983Z-d5f5-0000: None -> committed is not allowed
Commit done: committed=0 already_committed_or_missing=0 failed=1
```

Throughout Phase 2 onward the log carried 24 swallowed warnings (all
`code 7500`, `FOREIGN KEY constraint failed`):

| Component | Target table | Count |
|---|---|---|
| `CSVWriter` | `ReportMovies` / `ReportTorrents` | 21 |
| `Spider` | `SpiderStats` | 1 |
| `QBUploader` | `UploaderStats` | 1 |
| `PikPak` | `PikpakStats` | 1 |

A direct query against the canonical D1 **reports** database confirmed the
session and **all** its children are simply absent:

```
ReportSessions WHERE Id='20260619T065506.230983Z-d5f5-0000'  -> 0 rows
ReportMovies / SpiderStats / UploaderStats / PikpakStats      -> 0 rows each
```

The same query against the local SQLite mirror also returned nothing (the mirror
is a stale `STORAGE_BACKEND=d1` snapshot, newest row `2026-05-30`). Yet the
spider had logged a clean creation at `06:55:14`:

```
Spider  Created report session: id=20260619T065506.230983Z-d5f5-0000 ... write_mode=pending
```

The next scheduled run 13 minutes later (`27810934159`, `07:08`) created **and**
committed its session normally — so D1 was healthy; the loss was specific to
this run.

## Root Cause

Two layers — one **trigger** (not fully determined) and one **design flaw**
(fixed here).

**Trigger (undetermined).** `db_create_report_session`
([_db_reports.py](../../../javdb/storage/db/_db_reports.py)) issues a plain,
synchronous `INSERT INTO ReportSessions (... 'in_progress' ...)`. It carries no
write policy, so the D1 port executes it immediately (no batching, no recovery
outbox) and returns a cursor; the call did not raise (otherwise creation would
have aborted and the "Created report session" line would never print). Despite
the apparent success, the row never became visible on D1. No error for this
statement appears anywhere — not in the job log, not in the archived
`reports/D1/d1_drift.jsonl` (which only logs `rollback_summary` /
`pending_session_verify`, not per-write failures), and not in the recovery
outbox. `reports/D1/d1_port_summary.json` records `permanent_errors: 24` (the FK
cascade), `transient_errors: 4` with `retry_successes: 2` (two transients that
exhausted retries elsewhere), and `outbox_queued: 0`. The most consistent
explanation is a **silent write loss** — the backend acknowledged the request
but the row did not persist — but the artifacts cannot prove the exact cause,
and it has not recurred.

**Design flaw (the actual defect).** Nothing verified that the session write
landed before the pipeline committed six minutes of work to it:

1. `db_create_report_session` trusted the INSERT's apparent success and returned
   the `SessionId`.
2. `run_service.py` did read the row back
   ([run_service.py:617](../../../javdb/spider/app/run_service.py#L617)) — but
   only to mirror `WriteMode`; a `None` (row absent) result was **silently
   ignored**, and "Created report session" was logged regardless.
3. Every downstream write FK-references `ReportSessions(Id)`
   (`ReportMovies` / `SpiderStats` / `UploaderStats` / `PikpakStats`; with
   `ReportTorrents` chaining off `ReportMovies`). With the parent absent, all 24
   writes failed `FOREIGN KEY constraint failed` — but each is best-effort and
   only logged a warning, so the pipeline kept running.
4. `commit_session` reads the session state; `get_state` returns `status=None`
   when the row is absent ([lifecycle.py:95](../../../javdb/storage/sessions/lifecycle.py#L95)),
   so the legal-transition check rejected `None -> committed`
   ([lifecycle.py:126](../../../javdb/storage/sessions/lifecycle.py#L126)) and
   exited 1.

A silent D1 write loss thus turned into a full wasted run that failed late with
a cryptic message instead of failing fast at creation.

## Fix

Make session creation **read-your-write verify** the row before returning, in
the single choke point all callers share — `db_create_report_session`. The
read-back reuses the same connection (so it is read-your-write consistent and
free of any D1 read-replica lag), and under `dual` mode it routes to D1, so it
additionally catches a D1-leg drift on session creation:

```python
with _get_db(db_path or _REPORTS_DB_PATH) as conn:
    conn.execute("""INSERT INTO ReportSessions (...) VALUES (..., 'in_progress', ...)""", (...))
    # dual mode: read the D1 leg directly so a SQLite-mirror fallback cannot
    # mask a lost D1 write; d1/sqlite: conn is already the canonical store.
    verify_conn = getattr(conn, "_d1", conn)
    if verify_conn.execute("SELECT 1 FROM ReportSessions WHERE Id=?", (sid,)).fetchone() is None:
        raise RuntimeError(
            f"ReportSessions row {sid!r} is absent immediately after INSERT; "
            f"the session write did not durably land on the reports backend. ..."
        )
return sid
```

The `RuntimeError` propagates through `SessionLifecycleRepo.create_report_session`
into the existing handler in `run_service.py`
([run_service.py:648](../../../javdb/spider/app/run_service.py#L648)) — whose log
message already reads *"Aborting after init_db/db_create_report_session
failure"* — which logs and `sys.exit(1)`. The run now fails **at creation**,
before any scraping, with the offending `SessionId` named.

Regression test: `tests/unit/test_bfr021_session_write_verification.py` (happy
path still lands the row; a connection that ACKs the INSERT but reads back empty
raises `RuntimeError`).

## Side Effects

- One extra lightweight `SELECT 1` round-trip per session creation (once per
  run) — negligible.
- Under `STORAGE_BACKEND=dual` the verification reads the **D1 leg directly**
  (`conn._d1`) rather than going through `DualConnection.execute`. That matters:
  `DualConnection` writes the SQLite leg first and, on a D1 read error, falls
  back to the SQLite mirror (`dual_connection.py`) — which would let the local
  row mask a lost D1 write. Reading `conn._d1` makes a D1-leg session loss abort
  creation, aligned with "D1 is the canonical source of truth". (Thanks to the
  CodeRabbit review on [PR #238](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/238)
  for catching the fallback gap.)
- No false positives expected: the read-back shares the write's connection and
  D1's query API serves the primary (and retries transient blips), so the
  just-written row is visible. In d1/sqlite mode `conn` is already the canonical
  connection.

## Follow-Up

- [ ] **Trigger still unexplained.** If this recurs, the new `RuntimeError`
      fires immediately with the `SessionId`; at that moment capture a direct
      D1 `SELECT` for the row plus the run's `reports/D1/d1_port_summary.json`
      to characterise the loss.
- [ ] Consider extending the same read-your-write verification to other
      critical un-policied D1 writes (e.g. the lifecycle status transitions) if
      a similar silent loss is ever observed there.
- [x] Nothing to clean up for `20260619T065506.230983Z-d5f5-0000`: D1 holds no
      rows for it and the local SQLite mirror is already stale.
