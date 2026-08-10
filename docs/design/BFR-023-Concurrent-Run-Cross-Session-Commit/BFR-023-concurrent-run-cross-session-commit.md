# BFR-023: Concurrent ingestion runs commit each other's live sessions, stranding pending writes

**Status**: Fixed
**Date**: 2026-07-26
**Severity**: High (silent history data loss on every overlapping AdHoc pair)
**Affected**: `apps/cli/db/commit_session.py`, `.github/workflows/StaleSessionCleanup.yml`, `docs/handbook/{en,zh}/ops/d1-rollback.md`
**Related**: [ADR-006](../_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md) (pause gate), [BFR-002](../_archive/BFR-002-Commit-Session-Misleading-Log/BFR-002-commit-session-misleading-log.md), [BFR-021](../BFR-021-D1-Session-Write-Silent-Loss/BFR-021-d1-session-write-silent-loss.md)

---

## Symptom

An Ad-Hoc run reported success but the notification email carried a critical
pending-mode alert in its subject:

```
[PENDING-PAUSE] (pending_residual_count=255 > 0 session=20260726T085643.729472Z-502b-0000) ✓ SUCCESS - JavDB Ad-Hoc Report 20260726 [彌生美月_弥生みづき]
```

The run's own `Mark sessions as committed` step looked benign — it just
reported the session as already done, with nothing to drain:

```
Commit done: committed=0 already_committed_or_missing=1 failed=0 claim_commits=1
"already_committed_or_missing": ["20260726T085643.729472Z-502b-0000"]
"pending_session_drains": []
```

D1 afterwards: `Status='committed'`, `CommittedAt='2026-07-26T08:58:42.035Z'`
— roughly three minutes *before* the spider finished — with 153
`PendingMovieHistoryWrites` + 102 `PendingTorrentHistoryWrites` rows still at
`ApplyState='pending'`, and only 29 of the session's 80 movies in
`MovieHistory`.

## Root Cause

`commit_session` accepts `--session-id` and `--run-started-at` together and
commits the **union** of the two. The second is a bare time-window scan
(`find_in_progress_sessions(since=...)`) with **no run scoping** — it matches
every `Status='in_progress'` session created after the timestamp, no matter
which workflow run owns it.

