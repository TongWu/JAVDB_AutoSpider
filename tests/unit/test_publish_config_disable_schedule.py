"""Regression guard: every scheduled workflow must be disabled on the public mirror.

BFR: PurgeMissingFiles.yml and SubscriptionMonitor.yml shipped to the public
mirror with a live `schedule:` trigger and no `disable_schedule` entry in
`.publish-config.yml`. Their self-hosted runner requirement means the cron
fires on the public repo daily and just fails/queues forever, polluting its
Actions run history with runs no self-hoster asked for.

This pins the general invariant so a future cron-based workflow can't repeat
the mistake: any workflow file with an active `schedule:` trigger must be
listed in `workflow_modifications.disable_schedule` (or otherwise fully
disabled via `disable_all_triggers` / removed via `exclude_paths`) so
`publish-to-public.yml` strips the cron before it ever reaches the public
repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-to-public.yml"
PUBLISH_CONFIG = REPO_ROOT / ".publish-config.yml"

# Both files are themselves in exclude_paths, so neither exists on the public
# mirror. See test_workflow_public_runner_markers.py for the same guard.
IS_PUBLIC_MIRROR = not PUBLISH_WORKFLOW.exists()

pytestmark = pytest.mark.skipif(
    IS_PUBLIC_MIRROR,
    reason="publish-to-public.yml and .publish-config.yml are stripped from the mirror",
)


def _publish_config() -> dict:
    return yaml.safe_load(PUBLISH_CONFIG.read_text(encoding="utf-8"))


def _has_active_schedule_trigger(workflow_path: Path) -> bool:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    # PyYAML resolves the bare `on:` mapping key as the boolean True (YAML 1.1
    # scalar resolution applies to keys too), not the string "on".
    on_block = workflow.get(True) or workflow.get("on") or {}
    if not isinstance(on_block, dict):
        return False
    return "schedule" in on_block


def test_every_scheduled_workflow_is_disabled_for_public_mirror():
    config = _publish_config()
    mods = config["workflow_modifications"]
    handled = (
        set(mods.get("disable_schedule", []))
        | set(mods.get("disable_all_triggers", []))
        | set(config.get("exclude_paths", []))
    )

    unhandled = [
        f".github/workflows/{path.name}"
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
        if _has_active_schedule_trigger(path)
        and f".github/workflows/{path.name}" not in handled
    ]

    assert not unhandled, (
        "workflow(s) with a live schedule: trigger are not disabled for the "
        f"public mirror: {unhandled}. Add them to "
        "workflow_modifications.disable_schedule in .publish-config.yml."
    )
