# BFR-033: `git rebase -X ours` discarded a run's own results during auto-commit push conflicts

**Status**: Fixed
**Date**: 2026-08-12
**Severity**: High (silent partial data loss on every conflicting concurrent auto-commit)
**Affected**: `.github/workflows/DailyIngestion.yml`, `.github/workflows/AdHocIngestion.yml`
**Related**: [BFR-035](../BFR-035-Concurrent-Run-Cross-Session-Commit/BFR-035-concurrent-run-cross-session-commit.md) (same 2026-07-26 parallel-AdHoc pair), [BFR-007](../_archive/BFR-007-Reports-Artifact-Stale-Files/BFR-007-reports-artifact-stale-files.md), [BFR-034](../BFR-034-Sqlite-Lastrowid-As-D1-Foreign-Key/BFR-034-sqlite-lastrowid-as-d1-foreign-key.md) (sibling in the same review batch), commit `4cc7569a`

---

## Symptom

An ingestion run scraped and committed its data to D1 normally, its auto-commit
step printed the success banner, the job went green — and its rows were missing
from the shared report files in git. Nothing in the log, the notification email,
or `git log` indicated a loss.

Both ingestion workflows end in a push retry ladder that escalates through four
strategies when the remote has moved ahead. The final rung was:

```bash
# Try to accept our changes to resolve conflict
echo "Attempting to resolve conflicts by keeping local changes..."
if git rebase "origin/$CURRENT_BRANCH" -X ours 2>/dev/null; then
  echo "Rebase with ours strategy successful"
```

On conflict this exited `0`, printed `Rebase with ours strategy successful`, fell
through to `git push`, and then to:

```
✓ Changes committed and pushed to $CURRENT_BRANCH branch successfully
```

## Root Cause

**In `git rebase` the merge sides are swapped relative to `git merge`.** Rebase
replays each local commit *onto* the upstream, so during each replay the upstream
is the checked-out side (`ours`) and the commit being replayed — this run's work —
is the incoming side (`theirs`). Therefore:

| Command | `-X ours` keeps | `-X theirs` keeps |
| --- | --- | --- |
| `git merge origin/BRANCH` | this run's side | the other run's side |
| `git rebase origin/BRANCH` | **the other run's side** | **this run's side** |

The ladder was written with merge semantics in mind — the comment said "accept our
changes" and the echo said "keeping local changes" — but the command was a
*rebase*. Every conflicting hunk was therefore resolved to the already-pushed
content of the concurrent run, and this run's own hunk was dropped.

Four things conspired to make the loss silent rather than loud:

1. **Inverted semantics.** The strategy did the exact opposite of its stated
   intent, and the surrounding prose asserted the intent rather than the effect.
2. **`2>/dev/null`.** Rebase writes its conflict and auto-resolution reporting to
   stderr. Silencing it removed the only in-log evidence that hunks had been
   thrown away.
3. **No annotation.** Auto-resolution of a *destructive* conflict emitted no
   `::warning::`, so nothing surfaced in the job summary or the run's checks.
4. **Concurrency is a supported path, not an accident.** `AdHocIngestion.yml`
   deliberately carries no `concurrency:` group — its header comment states that
   parallel dispatches are expected by design, because per-session isolation
   lives in the SessionId and the pending-write architecture. So the ladder's
   last rung is not an exotic edge case; overlapping AdHoc dispatches append to
   the same shared report files as a matter of routine.

The deeper flaw is that the pending-write / SessionId architecture makes the
*database* safe under concurrency, and the auto-commit step silently inherited an
assumption that git would be safe too. It is not: the shared report files are
line-oriented, append-heavy, and rewritten by every run.

## Evidence

A real production loss, reconstructible entirely from committed state.

Two AdHoc runs overlapped on 2026-07-26. Both are recorded in
`reports/D1/d1_drift.jsonl` (lines 129-130), 8 seconds apart:

| Run | Session | `hrefs_processed` | `movies_upserted` | `commit_session` at |
| --- | --- | --- | --- | --- |
| [30195210787](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/30195210787) | `20260726T085050.063768Z-889e-0000` | 49 | 49 | `2026-07-26T08:58:37Z` |
| [30195386608](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/30195386608) | `20260726T085643.729472Z-502b-0000` | 29 | 29 | `2026-07-26T08:58:45Z` |

The first run pushed cleanly at 16:59:31 SGT (`9f77f81a`), touching
`reports/parsed_movies_history.csv` with `+69 / -25`. The second run's auto-commit
did not land until 17:07:20 SGT (`32e4a528`) — eight minutes later, i.e. after
working down the retry ladder — and its stat line is the fingerprint of the bug:

