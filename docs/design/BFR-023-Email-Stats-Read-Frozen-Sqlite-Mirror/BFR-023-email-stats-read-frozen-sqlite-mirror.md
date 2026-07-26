# BFR-023: Pipeline emails silently lost their DB stats after the D1 cutover

**Status**: Fixed
**Date**: 2026-07-26
**Severity**: Medium
**Affected**: `javdb/integrations/notify/email/service.py`
**Related**: [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-047](../_archive/ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md), [BFR-021](../BFR-021-D1-Session-Write-Silent-Loss/BFR-021-d1-session-write-silent-loss.md)

---

## Symptom

No error, no failed job — the defect was the *absence* of a signal.

Since `STORAGE_BACKEND` was switched to `d1` (Production variable, 2026-05-22), every
DailyIngestion / AdHocIngestion notification rendered its Spider / Uploader / PikPak sections
from the CSV and log-derived fallback numbers instead of the recorded per-session stats rows.
The email still looked complete, so nothing flagged the degradation. The only trace was a
`logger.debug` line that CI log level never emits:

```text
DEBUG  SQLite stats not available: <...>
```

Corroborating evidence that the local mirror has been frozen since the cutover — every commit
touching it is a manual re-alignment, never a pipeline auto-commit:

```shell
$ git log -3 --format='%h %ad %s' --date=short -- reports/reports.db
7e368500 2026-06-10 refactor(storage): retire the public db_* facade (ADR-046 Phase 4)
f992a10b 2026-05-31 chore(db): sync local sqlite mirrors for ADR-024
d06cab32 2026-05-31 chore(db): realign local SQLite mirror to D1 (absolute hrefs, BFR-010)
```

## Root Cause

**A correct rule outlived the premise it was built on.**

The P0-6 rule said: *stats MUST come from the canonical SQLite mirror, never from D1.* Its
**behaviour** was right when it was written, though its wording was not: dual mode has always
been migration validation only, and SQLite has never been the authority — drift resolves toward
D1 (CLAUDE.md, "D1 is the canonical source of truth"). What the rule was really doing under
dual was reading the side that had *definitely* received the write, so that an asymmetric
dual-write leaving D1 short by N rows would show up instead of being papered over — exactly the
2026-05 `ReportSessions` / `SpiderStats` `-1` incident. That is an observability exception, and
it only makes sense while dual mode is still proving out the D1 write path. It was enforced
with dedicated `_local` repo variants that open a raw `sqlite3` connection regardless of
backend.

The d1-only cutover inverted the premise without touching the rule. D1 became canonical and the
SQLite mirror stopped being written at all (`get_db()` hands out a `D1Connection`, never
`_open_sqlite_connection`). The forced-local read then had no possible outcome other than
failure: it looked up *this* run's `SessionId` in a file that no run had written since May.

Two secondary factors kept it invisible:

1. **The gate keyed off the wrong axis.** The block was guarded by `use_sqlite()`, which reads
   `STORAGE_MODE` (`csv` / `db` / `duo`) — the CSV-vs-DB question — not `STORAGE_BACKEND`
   (`sqlite` / `d1` / `dual`), the which-database question. With `STORAGE_MODE=duo` in
   Production the gate stayed open, so the code kept confidently reading the wrong side rather
   than skipping.
2. **The failure path was designed to be quiet.** Stats are decoration on top of the log/CSV
   numbers, so the whole block sits in `try/except` → `logger.debug`. Correct for a transient
   backend hiccup; it also swallowed a permanent, structural miss.

There was no test pinning *which* backend the email reads from — only
`tests/unit/test_email_notification_p0.py`, which asserts that `db_get_spider_stats_local`
itself opens SQLite directly. That stayed green because the repo method was never the broken
part.

## Fix

[PR #256](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/256).

The rule is unchanged in intent — *read the side that actually received this run's writes* — and
is now pointed at whichever side that is:

| `STORAGE_BACKEND` | Source | Why |
| --- | --- | --- |
| `d1` | D1 (backend-aware `StatsRepo` / `SessionsRepo` methods) | D1 is the source of truth; the SQLite mirror is frozen. |
| `sqlite` | local SQLite (`*_local` variants) | The only backend in play. |
| `dual` | local SQLite (`*_local` variants) | **Observability exception, not an authority claim.** D1 remains the source of truth and drift still resolves toward D1. Reading the side that definitely received the write is what makes a D1 shortfall visible; this read must never be taken as evidence that a D1 write succeeded, nor used to reconcile. Behaviour unchanged from P0-6. |

Key changes in `javdb/integrations/notify/email/service.py`:

- Extracted the inline block into `_load_run_stats()` returning a `_RunStats` NamedTuple, so
  the source-selection rule is reachable by a test at all.
- Gate now reads `current_backend()` (a pure config read, safe before `init_db()`), with
  `use_sqlite()` retained only for the sqlite/dual branch it actually describes.
- The no-`--session-id` fallback became backend-aware too: `SessionsRepo.get_latest_session()`
  under d1, `SessionLifecycleRepo.get_latest_session_local()` otherwise.
- Fixed a latent `NameError`: three `logger.info(f"... {_cur_be()} ...")` calls referenced a
  name bound only inside the old `if use_sqlite():` branch. They were unreachable while stats
  always came back empty, and fired the moment the d1 path started returning rows. They now use
  the `backend_label` the helper reports. Caught by
  `tests/harness/test_scenario_notify.py::test_daily_notify_email_is_captured`.

New test `tests/unit/test_email_stats_source.py` (7 cases) pins the selection matrix, the
backend-aware session fallback, and the degrade-to-empty behaviour. Verified adversarially:
reverting the production logic to the old always-local form fails 4 of the 7, while the three
sqlite/dual cases stay green.

## Side Effects

- **Emails now report real numbers again under d1.** Figures in the Spider / Uploader / PikPak
  sections may differ from recent runs — the previous ones were log/CSV estimates, not the
  recorded stats rows. This is the fix working, not a regression.
- **One extra D1 read set per notification** (spider + uploader + pikpak, by `SessionId`).
  Negligible, and it runs inside the existing best-effort `try/except`.
- **`sqlite` and `dual` behaviour is byte-for-byte unchanged.** The forced-local path and its
  anti-drift guarantee are untouched, which the parametrised tests assert.
- The audit for other consumers of the forced-local variants came back clean: `_local` repo
  methods have exactly one caller outside the DB layer, and it is this file. The neighbouring
  `use_sqlite()` gate in `log_analysis.py` reads through the backend-aware
  `OperationsRepo.load_dedup_records()`, so it was never affected.

## Follow-Up

- [ ] Surface the stats source in the email body (or promote the "stats unavailable" log to
      `warning`), so the next silent degradation is visible without reading the code. The whole
      class of bug here is *quiet fallback on a permanent failure*.
- [ ] Consider renaming `use_sqlite()` — the name invites exactly the confusion above.
      `STORAGE_MODE` (csv-vs-db) and `STORAGE_BACKEND` (which-database) are orthogonal, and
      `javdb/infra/config.py:128` already needs `use_sqlite() or storage_backend() in ('d1','dual')`
      to express "the DB layer is in play", which is what most callers actually mean.
- [ ] When the SQLite mirror is finally retired, delete the `*_local` repo variants and this
      branch outright rather than leaving a dead sqlite/dual arm.
