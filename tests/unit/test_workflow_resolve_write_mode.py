"""Regression tests for two workflow setup steps:

1. ``resolve_write_mode`` — selects ``JAVDB_HISTORY_WRITE_MODE`` for the
   run. Per ADR-006 this step no longer reads ``.publish-config.yml`` —
   the audit fallback is replaced by a hard pause. The remaining
   precedence is: ``workflow_dispatch`` override → default ``pending``.

2. ``pause_gate`` (new in ADR-006) — reads ``pipeline_paused_until``
   from ``.publish-config.yml``. If the timestamp is in the future the
   step records ``paused=true`` so every downstream job gates on
   ``needs.setup.outputs.paused != 'true'`` and skips. This is the
   replacement for the legacy ``pending_mode_disabled_until`` flag.

The pause-gate test specifically guards against the 2026-05
``IndentationError`` regression: YAML's literal-block indent stripping
silently broke the previous ``python3 -c "<multi-line>"`` form. The
heredoc ``python3 - <<'PY'`` is the fix and must not be reverted.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    REPO_ROOT / ".github" / "workflows" / "DailyIngestion.yml",
    REPO_ROOT / ".github" / "workflows" / "AdHocIngestion.yml",
)


def _extract_run(workflow_path: Path, step_id: str) -> str:
    """Return the parsed ``run:`` body of the named step."""
    with workflow_path.open() as f:
        data = yaml.safe_load(f)
    for step in data["jobs"]["setup"]["steps"]:
        if step.get("id") == step_id:
            return step["run"]
    raise AssertionError(
        f"step id={step_id!r} not found in {workflow_path.name}"
    )


def _inject_override(run_script: str, value: str) -> str:
    return run_script.replace("${{ inputs.write_mode_override }}", value)


def _run(script: str, cwd: Path) -> tuple[str, str, int, dict[str, str]]:
    """Run *script* under bash in *cwd* and capture stdout/stderr/rc/outputs."""
    gh_output = cwd / "gh_output"
    gh_output.write_text("")
    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(gh_output)
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    outputs: dict[str, str] = {}
    for line in gh_output.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            outputs[k] = v
    return proc.stdout, proc.stderr, proc.returncode, outputs


def _future_iso(hours: int = 1) -> str:
    return (
        dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(hours=hours)
    ).isoformat()


def _past_iso(hours: int = 1) -> str:
    return (
        dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=hours)
    ).isoformat()


# ──────────────────────────────────────────────────────────────────────
# resolve_write_mode: trivial after ADR-006 — workflow_dispatch override
# or default 'pending'. .publish-config.yml is no longer consulted here.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_resolve_write_mode_honours_override(workflow, tmp_path):
    base = _extract_run(workflow, "resolve_write_mode")
    script = _inject_override(base, "pending")
    _, stderr, rc, outputs = _run(script, tmp_path)
    assert rc == 0, f"script exited {rc}; stderr={stderr!r}"
    assert outputs.get("mode") == "pending"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_resolve_write_mode_defaults_to_pending(workflow, tmp_path):
    """Empty override → mode=pending. Even if a .publish-config.yml with
    the legacy ``pending_mode_disabled_until`` exists, this step no
    longer reads it (the pause gate handles pauses now)."""
    base = _extract_run(workflow, "resolve_write_mode")
    # Drop a legacy marker to prove resolve_write_mode ignores it.
    (tmp_path / ".publish-config.yml").write_text(
        f"pending_mode_disabled_until: '{_future_iso()}'\n"
    )
    script = _inject_override(base, "")
    _, stderr, rc, outputs = _run(script, tmp_path)
    assert rc == 0, f"script exited {rc}; stderr={stderr!r}"
    assert outputs.get("mode") == "pending", (
        "resolve_write_mode must not consult .publish-config.yml after ADR-006"
    )


# ──────────────────────────────────────────────────────────────────────
# pause_gate: the new ADR-006 step. Reads pipeline_paused_until from
# .publish-config.yml and emits paused=true|false.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_pause_gate_handles_all_branches(workflow, tmp_path):
    """Every state of .publish-config.yml maps to the documented paused value."""
    base = _extract_run(workflow, "pause_gate")

    future_iso = _future_iso()
    past_iso = _past_iso()
    case_configs: dict[str, str | None] = {
        "no_config_file":           None,
        "config_no_marker":         "exclude_paths: []\n",
        "future_marker_unquoted":   f"pipeline_paused_until: {future_iso}\n",
        "future_marker_quoted":     f"pipeline_paused_until: '{future_iso}'\n",
        "past_marker_quoted":       f"pipeline_paused_until: '{past_iso}'\n",
        "invalid_marker":           "pipeline_paused_until: 'not-a-date'\n",
        "legacy_marker_ignored":    f"pending_mode_disabled_until: '{future_iso}'\n",
    }
    case_expected = {
        "no_config_file":         "false",
        "config_no_marker":       "false",
        "future_marker_unquoted": "true",
        "future_marker_quoted":   "true",
        "past_marker_quoted":     "false",
        "invalid_marker":         "false",
        # ADR-006 explicitly switched from pending_mode_disabled_until to
        # pipeline_paused_until — the legacy key must be silently ignored
        # so a stale .publish-config.yml entry can't pause the pipeline
        # forever post-rollout.
        "legacy_marker_ignored":  "false",
    }

    failures = []
    for label, config_yaml in case_configs.items():
        case_dir = tmp_path / label
        case_dir.mkdir()
        if config_yaml is not None:
            (case_dir / ".publish-config.yml").write_text(config_yaml)
        stdout, stderr, rc, outputs = _run(base, case_dir)
        expected = case_expected[label]
        if rc != 0 or outputs.get("paused") != expected:
            failures.append(
                f"{label}: rc={rc} paused={outputs.get('paused')!r} "
                f"expected={expected!r}\n"
                f"  stdout={stdout!r}\n  stderr={stderr!r}"
            )
    assert not failures, (
        f"pause_gate regressed in {workflow.name}:\n"
        + "\n".join(failures)
    )


def test_pause_gate_no_indentation_error(tmp_path):
    """Pause-gate inherits the heredoc form from the retired
    ``resolve_write_mode`` reader; if a future refactor reverts to
    inline ``python3 -c "..."``, YAML's literal-block indent stripping
    will silently turn this into an ``IndentationError`` and the gate
    will permanently miss future pauses. Lock in the heredoc form."""
    base = _extract_run(WORKFLOWS[0], "pause_gate")
    case_dir = tmp_path / "indent_check"
    case_dir.mkdir()
    (case_dir / ".publish-config.yml").write_text(
        f"pipeline_paused_until: '{_future_iso()}'\n"
    )
    _, stderr, rc, outputs = _run(base, case_dir)
    assert "IndentationError" not in stderr, (
        f"Python heredoc regression: {stderr}"
    )
    assert rc == 0, f"script exited {rc}; stderr={stderr!r}"
    assert outputs.get("paused") == "true", (
        f"future marker not honoured (paused={outputs.get('paused')!r}); "
        "pause gate broken"
    )


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_pause_gate_uses_heredoc_form(workflow):
    """The pause gate must use ``python3 - <<'PY'`` (heredoc) or invoke
    a separate script. Inline ``python3 -c`` with a multi-line literal
    is the 2026-05 regression mode and is banned."""
    body = _extract_run(workflow, "pause_gate")
    if "python3 -c" in body and "<<" not in body:
        pytest.fail(
            f"{workflow.name}: pause_gate uses inline `python3 -c` — "
            f"this re-introduces the YAML literal-block indent bug "
            f"(see ADR-006 §D3 history). Use `python3 - <<'PY'` heredoc."
        )


def test_bash_available_for_runs():
    """Skip the harness with a clear message when bash is missing."""
    if shutil.which("bash") is None:
        pytest.skip("bash not on PATH; cannot run workflow run scripts")
