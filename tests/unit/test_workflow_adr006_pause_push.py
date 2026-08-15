"""Contract tests for the ADR-006 pause push (BFR-035).

The pause gate reads ``pipeline_paused_until`` out of the *checked-out*
``.publish-config.yml``, so a pause only engages if the marker reaches
the branch. Ad-hoc ingestion runs deliberately carry no ``concurrency:``
group, so the pause commit races the auto-commits of overlapping runs:
on 2026-07-26 run 30195386608 lost that race, the bare
``git push || echo "::warning::..."`` swallowed the rejection, and the
24h pause silently never happened while the run stayed green.

These tests pin the two halves of the repair:

1. the push retries after rebasing onto the remote, and afterwards reads
   the branch back to confirm a pause is actually in effect — a green
   ``git push`` is not proof when the commit may have been a no-op. The
   read-back must be time-aware: an expired leftover marker pauses
   nothing (so a bare ``grep`` is too weak), while a *concurrent* run's
   future marker pauses us just as well as our own (so matching our own
   exact line is too strict — when Daily and AdHoc alert together the
   loser's rebase conflicts on those very lines and aborts);
2. the step is fatal, not annotated. ``continue-on-error`` or a bare
   ``|| echo ::warning::`` on the enforcement path reinstates exactly the
   silent-unpause failure the mechanism exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

INGESTION_WORKFLOWS = ("DailyIngestion.yml", "AdHocIngestion.yml")


def _pause_step(workflow_name: str) -> dict:
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / workflow_name).read_text(encoding="utf-8")
    )
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "ADR-006" in str(step.get("name", ""))
        and "pause" in str(step.get("name", "")).lower()
        and "run" in step
        and "apps.cli.db.pending_alert" in step["run"]
    ]
    assert len(steps) == 1, (
        f"{workflow_name}: expected exactly one ADR-006 alert+pause step, "
        f"found {len(steps)}"
    )
    return steps[0]


@pytest.mark.parametrize("workflow_name", INGESTION_WORKFLOWS)
def test_pause_push_retries_and_verifies_the_marker_landed(workflow_name):
    run = _pause_step(workflow_name)["run"]

    assert "git pull --rebase" in run, (
        f"{workflow_name}: the pause push must rebase onto the remote and "
        "retry — a bare push loses the race against a concurrent run's "
        "auto-commit (BFR-035)"
    )
    assert 'git show "origin/$GITHUB_REF_NAME:.publish-config.yml"' in run, (
        f"{workflow_name}: the pause step must read back "
        ".publish-config.yml from the remote branch to confirm a pause is "
        "in effect; push exit status alone does not prove it"
    )
    assert "datetime.datetime.now" in run, (
        f"{workflow_name}: the read-back must compare "
        "pipeline_paused_until against now — a bare grep passes on an "
        "expired leftover marker, which pauses nothing"
    )
    assert "exit 1" in run, (
        f"{workflow_name}: a branch with no future marker must fail the "
        "step, not warn"
    )


@pytest.mark.parametrize("workflow_name", INGESTION_WORKFLOWS)
def test_pause_step_is_fatal_not_annotated(workflow_name):
    step = _pause_step(workflow_name)

    assert not step.get("continue-on-error"), (
        f"{workflow_name}: continue-on-error keeps the run green when the "
        "pause fails to engage — the exact BFR-035 failure mode"
    )
    assert "git push || echo" not in step["run"], (
        f"{workflow_name}: `git push || echo ::warning::` downgrades a "
        "failed pause to an annotation nobody reads"
    )