`AdHocIngestion.yml` deliberately carries no `concurrency:` group (the D1 +
Durable Object + pending-write architecture exists precisely so concurrent
ingestions of different URLs don't collide), so overlapping runs are routine.
What actually happened:

| Time (UTC) | Event |
|---|---|
| 08:45:56 | Run **A** `30195210787` starts, `RUN_STARTED_AT=08:46:03` |
| 08:51:46 | Run **B** `30195386608` starts |
| 08:56:43 | B creates session `20260726T085643…` — inside A's window |
| 08:58:35 | A commits its own session (245 rows) |
| **08:58:42** | **A commits B's still-spidering session** — the 145 rows staged so far are applied, `Status→committed` |
| 08:58:45–09:01:29 | B keeps staging → 255 more pending rows, for a session that is already `committed` |
| 09:06:06 | B's own commit step sees `sess_status == 'committed'`, skips the drain entirely (`commit_session.py`, the `write_mode == 'pending' and sess_status != 'committed'` guard) → 255 rows stranded |
| 09:07:53 | Email step reads `pending_residual_count=255` → `[PENDING-PAUSE]` |

A's log is the direct evidence — one invocation, two sessions, only one of
them its own:

```
Executing: python3 -m apps.cli.db.commit_session --run-started-at 2026-07-26T08:46:03Z --session-id 20260726T085050.063768Z-889e-0000
Pending session committed: id=20260726T085050.063768Z-889e-0000 … pending_marked_applied: 245
Pending session committed: id=20260726T085643.729472Z-502b-0000 … pending_marked_applied: 145
Commit done: committed=2 …
```

`ReportSessions.RunId` was already being stamped from `GITHUB_RUN_ID` at
session creation, so the ownership information needed to prevent this existed
the whole time — the window scan simply never consulted it.

## Fix

1. **`apps/cli/db/commit_session.py`** — the `--run-started-at` window is now
   intersected with the sessions carrying this run's `RunId`
   (`SessionLifecycleRepo().find_sessions_by_run`, keyed off `GITHUB_RUN_ID` /
   `GITHUB_RUN_ATTEMPT`). Foreign sessions are skipped with an explanatory INFO
   line. Local runs leave `RunId` NULL and have no concurrent peers, so the
   scan stays unrestricted there.

   The ownership lookup deliberately does **not** go through the
   `find_run_sessions` helper, which swallows errors and returns `[]` — that
   would make a transient D1 failure indistinguishable from "this run owns no
   other session" and silently skip the whole window. A failed lookup now
   always ends in a non-zero exit: without an explicit `--session-id` there is
   nothing left to do, and with one (the shape both ingestion workflows use)
   the explicit session is committed first — a `committed` session is shielded
   from the failure cleanup, so its writes land — and the step then goes red so
   an operator looks before the 48h stale sweep rolls the unidentified sibling
   sessions back. An ERROR line inside a green run protects nothing. Covered by
   five regression tests in `tests/unit/test_rollback_commit_cli.py`.

2. **`.github/workflows/StaleSessionCleanup.yml`** — `INPUT_APPLY` read
   `${{ inputs.apply }}` alone, and cron triggers leave `inputs` unset, so the
   daily safety net had been running **dry-run only** and never cleaned
   anything. Now `${{ github.event_name == 'schedule' || inputs.apply }}`:
   cron applies, manual dispatch keeps its dry-run default.

   Turning the cron on is not sufficient by itself. Rolling back an
   `in_progress` session only discards writes that never landed — safe
   unattended — but *resuming* a `finalizing` one replays its staged payload
   over the live tables, and by construction that payload is at least
   `--max-age-hours` old, so a later run may well have re-scraped the same
   `Href`. `cleanup_stale_in_progress` therefore grew a
   `--no-resume-finalizing` flag that reports those sessions as
   `needs_manual_review` instead; only the scheduled run passes it, so a
   manual dispatch still resumes — once the operator ticks `apply`, since
   `workflow_dispatch` keeps its dry-run default. Those sessions are counted
   separately (`manual_review_count`) rather than folded into
   `sessions_cleaned`, and each one logs a WARNING; the run still exits 0 on
   purpose, because these accumulate weekly while the alignment leak is open
   and a cron that is red every day is a cron nobody reads. Conflict-aware
   automatic resume is left as follow-up rather than half-designed here.

3. **`docs/handbook/{en,zh}/ops/d1-rollback.md`** — the runbook row for
   "`pending_residual_count > 0` on a `committed` session" asserted that the
   live tables are necessarily correct and told operators to clear the residue.
   Under this incident that advice **destroys unapplied writes**: both the
   manual `DELETE` and `db_commit_session_history` take the committed-session
   branch, which deletes `ApplyState IN ('pending','applied')` without applying
   anything.

   The row now says: drain, never delete — and is explicit about the two
   things `pending_residual_count` does *not* establish.

   **It is not proof the writes never landed.** `_commit_session_bulk` UPSERTs
   the live tables in one batch and only marks `ApplyState='applied'` in a
   later one; on D1 those are separate requests with no transaction spanning
   them, so a failure in between leaves applied data behind `'pending'` rows.
   `'pending'` means *unconfirmed*. That still makes deletion always wrong
   (unrecoverable if the data really is missing), but it makes the drain a
   **replay**: on an already-applied row it re-UPSERTs the old payload,
   overwriting `SessionId`, `DateTimeVisited`, actor fields and the torrent's
   `MagnetUri`/`Size`/`FileCount`/`ResolutionType`.

   **It says nothing about who owns the live row now.** Draining an old
   session can regress rows a later session has since updated. The runbook now
   carries a pre-drain check for that case and tells the operator to re-scrape
   the affected `Href`s instead when the regression isn't acceptable.

   This incident's own recovery was not exposed to either hazard: all 51
   `Href`s were absent from `MovieHistory`, verified before draining. That
   single check is sufficient for *both* live tables, and the reason is worth
   spelling out because it is not obvious: `TorrentHistory` has no `Href`
   column at all — it hangs off `MovieHistoryId INTEGER NOT NULL REFERENCES
   MovieHistory(Id)` — so an `Href` with no movie row cannot have torrent
   rows either. The drain agrees structurally: `live_movies_by_href.get(href)`
   returning `None` sends it down the INSERT path with a freshly generated
   `movie_id`, whose `live_torrents_by_mid` entry is necessarily empty, so
   every torrent write is an INSERT. No UPDATE and no variant DELETE can touch
   a pre-existing row.

   Four earlier drafts of this row were wrong and are recorded here because
   the failure modes are instructive — every one of them produced a *false
   zero*, i.e. told the operator "no conflict, go ahead and drain":

   1. Classified by whether the pending `Href`s were missing from
      `MovieHistory`. Unsound: `MovieHistory` is keyed by `Href` globally, so
      any re-scraped movie is already present. Verified against live session
      `20260726T093214…` — 1885 undrained pending rows, zero on that check.
   2. Over-corrected into treating `ApplyState` as proof of non-application,
      which the write-then-mark ordering above disproves.
   3. Checked only `PendingMovieHistoryWrites` for conflicts. Phase F marks
      the two pending tables in *separate* batches, so "movie rows `applied`,
      torrent rows still `pending`" is a legitimate half-applied state in
      which a movie-only check returns zero while unapplied torrent rows sit
      against a `Href` a later session has taken over. Verified with a fixture
      reproducing exactly that state (movie-only → 0, union → 1).
   4. Spanned both tables but still filtered `ApplyState='pending'`, while the
      drain reads `ApplyState IN ('pending','applied')` via
      `_pending_distinct_hrefs` and replays `applied` residue too. A session
      whose only conflicting rows are `applied` scored zero. Fixture again:
      pending-only → 0, drain's own state set → 1.

   The rule that finally holds, and the one worth carrying to any similar
   check: **a pre-drain safety check must mirror the drain's own scope
   exactly** — same tables, same states. Each of the four drafts narrowed that
   scope in a different way and each produced a false zero, i.e. told the
   operator "no conflict, go ahead". Deriving the check from what "residual"
   intuitively means, rather than from what the drain actually reads, is what
   kept reintroducing the bug.

   The same review round also removed a batch-shaped foot-gun from the
   `needs_manual_review` row: it suggested dispatching StaleSessionCleanup
   with `apply` as an alternative to resuming one session, but that workflow
   takes no session id and resumes every eligible stale session — replaying
   the ones the operator had not conflict-checked. Both rows now say to
   resume one at a time with `commit_session --session-id`.

## Recovery

The 255 stranded rows were recovered by reopening the session and draining it
through the normal path:

```sql
UPDATE ReportSessions SET Status='finalizing', CommittedAt=NULL
 WHERE Id='20260726T085643.729472Z-502b-0000' AND Status='committed';
```

```bash
python3 -m apps.cli.db.commit_session \
  --session-id 20260726T085643.729472Z-502b-0000 --no-claim-commit
```

Result: `pending_marked_applied: 255`, `movies_upserted: 51`,
`torrents_upserted: 102`, `hrefs_processed: 51`. `MovieHistory` for the session
went 29 → 80, `TorrentHistory` 58 → 160, residual → 0.

`committed → finalizing` is not a legal lifecycle transition and has no CLI —
the direct D1 `UPDATE` is deliberate, and is why the runbook now spells it out.

## Side Effects

- **The ADR-006 pause never engaged.** The email job created the 24h pause
  commit but `git push` was rejected (`! [rejected] main -> main (fetch first)`)
  by a concurrent run's push, and the step swallows it with
  `git push || echo "::warning::pause commit push failed"`. The detection half
  of the safety net worked; the enforcement half failed silently. Not fixed
  here — see Follow-Up.
- **Three unrelated `alignment` sessions** (2026-07-05 / 07-12 / 07-19) were
  found stuck in `finalizing` with 2185 undrained pending rows between them,
  surviving only because of the cron dry-run bug above. Resumed during this
  fix via `cleanup_stale_in_progress --apply` (1021 movies, 1164 torrents,
  drift 0).

  **That resume caused a small regression of its own**, discovered only after
  the fact when review surfaced the replay hazard. Those payloads were 1–3
  weeks old, so the replay rewrote `SessionId` / `DateTimeVisited` /
  `DateTimeUpdated` on 402 pre-existing `MovieHistory` rows (`DateTimeCreated`
  2025-06-29 — they were not new rows). Most were untouched in the interim:
  of the torrents under those movies, 401 belong to the alignment sessions
  themselves and only ~6 to later runs (the 07-15 / 07-16 / 07-23 dailies), so
  the regression is confined to provenance metadata on a handful of rows and
  no torrent content was lost. The pre-resume state is **not** reconstructible
  — the local SQLite mirror is from 2026-05-30, predating the alignment runs.

  The lesson is in the ordering: the cron fix was applied and exercised on real
  data *before* the replay semantics of `db_resume_finalizing_session` were
  understood. Enabling an automated path deserves the same scrutiny as writing
  one.
- Only the shared `reports/D1/d1_drift.jsonl` artifact is affected by
  concurrent auto-commits: whichever run pushes last wins, so B's own verify
  record never reached git. The alert path reads the file on the runner before
  the commit, so detection was unaffected.

## Follow-Up

- [x] Make the ADR-006 pause push survive a concurrent push. Fixed and merged
      as PR #258. Until that landed, the enforcement half of the pause gate
      could fail silently exactly as it did in this incident — detection
      worked, the marker never reached `main`.
- [x] Investigate why `align_inventory_with_moviehistory` leaves its sessions in
      `finalizing` with undrained rows. Fixed by PR #259: the tool defaulted to
      the SQLite mirror, and a stale mirror aborted the drain partway. Three
      consecutive weekly runs were affected before it was caught.

      This weakens — but does not remove — the rationale for the cron exiting 0
      on `needs_manual_review` above: with #259 merged these sessions should
      stop accumulating weekly, so once a few cycles confirm that, the policy
      is worth revisiting alongside the conflict-aware resume below.
- [ ] Consider making `commit_session` treat "session already `committed` but
      `pending_residual_count > 0`" as a hard failure rather than a silent
      `already_committed_or_missing`, so the run goes red instead of green.
- [ ] Conflict-aware resume for `finalizing` sessions: detect live rows a later
      session has taken over, skip or merge them, and let the cron resume
      automatically again. Until then the cron only reports them.

## Lesson

A time window is not an ownership claim. Any "sweep everything since T" query
that runs inside a workflow which is explicitly allowed to run concurrently
must be scoped by run identity — the identity column existed and was populated,
it just wasn't used. The rollback CLI got this right (its window scan is gated
behind `--include-orphaned` or "no other source yielded anything"); the commit
CLI, which is the more destructive of the two, did not.
