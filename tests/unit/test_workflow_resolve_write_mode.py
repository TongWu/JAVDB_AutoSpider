"""Regression test for the `Resolve effective WriteMode` workflow step.

Phase 3 (Ingestion Perfect Rollback) auto-fallback contract: when a
critical pending-mode alert fires, the email job writes
``pending_mode_disabled_until: <ISO>`` into ``.publish-config.yml`` and
pushes; the **next** ingestion run reads that key in this step and forces
``JAVDB_HISTORY_WRITE_MODE=audit`` for 24h.  The original implementation
inlined the parsing as ``python3 -c "<multi-line>"`` which silently
failed with ``IndentationError`` after YAML stripped the literal-block
indent unevenly — leaving the auto-fallback marker permanently ignored
and breaking the whole emergency rollback path.

This test parses the actual workflow YAML, extracts the step's `run`
script verbatim, and runs it under bash against six fabricated
``.publish-config.yml`` states.  A future regression that re-introduces
the indentation bug — or breaks any of the six branches — fails here
before reaching CI.
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


def _extract_run(workflow_path: Path) -> str:
    """Return the parsed ``run:`` body of the resolve_write_mode step."""
    with workflow_path.open() as f:
        data = yaml.safe_load(f)
    for step in data["jobs"]["setup"]["steps"]:
        if step.get("id") == "resolve_write_mode":
            return step["run"]
    raise AssertionError(
        f"resolve_write_mode step not found in {workflow_path.name}"
    )


def _strip_dispatch_template(run_script: str) -> str:
    """Replace the ``${{ inputs.write_mode_override }}`` template with empty.

    The workflow_dispatch override is exercised by a separate case below;
    every other case must see an empty override so the script falls
    through to the .publish-config.yml branch.
    """
    return run_script.replace("${{ inputs.write_mode_override }}", "")


def _inject_override(run_script: str, value: str) -> str:
    return run_script.replace("${{ inputs.write_mode_override }}", value)


def _run(script: str, cwd: Path) -> tuple[str, str, int, str]:
    """Run *script* under bash in *cwd* and capture (stdout, stderr, rc, mode)."""
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
    mode = ""
    for line in gh_output.read_text().splitlines():
        if line.startswith("mode="):
            mode = line.split("=", 1)[1]
    return proc.stdout, proc.stderr, proc.returncode, mode


def _future_iso(hours: int = 1) -> str:
    return (
        dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(hours=hours)
    ).isoformat()


def _past_iso(hours: int = 1) -> str:
    return (
        dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=hours)
    ).isoformat()


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_resolve_write_mode_handles_all_branches(workflow, tmp_path):
    """Every state of .publish-config.yml maps to the documented mode."""
    base = _extract_run(workflow)

    future_iso = _future_iso()
    past_iso = _past_iso()
    case_configs = {
        "no_config_file":             (None, ""),
        "config_no_marker":           ("exclude_paths: []\n", ""),
        "future_marker_unquoted":     (f"pending_mode_disabled_until: {future_iso}\n", ""),
        "future_marker_quoted":       (f"pending_mode_disabled_until: '{future_iso}'\n", ""),
        "past_marker_quoted":         (f"pending_mode_disabled_until: '{past_iso}'\n", ""),
        "invalid_marker":             ("pending_mode_disabled_until: 'not-a-date'\n", ""),
        "dispatch_override_audit":    ("pending_mode_disabled_until: '2026-01-01T00:00:00+00:00'\n", "audit"),
        "dispatch_override_pending":  (f"pending_mode_disabled_until: '{future_iso}'\n", "pending"),
    }
    case_expected = {
        "no_config_file":             "pending",
        "config_no_marker":           "pending",
        "future_marker_unquoted":     "audit",
        "future_marker_quoted":       "audit",
        "past_marker_quoted":         "pending",
        "invalid_marker":             "pending",
        "dispatch_override_audit":    "audit",
        "dispatch_override_pending":  "pending",
    }

    failures = []
    for label, (config_yaml, override) in case_configs.items():
        case_dir = tmp_path / label
        case_dir.mkdir()
        if config_yaml is not None:
            (case_dir / ".publish-config.yml").write_text(config_yaml)
        script = _inject_override(base, override)
        stdout, stderr, rc, mode = _run(script, case_dir)
        expected = case_expected[label]
        if rc != 0 or mode != expected:
            failures.append(
                f"{label}: rc={rc} mode={mode!r} expected={expected!r}\n"
                f"  stdout={stdout!r}\n  stderr={stderr!r}"
            )
    assert not failures, (
        f"resolve_write_mode regressed in {workflow.name}:\n"
        + "\n".join(failures)
    )


def test_resolve_write_mode_no_indentation_error(tmp_path):
    """Smoke test that catches the 2026-05 IndentationError specifically.

    Even with the marker in place, the original `python3 -c` form printed
    `IndentationError: unexpected indent` to stderr and exited 1.  The
    fixed heredoc form must never emit IndentationError.
    """
    base = _extract_run(WORKFLOWS[0])
    script = _strip_dispatch_template(base)
    case_dir = tmp_path / "indent_check"
    case_dir.mkdir()
    (case_dir / ".publish-config.yml").write_text(
        f"pending_mode_disabled_until: '{_future_iso()}'\n"
    )
    _, stderr, rc, mode = _run(script, case_dir)
    assert "IndentationError" not in stderr, (
        f"Python heredoc regression: {stderr}"
    )
    assert rc == 0, f"script exited {rc}; stderr={stderr!r}"
    assert mode == "audit", (
        f"future marker not honoured (mode={mode!r}); auto-fallback broken"
    )


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_resolve_write_mode_uses_heredoc_form(workflow):
    """Lock in the heredoc form so a future refactor doesn't reintroduce
    the inline `python3 -c "..."` bug.  Either heredoc or a separate
    script invocation is OK; inline `python3 -c` with a multi-line
    string literal is NOT.
    """
    body = _extract_run(workflow)
    if "python3 -c" in body and "<<" not in body:
        pytest.fail(
            f"{workflow.name}: resolve_write_mode reverted to inline "
            f"`python3 -c` — this re-introduces the IndentationError "
            f"bug (R1).  Use `python3 - <<'PY'` heredoc or extract to "
            f"a separate script."
        )


def test_bash_available_for_runs():
    """Skip the harness with a clear message when bash is missing."""
    if shutil.which("bash") is None:
        pytest.skip("bash not on PATH; cannot run workflow run scripts")