```
 reports/AdHoc/2026/07/Javdb_AdHoc_actors_彌生美月_弥生みづき_20260726.csv | 81 ++++++++++
 reports/parsed_movies_history.csv                                       |  4 --
 2 files changed, 81 insertions(+), 4 deletions(-)
```

A run that upserted 29 movies to D1 contributed **zero** lines to the shared
history CSV and *removed* four. The dated AdHoc CSV survived intact (it is unique
to the run, so it never conflicted); only the shared file lost content. The
actress in that filename (`彌生美月_弥生みづき`) matches the session named in
[BFR-035](../BFR-035-Concurrent-Run-Cross-Session-Commit/BFR-035-concurrent-run-cross-session-commit.md),
confirming `32e4a528` is run `30195386608`.

One dropped row, traced across revisions:

```bash
$ for rev in 9f77f81a 32e4a528 23def421 HEAD; do
    printf "%s: " $rev
    git show $rev:reports/parsed_movies_history.csv | grep -c "^/v/nKem06,"
  done
9f77f81a: 1
32e4a528: 0
23def421: 0
HEAD: 0
```

`/v/nKem06` (`HNDS-078`) was present before the conflicted push, gone after, and
has never returned — not even through `23def421`, the next AdHoc run, which
re-serialised 745 lines of the same file.

## Blast Radius

- **The loss is partial, per conflicting hunk — not whole-commit.** A run keeps
  every non-conflicting change and loses only the hunks that collided. There is no
  failed step, no empty commit, and no missing file to notice. Detecting it
  requires diffing the commit stat in the job log against the stat of the commit
  that actually landed.
- **Files at risk under the production `STORAGE_BACKEND=d1` setting** — every path
  staged unconditionally by the auto-commit step (`DailyIngestion.yml` →
  `Commit and Push Results` → the `STEP 1: Stage all files` block):
  `reports/D1/d1_recovery_outbox.jsonl`, `reports/D1/d1_recovery_outbox.processed.jsonl`,
  `reports/D1/d1_drift.jsonl`, `reports/D1/d1_drift.processed.jsonl`,
  `reports/D1/d1_port_summary.json`, plus the dated `reports/DailyReport/**.csv`
  and `reports/AdHoc/**.csv` and `reports/parsed_movies_history.csv`.
- **The D1 state logs are the expensive ones.** `d1_recovery_outbox.jsonl` and
  `d1_drift.jsonl` are not mirrors of anything — they are the *only* record of
  divergence and of writes queued for replay, and are staged unconditionally
  precisely so they survive failed runs. A dropped line there is unrecoverable.
  `parsed_movies_history.csv` is a mirror in `d1` mode, but it is still read back
  as spider history input when history loading is enabled
  (`load_parsed_movies_history` in `javdb/spider/app/run_service.py`), so dropped
  rows can cause re-processing.
- **The race is wider than Daily-vs-AdHoc.** The `Commit and push results` steps
  of `Migration.yml` and `WeeklyDedup.yml` stage the same five `reports/D1/` state
  files through the same `D1_STATE_FILE` loop. Any pair of those workflows overlapping is exposed —
  the two ingestion workflows were simply the pair that carried the broken ladder.

## Fix

Commit [`4cc7569a`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/4cc7569a)
— `fix(ci): keep this run's changes when auto-resolving push conflicts`.

1. **`-X ours` → `-X theirs`** in both `DailyIngestion.yml` and
   `AdHocIngestion.yml`, so the behaviour finally matches the stated intent.
2. **Un-silenced the rebase.** Dropped `2>/dev/null`, with an explicit
   *"do not re-add"* comment, so conflict reporting stays on the job log.
3. **Annotated the auto-resolution.** A `::warning::` now fires whenever the
   strategy is used, naming the shared report files to check:

   ```
   ::warning::Push conflict auto-resolved with -X theirs (this run's version kept
   for conflicting hunks) — verify shared report files
   (reports/D1/d1_recovery_outbox.jsonl, reports/D1/d1_drift.jsonl,
   reports/DailyReport/**.csv) for rows dropped from a concurrent run
   ```
4. **Corrected four stale comments** that named `-X ours` as the clobbering
   mechanism (two per workflow, in the `STORAGE_BACKEND` env note and the
   `stage_db_mirrors` note).
5. **Pinned the contract** with `tests/unit/test_workflow_push_conflict_strategy.py`,
   parametrised over both workflows:
   `test_forced_rebase_keeps_this_runs_hunks`,
   `test_forced_rebase_output_is_not_silenced`,
   `test_forced_rebase_success_is_annotated`.

