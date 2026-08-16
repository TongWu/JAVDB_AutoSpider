# BFR-036: Publishing re-keyed the whole mirror history whenever `exclude_paths` changed

**Status**: Fixed
**Date**: 2026-08-15
**Severity**: High
**Affected**: `.github/workflows/publish-to-public.yml`, `.publish-config.yml`, `.github/scripts/publish_mirror.py`
**Related**: [PR #157](https://github.com/TongWu/JAVDB_AutoSpider/pull/157), [PR #154](https://github.com/TongWu/JAVDB_AutoSpider/pull/154), [BFR-034](../BFR-034-Sqlite-Lastrowid-As-D1-Foreign-Key/BFR-034-sqlite-lastrowid-as-d1-foreign-key.md)

---

## Symptom

The `dev → main` promotion PR on the public repository (PR #157) showed
**1450 changed files and roughly +408K lines**, against a true content delta of
**103 files / +10298 / −245**. Downstream tooling degraded accordingly:

- CodeRabbit skipped review entirely (its 150-file ceiling was exceeded).
- CodeQL reported pre-existing findings as new, and said so itself: *"Alerts not
  introduced by this pull request might have been detected because the code
  changes were too large."*
- Merging required hand-resolving dozens of `add/add` conflicts — the same
  content appearing as a fresh addition on two unrelated histories.

`git merge-base origin/dev origin/main` returned nothing: the two branches had
**no common ancestor at all**.

PR #154 had shown a milder version of the same thing a week earlier, and its
body attributed it to history rewriting being inherent to publishing.

## Root Cause

Publishing ran `git filter-repo` over the **entire** private history on every
run and force-pushed the result to the public `dev` branch.

The initial diagnosis in the PR #154 body — that filter-repo regenerates fresh
SHAs on every run, making this an unavoidable structural cost of publishing —
was **wrong**, and it mattered: it framed a fixable bug as a fact of life.
`git filter-repo` is deterministic. Two independent runs over the same history
with the same arguments produced byte-identical output across all 2621 rewritten
commits. (That count is for private `main`; the 2618 below is the same history
truncated at `868ce5ff`, the commit the two argument sets are compared at.)

The real trigger is that **`exclude_paths` is one of those arguments**. Change
the list and every historical tree changes, so every commit is re-keyed and the
published history shares no ancestry with what was published before.

Verified by replaying a single fixed private commit
(`868ce5ff`, *"fix(ops): stop the reconciler deleting missingFiles torrents
unverified (#275)"*) under each argument set:

| Filter arguments | Resulting SHA | Where it appears |
| --- | --- | --- |
| Before `7c6f3f8c` | `fcebe702` | public `main` (published 2026-08-09) |
| After `7c6f3f8c` | `c28e40c3` | public `dev` (published 2026-08-15) |

The trigger was `7c6f3f8c` (2026-08-12), the *"make publish exclusion globs
effective"* change carried inside the BFR-034 PR. It routed glob entries from
`--path` to `--path-glob` and replaced the dotenv pattern set — correct fixes
in themselves, and neither one announced that it would reset the mirror.

Two details make the cost especially clear:

- **2612 of 2618 commits** were re-keyed, with the divergence reaching back to
  2025-06-27.
- The published **tree was byte-identical** either way — both argument sets
  produced tree `efe0185c` at the publish snapshot. The entire ancestry loss
  bought no change in published content whatsoever.

`exclude_paths` had been edited **7 times** since 2026-01, so this was a
recurring trap, not a one-off.

### The second, chronic defect

Independently of any config edit, the workflow force-pushed a history that had
never seen the public repository. Two consequences:

1. The previous `Sync from private repository` commit was orphaned on every run.
2. Commits made directly on the public repo (e.g. `044e5a71 Delete
   .github/workflows/ReconcileLibrary.yml`) survived on `main` but were wiped
   from `dev`.

So `main` could never be an ancestor of `dev`, and every promotion produced
phantom conflicts. This is what PR #154 hit. The deeper problem is that **`dev`
was a force-pushed mirror being treated as a branch you can raise a PR from** —
mirroring and merge-based promotion are incompatible.

## Fix

Publishing is now **append-only**. `git filter-repo` is gone; all scrub and
rewrite logic lives in `.github/scripts/publish_mirror.py`.

Each private commit not yet on the mirror is replayed as one public commit on
top of the mirror's **current tip**, with excluded paths stripped and workflow
modifications applied. Every publish is a fast-forward, so:

- editing `exclude_paths` changes only what future commits carry, and never
  disturbs published history;
- a promotion PR always shows the real diff;
- the public repo is **never force-pushed** on the normal path — a rejected push
  is surfaced as an error instead of being forced through.

Key elements:

- **Sync cursor.** Each replayed commit carries a `Private-Commit: <sha>`
  trailer. The newest one reachable from the mirror tip is the resume point, so
  no side-band state is needed. It names the last private commit that *produced*
  a published commit; commits that are empty after the scrub (an `Auto-commit`
  touching only `reports/`) publish nothing and are simply re-examined next run.
- **Retroactive scrub preserved.** Append-only cannot remove a path from
  already-published commits, so `--full` (workflow input `full_remirror`)
  rebuilds every commit from the root under today's `exclude_paths` and force
  pushes. Same engine, so there is only one definition of "excluded". It walks
  first-parent only, so a rebuilt mirror is linear.
- **Trees are built through the index**, never a working directory. An earlier
  draft materialised each commit and ran `git add -A`; `git add` honours
  `.gitignore`, so `.dockerignore` — tracked here *and* listed in this repo's
  `.gitignore` — was silently dropped from the mirror. Caught by the tree
  equivalence check below, now pinned by a test and by a completeness assertion
  (`assert_tree_complete`) that fails the job if the built tree loses any path
  the scrub did not ask to drop.

### Guards added during review

Three ways the append-only model could be broken from outside, each now enforced
rather than documented:

- **Publishing from a feature branch.** The cursor records the source commit, so
  a publish from a branch writes a SHA that a squash or rebase merge leaves out
  of the default branch — after which every later publish aborts with "not an
  ancestor". The workflow now refuses a real publish from any non-default ref;
  dry runs stay allowed, since they push nothing.
- **Direct commits on the mirror branch.** A fast-forward push is not evidence
  that nothing was destroyed: each tree is a full snapshot, so a direct commit
  fast-forwards *and* disappears. The build step now anchors on the newest
  commit carrying a `Private-Commit` trailer and reports anything above it —
  but only when that content would actually be lost. A commit that leaves the
  tree untouched (merging the public `main` back into the mirror) passes, and
  so does one whose change this publish reproduces (the fix was ported into the
  private repo, exactly as the error message advises). Anchoring on provenance
  rather than rebuilding a tree matters: a rebuilt-tree comparison fires on
  every `exclude_paths` edit, since the tree published under the old config
  legitimately differs from what the new one produces. `allow_public_drift`
  overrides the gate once the paths have been reviewed.
- **Squash or rebase on the promotion PR.** Either gives `main` SHAs `dev` does
  not contain, so the merge base stays at the previous promotion and
  already-promoted changes reappear — the inflated diff this fix exists to
  prevent. Now a stated rule in the handbook, with merging `main` back into
  `dev` documented as the repair.

### Validation

The replay was verified against the real repositories before shipping:

- Replaying the private commits behind PR #157 onto the pre-#157 public `main`
  produced a tree **identical** to what filter-repo had published
  (`31ce935e9a4a…`), with the diff against `main` being exactly the true
  **103 files / +10298 / −245**.
- `--full` over the entire private history produced that **same tree**, in 177s.
- Re-running the replay is idempotent, and its output is deterministic.

### Files changed

| File | Change |
| --- | --- |
| `.github/scripts/publish_mirror.py` | New — the whole mirror builder |
| `.github/workflows/publish-to-public.yml` | Rewritten; 19 steps → 11, filter-repo and four inline sed/python rewrite steps removed |
| `.publish-config.yml` | Entry semantics point at the script; note that edits are now cheap; script added to `exclude_paths` |
| `tests/unit/test_publish_mirror_append_only.py` | New — BFR-036 regression suite |
| `tests/unit/test_publish_config_exclusion_globs.py` | Now calls the production matcher instead of carrying a copy |
| `docs/handbook/{en,zh}/developer/publish-to-public.md` | Rewritten for append-only |

## Side Effects

- **The public mirror's history shape changes going forward.** Commits are
  replayed first-parent, one public commit per private commit. Merge commits in
  the private history are flattened rather than reproduced.
- **`--full` is destructive by design.** It replaces the published history, so
  anything forked from the old history (including the public `main`) loses its
  common ancestor. This is the cost of a retroactive scrub and is now an
  explicit, opt-in workflow input rather than the default behaviour.
- **A pre-append-only mirror carries no cursor.** The first publish after this
  change needs `bootstrap_private_commit` set to the private commit the mirror
  tip corresponds to. The script refuses to guess.
- **Trailing pruned commits are re-scanned each run.** Harmless (milliseconds
  per commit, nothing published), and it keeps the trailer honest.
- No change to what is published: the scrub produces the same trees as before.

## Follow-Up

- [x] Replace the full-history rewrite with append-only replay
- [x] Stop force-pushing the public repo on the normal path
- [x] Preserve retroactive scrub as an explicit `full_remirror` input
- [x] Regression tests for the re-keying invariant and the `.gitignore` drop
- [x] Update the EN/ZH publish handbook
- [ ] First publish after this lands must pass `bootstrap_private_commit`
      (`b60de9f1e9e0e25d0ee0ff42fd3026177885d57a`, the private commit public
      `dev` currently corresponds to), dispatched from `main`
- [ ] Decide what to do about the two stale paths the bootstrap gate reports on
      the live mirror. `docs/design/BFR-023-Concurrent-Run-Cross-Session-Commit/`
      exists on public `main` under the number this BFR family used before it
      was renumbered to BFR-035; `main` was never force-pushed, so the old copy
      survived and PR #157 merged it into `dev`. Publishing removes it, which is
      correct — confirm, then pass `allow_public_drift` once
- [ ] Consider protecting the public `dev` branch against direct pushes, so the
      fast-forward guarantee cannot be broken from the other side
