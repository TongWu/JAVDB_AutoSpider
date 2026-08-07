"""Regression guard: the CF-bypass env block must be complete in every workflow.

``test_cf_bypass_via_proxy_wiring.py`` proves the Python side — every
production ``RequestConfig(...)`` passes ``cf_bypass_via_proxy``. But
``config.py`` on a runner is *generated* from ``VAR_*`` env vars by
``apps.cli.ops.config_generator``, so a workflow that sets
``VAR_CF_BYPASS_ENABLED`` and forgets ``VAR_CF_BYPASS_VIA_PROXY`` produces a
config where the flag falls back to its default. That is exactly how the
missing ``CF_BYPASS_VIA_PROXY`` survived in six workflows until commit
``d28c96e`` — the Python-side test could not see it.

The rule enforced here: a config-generating step that touches the CF-bypass
tier at all must set every key in the block. Workflows that set none are left alone —
they never fetch javdb (rollback, migration-only jobs), and demanding the block
there would be noise.
"""
import glob
import os
import re

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOW_GLOB = os.path.join(PROJECT_ROOT, ".github", "workflows", "*.yml")

CONFIG_GENERATOR = "apps.cli.ops.config_generator"
REQUIRED_KEYS = (
    "VAR_CF_BYPASS_SERVICE_PORT",
    "VAR_CF_BYPASS_ENABLED",
    "VAR_CF_BYPASS_VIA_PROXY",
    # Per-proxy port overrides. Without this the generated config falls back to
    # an empty map and every remapped host is dialled on the pool-wide default
    # port — a live bypass service reported as unreachable.
    "VAR_CF_BYPASS_PORT_MAP_JSON",
)
# ``${{ vars.X }}`` / ``${{ secrets.X }}`` evaluates to an empty string when the
# repo variable is unset, which config_generator then bakes in as an empty
# value. Any such reference therefore needs a ``||`` fallback. A hard-coded
# literal (TestIngestion pins ENABLED to 'True' so the canary always exercises
# the bypass path) is already a definite value and needs no default.
GH_EXPRESSION = re.compile(r"\$\{\{")


def _config_generator_steps():
    """Yield ``(workflow_file, step_env)`` for every config_generator step."""
    for path in sorted(glob.glob(WORKFLOW_GLOB)):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if CONFIG_GENERATOR in (step.get("run") or ""):
                    yield os.path.basename(path), (step.get("env") or {})


def test_config_generator_steps_exist():
    """Guard the guard: a broken parse must not silently pass every check."""
    steps = list(_config_generator_steps())
    assert len(steps) >= 10, f"expected the workflow sweep to find steps, got {steps}"


def test_cf_bypass_env_block_is_complete():
    """A step setting any VAR_CF_BYPASS_* must set the whole block."""
    for workflow, env in _config_generator_steps():
        if not any(key.startswith("VAR_CF_BYPASS_") for key in env):
            continue  # never fetches javdb — nothing to wire
        for key in REQUIRED_KEYS:
            assert key in env, (
                f"{workflow}: config_generator step sets VAR_CF_BYPASS_* but is "
                f"missing {key}"
            )


def test_cf_bypass_vars_have_defaults():
    """Every ``${{ vars.* }}`` CF-bypass value needs a ``||`` fallback."""
    for workflow, env in _config_generator_steps():
        for key in REQUIRED_KEYS:
            value = str(env.get(key, ""))
            if not GH_EXPRESSION.search(value):
                continue  # literal — already definite
            assert "||" in value, (
                f"{workflow}: {key} reads a repo variable with no '||' default, "
                f"so an unset variable generates an empty config value: {value}"
            )