The ladder's shape is unchanged: plain rebase → merge → strategy rebase →
fallback branch (`.github/scripts/fallback_push.sh`). Only the last rung's
resolution direction, its visibility, and its annotation changed.

## Side Effects

- **The direction of loss is now inverted, not eliminated.** `-X theirs` resolves
  a conflicting hunk wholly to this run's version, so the *other* run's lines in
  that hunk are dropped instead. This is a deliberate trade: the losing side is
  now the run that already pushed and already emitted its own artifacts and email,
  whereas previously the loser was the run standing right there with unsaved
  results. It is also now announced by a `::warning::`. A whole-hunk strategy is
  the wrong primitive for append-only files in either direction, which is why the
  `merge=union` driver (Follow-Up, landed in the same batch) matters more than the
  direction flip: it stops the lossy rung being reached at all for those files.
- **Job logs are noisier on the conflict path.** Rebase conflict output now
  appears. This is intended; it was the missing evidence.
- No change to the ladder's control flow, retry counts, backoff, or fallback
  branch behaviour. No workflow inputs, config keys, or env vars changed. No
  schema change, no migration.

## Follow-Up

- [x] Flip the strategy option to `-X theirs` in both ingestion workflows
- [x] Stop silencing rebase stderr on the strategy rung
- [x] Emit a `::warning::` whenever the strategy auto-resolves, naming the files to audit
- [x] Correct the four stale comments naming `-X ours`
- [x] Pin the strategy, the un-silencing, and the annotation with a contract test
- [x] **Union merge driver for append-only report files.** A whole-hunk strategy
      cannot be correct for files where both sides' lines are wanted, so
      `.gitattributes` now marks the append-only report files `merge=union`,
      letting git keep both runs' lines instead of picking a winner — which means
      the ladder's *first* rung (plain `git rebase`) succeeds losslessly and the
      lossy rung never fires for them: `reports/D1/d1_drift.jsonl`,
      `reports/D1/d1_drift.processed.jsonl`,
      `reports/D1/d1_recovery_outbox.jsonl`,
      `reports/D1/d1_recovery_outbox.processed.jsonl`,
      `reports/pikpak_bridge_history.csv`, `reports/DailyReport/**/*.csv`,
      `reports/AdHoc/**/*.csv`. Whole-document rewrites are deliberately excluded
      and keep the default driver (`d1_port_summary.json`, `rclone_inventory.csv`,
      `dedup_history.csv`, `parsed_movies_history.csv`, the LFS `reports/*.db`) —
      concatenating two halves of a rewritten document is not a valid document.
      Landed as a sibling change in this review batch, commit
      [`85fd3cfa`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/85fd3cfa)
      (`.gitattributes` carries the rationale inline).
- [x] Audit the other two writers of the same `reports/D1/` state files.
      The push loops in `Migration.yml` → `Commit and push results` and
      `WeeklyDedup.yml` → `Commit and push results` do **not** carry this bug —
      they use `git pull --rebase origin "$CURRENT_BRANCH" || true` with no
      strategy option — but the `|| true` swallows a conflicted rebase rather than
      resolving it, which is a different silent-failure shape on the same shared
      files and deserves its own look.
      **Done.** The sweep found a third writer with the identical loop
      (`RcloneManager.yml` → `Commit and push results`) plus the same swallow in
      six pre-run `Pull latest changes` steps. All nine sites now test the rebase with `if`,
      `git rebase --abort` on failure so the tree is never left mid-conflict
      (the old `|| true` burned the whole retry budget on
      *"there is already a rebase-merge directory"*), and annotate with
      `::warning::`; the three auto-commit loops still escalate to
      `fallback_push.sh` with `$LOCAL_COMMIT` on exhaustion, and the six
      pre-run pulls stay non-fatal (nothing exists to lose yet) but loud.
      Fixed: `Migration.yml`, `WeeklyDedup.yml`, `RcloneManager.yml`,
      `RollbackD1.yml`, `DailyIngestion.yml`, `AdHocIngestion.yml` (pre-run
      pull only — the push ladder is untouched). Pinned repo-wide by
      `tests/unit/test_workflow_git_push_not_swallowed.py`, which fails on any
      git push/pull/rebase/merge whose failure is discarded by `|| true`,
      exempting only an abort cleaning up its own preceding rebase — a
      standalone `git rebase --abort || true`, or one whose immediately
      preceding segment is `git pull --rebase` / `git rebase` (so
      `git push … || git rebase --abort || true` is still flagged).
- [ ] Consider whether a `concurrency:` group is warranted around the *auto-commit
      step alone* (not the pipeline), which would remove the conflict window
      without giving up the parallel-dispatch property AdHoc deliberately relies on.
