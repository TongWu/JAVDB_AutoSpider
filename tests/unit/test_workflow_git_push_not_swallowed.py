"""Contract tests: no workflow discards a git push/pull/rebase failure.

BFR-033 fixed one silent-loss shape in the ingestion push ladder
(``git rebase -X ours`` resolving conflicts to the *other* run's side).
Its Follow-Up flagged a second, different shape in the other writers of
the same shared ``reports/`` files:

    git pull --rebase origin "$CURRENT_BRANCH" || true
    if git push origin "$CURRENT_BRANCH"; then ...

``|| true`` does not resolve a conflicted rebase, it *hides* it. The tree
is left mid-conflict, so the push is rejected, every later attempt of the
retry loop dies on "there is already a rebase-merge directory", the whole
retry budget burns, and the only trace is a confusing error nobody
annotated. Fixed in ``Migration.yml``, ``WeeklyDedup.yml`` and
``RcloneManager.yml``; the six pre-run "Pull latest changes" steps carried
the same swallow and were made loud too.

These tests pin both halves:

1. **The sweep.** No git ``push`` / ``pull`` / ``rebase`` / ``merge``
   anywhere under ``.github/`` may have its failure thrown away by
   ``|| true`` (or ``|| :``). The single legitimate carve-out is the
   cleanup tail — a bare ``git rebase --abort || true``, or
   ``git pull --rebase … || git rebase --abort || true``: there the
   swallow guards only the *cleanup*, whose failure means "there was no
   rebase in progress", and the failure that triggered it came from the
   rebase the abort undoes, so nothing is discarded. The carve-out is
   scoped to that pairing: ``git push … || git rebase --abort || true``
   aborts a rebase the push never started and is a real swallow.
   Non-git ``|| true`` (``git add``,
   ``git commit``, ``git config --unset``) stays legal; those are not the
   commands whose failure loses a run's results.

2. **The repair, not just the removal.** Deleting ``|| true`` alone would
   turn a swallowed conflict into a *fatal* one while still leaving the
   tree mid-conflict. Each of the three auto-commit retry loops must
   guard the rebase with an ``if``, ``git rebase --abort`` on failure so
   the next attempt starts clean, annotate it, and still fall through to
   ``fallback_push.sh`` when the retry budget is exhausted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"

# Auto-commit steps whose retry loop reconciles with the remote before pushing.
# Deliberately excludes DailyIngestion / AdHocIngestion: their ladder is the
# four-rung escalation hardened by BFR-033 (4cc7569a), a different shape with
# its own contract test (``test_workflow_push_conflict_strategy.py``).
RETRY_LOOP_WORKFLOWS = ("Migration.yml", "WeeklyDedup.yml", "RcloneManager.yml")

# Failure of these loses a run's results, or leaves the tree mid-conflict.
# ``(?![-\w])`` rather than ``\b`` so read-only ``git merge-base`` is not
# mistaken for ``git merge``.
RISKY_GIT = re.compile(r"\bgit\s+(?:-\S+\s+)*(?:push|pull|rebase|merge)(?![-\w])")

# Cleanup whose own failure only means "nothing was in progress".
CLEANUP_GIT = re.compile(r"\bgit\s+(?:rebase|merge|cherry-pick|am)\s+--abort\b")

# The only commands whose failure a trailing ``git rebase --abort`` is actually
# cleaning up after: they are what can leave a rebase half-applied. Anything
# else in that slot (notably ``git push``) means the abort is unrelated and the
# real failure is being discarded.
REBASE_STARTER = re.compile(
    r"\bgit\s+(?:-\S+\s+)*(?:pull\s+(?:-\S+\s+)*--rebase|rebase)(?![-\w])"
)

# A trailing ``|| true`` / ``|| :`` discards the exit status of everything
# before it on the line.
SWALLOW_TAIL = re.compile(r"\|\|\s*(?:true|:)\s*;?\s*$")


def _shell_lines(script: str) -> list[str]:
    """Logical shell lines: comments dropped, continuations joined."""
    logical: list[str] = []
    pending = ""
    for raw in script.splitlines():
        line = raw.strip()
        if not pending and line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        logical.append((pending + line).strip())
        pending = ""
    if pending:
        logical.append(pending.strip())
    return [line for line in logical if line]


def _swallowed_git_failures(script: str) -> list[str]:
    """Lines where a risky git command's failure is discarded by ``|| true``."""
    offenders = []
    for line in _shell_lines(script):
        if not SWALLOW_TAIL.search(line):
            continue
        segments = [seg.strip() for seg in line.split("||")]
        # segments[-1] is the `true` / `:` itself.
        discarded = segments[:-1]
        if not discarded:
            continue
        # `git rebase --abort || true` on its own, and
        # `git pull --rebase ... || git rebase --abort || true`: the swallow
        # covers only the cleanup, whose failure means "there was no rebase in
        # progress", and the risky command's failure is what invoked it.
        # The exemption does NOT extend to an unrelated command in that slot:
        # `git push ... || git rebase --abort || true` aborts a rebase the push
        # never started, and throws the rejected push away.
        if CLEANUP_GIT.search(discarded[-1]) and (
            len(discarded) == 1 or REBASE_STARTER.search(discarded[-2])
        ):
            continue
        if any(RISKY_GIT.search(seg) for seg in discarded):
            offenders.append(line)
    return offenders


