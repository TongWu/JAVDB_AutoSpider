# Publish to Public Repository Guide

How code is synchronized from the private repository to the public mirror.

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Triggers and Inputs](#triggers-and-inputs)
- [Required Secrets](#required-secrets)
- [Promoting dev to main](#promoting-dev-to-main)
- [Full Remirror (destructive)](#full-remirror-destructive)
- [FAQ](#faq)

## Overview

```text
Private repo (main branch)
    ↓ (manual trigger)
GitHub Actions: .github/scripts/publish_mirror.py
    ↓ (scrub excluded paths, apply workflow modifications)
Private repo (public-sync branch)
    ↓ (fast-forward push)
Public repo (dev branch)
    ↓ (promotion PR)
Public repo (main branch)
```

The mirror is built **append-only**. Each private commit that is not on the
mirror yet is replayed as one public commit on top of the mirror's current tip.
Every publish is a fast-forward, and the public repo is never force-pushed on
the normal path.

## How It Works

1. **Resume from the sync cursor.** Every published commit carries a
   `Private-Commit: <sha>` trailer naming the private commit it was built from.
   The newest one reachable from the mirror tip tells the next run where to
   resume — there is no separate state file.

2. **Replay each new private commit.** For each one, in first-parent order:
   - load the commit's tree through the git index (never a working directory);
   - drop every path matching `exclude_paths`;
   - apply the configured workflow modifications;
   - commit the result on top of the previous public commit, preserving the
     original author and dates.

3. **Prune commits that are empty after the scrub.** An `Auto-commit` that only
   touched `reports/` produces no public commit.

4. **Verify before pushing.** Two gates, both fatal:
   - no path matching `exclude_paths` may appear in any new commit;
   - no path may go *missing* that the scrub did not ask to drop.

5. **Fast-forward push** to the public target branch. A rejected push fails the
   job — it means the mirror moved underneath us, and silently overwriting it is
   exactly what this design prevents.

> **Why append-only?** Publishing used to rewrite the entire history with
> `git filter-repo` and force-push. filter-repo is deterministic, but
> `exclude_paths` is one of its inputs — so editing that list re-keyed every
> commit and destroyed the merge base with the public `main`. See
> [BFR-036](../../../design/BFR-036-Mirror-Rewrite-Rekeys-History/BFR-036-mirror-rewrite-rekeys-history.md).

## Configuration

All publishing configuration lives in `.publish-config.yml`:

```yaml
# Files/directories to exclude from the public repo
exclude_paths:
  - "reports/"
  - "logs/"
  - "config.py"          # resolved secrets — never publish
  - "CLAUDE.md"
  - "AGENTS.md"
  - "CONTEXT.md"
  - "*.db"
  - ".github/workflows/publish-to-public.yml"
  - ".github/scripts/publish_mirror.py"
  - ".publish-config.yml"
  # ... see .publish-config.yml for the complete list

# Per-workflow modifications applied to the public mirror
workflow_modifications:
  disable_schedule:        # comment out `schedule:` triggers
    - ".github/workflows/DailyIngestion.yml"
  enable_push_trigger:     # uncomment the public push trigger
    - ".github/workflows/docker-publish-ghcr.yml"
  disable_push_trigger:    # comment out the PRIVATE_ONLY_PUSH block
    - ".github/workflows/TestIngestion.yml"
  disable_all_triggers: [] # comment out the whole `on:` block

branches:
  publish_branch: "public-sync"
  public_target_branch: "dev"
```

### Entry semantics

| Entry shape | Matching |
|---|---|
| No glob metacharacters (`*`, `?`, `[`) | Exact path, or any path beneath it if it names a directory |
| Contains glob metacharacters | `fnmatch`, where `*` also crosses `/` — so `*.env` matches `.env` at any depth |

A `**/`-prefixed glob requires a preceding `/`, so it never covers the
root-level case. Pair it with a literal entry (`secrets/` alongside
`**/secrets/`).

### Editing `exclude_paths` is cheap — but not retroactive

Adding an entry affects **commits published from now on**. It does *not* remove
the path from already-published history; that needs a
[full remirror](#full-remirror-destructive).

## Triggers and Inputs

Manual only: GitHub Actions → "Publish to Public Repository" → "Run workflow".

| Input | Purpose |
|---|---|
| `dry_run` | Build the mirror commits and run both gates, push nothing |
| `target_branch` | Override the public target branch (default from config) |
| `full_remirror` | **Destructive.** Rebuild all history and force push — see below |
| `bootstrap_private_commit` | The private commit the mirror tip corresponds to. Needed once, when the mirror carries no `Private-Commit` trailer yet |
| `allow_public_drift` | Publish over content the mirror branch has but this publish will not reproduce, overwriting it. Only after reviewing the paths the build step reported |

## Required Secrets

| Secret | Description |
|--------|-------------|
| `DEPLOY_KEY` | SSH key for accessing the private repo |
| `GIT_USERNAME` | Git username for public repo authentication |
| `GIT_PASSWORD` | PAT for the public repo. Needs workflow scope (classic: `repo` + `workflow`) because `GITHUB_TOKEN` cannot update `.github/workflows/**` |
| `GIT_REPO_URL_REMOTE` | Public repository URL (HTTPS format) |

## Promoting dev to main

The public `dev` branch receives every publish; `main` is promoted from it via a
pull request.

Because publishing is append-only, `dev` is always a descendant of the last
promoted state, so the promotion PR shows the real diff and merges cleanly.

> **Merge the promotion PR with a merge commit. Never squash, never rebase.**
> A merge commit makes the `dev` tip an ancestor of `main`, so the next
> promotion's merge base is the commit you just promoted and its diff contains
> only what was published since. Squash and rebase both give `main` fresh SHAs
> that `dev` does not contain, leaving the merge base stuck at the *previous*
> promotion — so already-promoted changes reappear in the next PR, which is the
> inflated diff this whole design exists to prevent.

Two more things break the guarantee — avoid both:

- **Committing directly to the public `dev`.** Each published commit's tree is a
  full snapshot of the filtered private commit, not a patch, so a direct commit
  would still fast-forward on the next publish while its content vanished from
  the tree. The build step refuses instead, naming every path that would be
  lost. Port the change into the private repo and publish it from there; only
  use `allow_public_drift` when you have reviewed the list and want the mirror
  to win.
- **Running a full remirror between promotions.** It replaces `dev`'s history,
  so `main` loses its common ancestor with it.

If a promotion does land as a squash or rebase, recover by merging `main` back
into `dev` (`sync-main-to-dev.yml`, manual dispatch) before the next promotion.
That restores a shared ancestor without rewriting anything.

## Full Remirror (destructive)

`full_remirror: true` rebuilds every commit from the root under today's
`exclude_paths` and **force pushes** the result.

**Use it only to retroactively scrub something out of already-published
history** — a path that should never have been published and is still visible in
old commits.

What it costs:

- The published history is replaced. Anything forked from it — including the
  public `main` — loses its common ancestor, so the next promotion PR is a
  whole-repository diff and needs manual conflict resolution.
- The rebuilt history is linear: it walks first-parent only, so private merge
  commits are flattened.

After a full remirror, reset the public `main` from the new `dev` rather than
raising a promotion PR against a history it no longer shares.

## FAQ

### Q: How do I add a new file/directory to exclude?

Add the path to `exclude_paths` in `.publish-config.yml`. It applies to the next
publish onward. To also remove it from published history, run a
[full remirror](#full-remirror-destructive).

### Q: The push to the public repo was rejected. What now?

The mirror branch is not a fast-forward of what was built — someone force-pushed
or committed directly to it. Reconcile by hand. **Do not force push** unless you
intend a full remirror and accept resetting `main` afterwards.

### Q: The job says there is no sync cursor. What do I pass?

The mirror was published before append-only mode and carries no
`Private-Commit` trailer. Find the private commit its tip corresponds to and
pass it as `bootstrap_private_commit`. The script refuses to guess, because
guessing wrong would silently drop or duplicate commits.

### Q: How do I make a self-hosted job work on the public repo?

The public repo has no access to the private self-hosted runner pool, so any job
with `runs-on: self-hosted` would hang there. Annotate the line inline:

```yaml
build-arm:
  runs-on: [self-hosted, ARM64]  # PUBLIC_RUNNER: ubuntu-24.04-arm
```

Every `runs-on:` line carrying this marker is rewritten to `runs-on: <name>`
across all `.github/workflows/*.yml`, so no entry in `.publish-config.yml` is
needed. Both the single-token form and the arch-pinned array form are supported;
the replacement is always a single token. Expression forms like
`${{ matrix.runner }}` carry no marker and are left untouched.

### Q: A workflow contract test passes here but fails on the public repo. Why?

The mirror is a *rewritten* tree, not a copy. Any test asserting on `.github/`
content can see a different tree than the one you committed:

- workflows in `exclude_paths` (e.g. `publish-to-public.yml`) are **absent**, so
  reading one raises `FileNotFoundError`;
- `# PUBLIC_RUNNER`-marked `runs-on:` lines are **already rewritten**.

Guard such assertions on whether this checkout is the mirror. The absence of
`publish-to-public.yml` is the marker:

```python
IS_PUBLIC_MIRROR = not (WORKFLOWS_DIR / "publish-to-public.yml").exists()

@pytest.mark.skipif(IS_PUBLIC_MIRROR, reason="rewritten on the public mirror")
def test_jobs_run_self_hosted():
    ...
```

See `tests/unit/test_workflow_public_runner_markers.py` for live usage.

### Q: How do I change the target branch?

Either edit `branches.public_target_branch` in `.publish-config.yml`, or pass
`target_branch` on the workflow dispatch for a one-off.

If the branch does not exist on the public repo yet, the build forks it from the
default branch, so it has a proper merge base and stays mergeable. It is created
by the push, not beforehand.

### Q: The workflow failed. How do I debug?

1. Check the run logs — the build step names every commit it replayed, skipped
   or rejected.
2. Re-run with `dry_run: true` to exercise both verification gates without
   pushing.
3. Verify all secrets are configured.
