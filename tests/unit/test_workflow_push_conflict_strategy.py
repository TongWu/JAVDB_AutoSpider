"""Contract tests for the auto-commit push conflict escalation ladder.

``DailyIngestion.yml`` / ``AdHocIngestion.yml`` end with a "Commit and
Push Results" step that escalates through
``rebase`` → ``merge`` → strategy-forced ``rebase`` → ``fallback_push.sh``
when the branch moved under the run. The strategy-forced rung used to be
``git rebase "origin/$CURRENT_BRANCH" -X ours 2>/dev/null``, which is
backwards: in *rebase* the sides are swapped relative to *merge* —
``ours`` is the upstream being replayed **onto** (the other run's
commit), ``theirs`` is the commit being replayed (this run's). So
``-X ours`` resolved every conflicting hunk to the *other* run's version,
discarded this run's ``reports/D1/d1_recovery_outbox.jsonl`` /
``d1_drift.jsonl`` / dated report CSV rows, exited 0, and printed a
success banner. AdHoc ingestion deliberately carries no ``concurrency:``
group, so parallel dispatches reach this rung — on 2026-07-26 they did,
and the loss was silent.

These tests pin the three halves of the repair:

1. the forced rung uses ``-X theirs`` (matching the stated intent of
   "keeping local changes") and ``-X ours`` never returns as an executed
   command;
2. the forced rebase is not silenced with ``2>/dev/null`` — conflict
   output has to reach the run log to be diagnosable;
3. a successful forced resolution emits a ``::warning::`` annotation.
   Overwriting a concurrent run's hunks may be the least-bad option, but
   it must never be silent — a green step with a "successful" banner is
   exactly what hid the 2026-07-26 loss.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

INGESTION_WORKFLOWS = ("DailyIngestion.yml", "AdHocIngestion.yml")


def _commit_push_run(workflow_name: str) -> str:
    """Return the run script of the auto-commit push step."""
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / workflow_name).read_text(encoding="utf-8")
    )
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
        and "fallback_push.sh" in step["run"]
        and "git rebase" in step["run"]
    ]
    assert len(steps) == 1, (
        f"{workflow_name}: expected exactly one auto-commit push step "
        f"(rebase ladder + fallback_push.sh), found {len(steps)}"
    )
    return steps[0]["run"]


def _executable_lines(run: str) -> list[str]:
    """Drop whole-line shell comments; keep the commands git actually runs."""
    return [line for line in run.splitlines() if not line.strip().startswith("#")]


def _forced_rebase_index(workflow_name: str, lines: list[str]) -> int:
    """Index of the single strategy-forced rebase rung."""
    indexes = [
        index
        for index, line in enumerate(lines)
        if "git rebase" in line and "-X theirs" in line
    ]
    assert len(indexes) == 1, (
        f"{workflow_name}: expected exactly one strategy-forced "
        f"`git rebase ... -X theirs` rung in the push ladder, found "
        f"{len(indexes)}"
    )
    return indexes[0]


@pytest.mark.parametrize("workflow_name", INGESTION_WORKFLOWS)
def test_forced_rebase_keeps_this_runs_hunks(workflow_name):
    lines = _executable_lines(_commit_push_run(workflow_name))

    assert not [line for line in lines if "-X ours" in line], (
        f"{workflow_name}: `-X ours` on a *rebase* resolves conflicts to the "
        "upstream being replayed onto — i.e. the other run's version — so it "
        "silently discards this run's report rows while exiting 0. Use "
        "`-X theirs` to keep this run's changes"
    )

    _forced_rebase_index(workflow_name, lines)


@pytest.mark.parametrize("workflow_name", INGESTION_WORKFLOWS)
def test_forced_rebase_output_is_not_silenced(workflow_name):
    lines = _executable_lines(_commit_push_run(workflow_name))
    forced = lines[_forced_rebase_index(workflow_name, lines)]

    assert "2>/dev/null" not in forced, (
        f"{workflow_name}: the forced rebase must not redirect stderr to "
        "/dev/null — conflict output is the only signal that files were "
        "auto-resolved"
    )


@pytest.mark.parametrize("workflow_name", INGESTION_WORKFLOWS)
def test_forced_rebase_success_is_annotated(workflow_name):
    lines = _executable_lines(_commit_push_run(workflow_name))
    forced_index = _forced_rebase_index(workflow_name, lines)

    # The success branch runs until the matching `else` of the forced rebase.
    success_branch = []
    for line in lines[forced_index + 1 :]:
        if line.strip() == "else":
            break
        success_branch.append(line)

    assert any("::warning::" in line for line in success_branch), (
        f"{workflow_name}: a successful `-X theirs` rebase overwrites a "
        "concurrent run's conflicting hunks — it must emit a ::warning:: "
        "annotation, not just a success banner (the 2026-07-26 silent loss)"
    )