def _shell_sources() -> list[tuple[str, str]]:
    """(label, script) for every workflow ``run:`` block and .github script."""
    sources: list[tuple[str, str]] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                label = f"{path.name}:{job_name}:{step.get('name', '<unnamed>')}"
                sources.append((label, run))
    for path in sorted(SCRIPTS_DIR.glob("*.sh")):
        sources.append((f".github/scripts/{path.name}", path.read_text(encoding="utf-8")))
    return sources


def test_shell_sources_were_found():
    """Guard the sweep itself: a broken loader must not pass vacuously."""
    sources = _shell_sources()
    assert len(sources) > 50, f"expected the full workflow corpus, got {len(sources)}"
    assert any("fallback_push.sh" in script for _, script in sources)


def test_no_git_push_pull_rebase_failure_is_swallowed():
    offenders = [
        f"{label}: {line}"
        for label, script in _shell_sources()
        for line in _swallowed_git_failures(script)
    ]
    assert not offenders, (
        "`|| true` on a git push/pull/rebase/merge throws the failure away: a "
        "conflicted rebase is left mid-conflict (so the push is rejected and "
        "every retry dies on 'already a rebase-merge directory') and a "
        "rejected push looks like a success. Guard it with `if`, abort the "
        "rebase, and annotate — see Migration.yml / WeeklyDedup.yml / "
        "RcloneManager.yml. Offending lines:\n  " + "\n  ".join(offenders)
    )


def test_swallow_detector_recognises_the_bfr033_shapes():
    """The detector must catch the real regressions and allow the real idioms."""
    caught = _swallowed_git_failures(
        'git pull --rebase origin "$B" || true\n'
        'git pull --rebase origin "$B" || git pull --no-rebase --no-edit origin "$B" || true\n'
        'git push origin "$B" || true\n'
        # The cleanup-tail carve-out must not stretch to cover an unrelated
        # command: this abort cleans up no rebase, and the rejected push is
        # thrown away.
        'git push origin "$B" || git rebase --abort || true\n'
    )
    assert len(caught) == 4, caught

    allowed = _swallowed_git_failures(
        # cleanup-tail idiom: the swallow guards only the abort
        'git pull --rebase --autostash origin "$R" || git rebase --abort || true\n'
        "git rebase --abort 2>/dev/null || true\n"
        "git merge --abort 2>/dev/null || true\n"
        # not push/pull/rebase — losing these does not lose a run's results
        'git add "$F" 2>/dev/null || true\n'
        'git commit -m "x" || true\n'
        "git config --unset-all http.https://github.com/.extraheader || true\n"
        # read-only, and must not be confused with `git merge`
        'MERGE_BASE=$(git merge-base HEAD "origin/$B" 2>/dev/null || echo "")\n'
        # guarded, not swallowed
        'if git push origin "$B"; then echo ok; fi\n'
    )
    assert allowed == [], allowed

    # A comment quoting the anti-pattern must not trip the sweep.
    assert _swallowed_git_failures('# git pull --rebase origin "$B" || true') == []


def _retry_loop_step(workflow_name: str) -> str:
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / workflow_name).read_text(encoding="utf-8")
    )
    steps = [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps") or []
        if "run" in step
        and "fallback_push.sh" in step["run"]
        and "git pull --rebase" in step["run"]
    ]
    assert len(steps) == 1, (
        f"{workflow_name}: expected exactly one auto-commit retry-loop step "
        f"(git pull --rebase + fallback_push.sh), found {len(steps)}"
    )
    return steps[0]


@pytest.mark.parametrize("workflow_name", RETRY_LOOP_WORKFLOWS)
def test_retry_loop_rebase_is_guarded_and_aborted(workflow_name):
    lines = _shell_lines(_retry_loop_step(workflow_name))

    pulls = [line for line in lines if "git pull --rebase" in line]
    assert len(pulls) == 1, f"{workflow_name}: expected one rebase rung, got {pulls}"
    assert pulls[0].startswith("if git pull --rebase"), (
        f"{workflow_name}: the rebase must be tested, not swallowed — "
        f"got {pulls[0]!r}"
    )

    assert any("git rebase --abort" in line for line in lines), (
        f"{workflow_name}: a failed rebase must be aborted, otherwise the tree "
        "stays mid-conflict and every remaining retry dies on 'there is "
        "already a rebase-merge directory'"
    )


@pytest.mark.parametrize("workflow_name", RETRY_LOOP_WORKFLOWS)
def test_retry_loop_rebase_conflict_is_annotated(workflow_name):
    run = _retry_loop_step(workflow_name)
    lines = _shell_lines(run)

    abort_index = next(
        index for index, line in enumerate(lines) if "git rebase --abort" in line
    )
    # The annotation sits in the same failure branch as the abort.
    branch = lines[max(0, abort_index - 3) : abort_index + 1]
    assert any("::warning::" in line for line in branch), (
        f"{workflow_name}: a conflicted rebase on the shared report files must "
        "emit a ::warning:: — the append-only files are merge=union, so a "
        "genuine conflict here is unexpected and must not be silent"
    )


@pytest.mark.parametrize("workflow_name", RETRY_LOOP_WORKFLOWS)
def test_retry_loop_still_preserves_results_on_exhaustion(workflow_name):
    run = _retry_loop_step(workflow_name)

    assert "fallback_push.sh" in run, (
        f"{workflow_name}: when the retry budget is exhausted the commit must "
        "still be preserved on a fallback branch — these steps carry the only "
        "record of D1 drift / queued replays"
    )
    assert '"$LOCAL_COMMIT"' in run, (
        f"{workflow_name}: fallback_push.sh must be handed the preserved "
        "commit SHA, or an aborted rebase's rewind loses the payload"
    )
