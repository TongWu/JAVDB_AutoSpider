"""Contract tests for the ``# PUBLIC_RUNNER:`` runner-rewrite convention.

Private-repo workflows may run on self-hosted runners, but the public
mirror (synced by ``publish-to-public.yml``) has none. The "Replace
private runners for public repo" step rewrites any ``runs-on`` line
carrying a ``# PUBLIC_RUNNER: <runner>`` marker to the GitHub-hosted
runner named inline. A literal ``runs-on: self-hosted`` line *without*
the marker would leave the public repo with jobs no runner can pick up,
so these tests pin the convention:

1. every literal ``runs-on: self-hosted`` carries a marker the rewrite
   regex can consume, naming a non-self-hosted replacement;
2. ``unit-tests.yml`` jobs all run on self-hosted runners (the 2026-06
   migration of CI onto self-hosted x64/arm64 Linux machines);
3. every ``actions/setup-python`` use on the self-hosted path is preceded
   by ``./.github/actions/ensure-python`` for the same version.
   setup-python's download manifest only covers Ubuntu, so on the
   fleet's non-Ubuntu machines a missing ensure-python step fails with
   "version ... was not found for this operating system" — while the
   public mirror (rewritten to GitHub-hosted runners) stays green and
   hides the regression from PR review;
4. no ``unit-tests.yml`` checkout uses ``lfs: true`` — that requires a
   git-lfs binary before checkout starts, which fleet machines may lack
   (repo-local bootstrap actions cannot run pre-checkout). The
   test-execution jobs instead pull LFS objects (``reports/*.db``,
   needed by impact-selected tests; see commit c1a78737) via
   ``./.github/actions/ensure-git-lfs`` after checkout;
5. ``build-rust-extension.yml`` pins each wheel-build job to an
   architecture-specific GitHub-hosted runner (``ubuntu-latest`` for x64 /
   ``ubuntu-24.04-arm`` for arm64; commit 831a296f moved the wheel build off
   the fleet). Pointing both jobs at the same image would build an x64 wheel
   under the ``*-arm-*`` artifact name and warm the wrong per-arch cache.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Mirror of the rewrite pattern in publish-to-public.yml ("Replace private
# runners for public repo" step). Both must stay in sync. The private value is
# matched lazily so the array form ``[self-hosted, ARM64]`` rewrites too.
PUBLIC_RUNNER_REWRITE = re.compile(
    r"^(?P<indent>\s*)runs-on:\s*.+?\s*"
    r"#\s*PUBLIC_RUNNER:\s*(?P<runner>\S+).*$"
)

# Literal self-hosted forms that must carry a marker: the single-token
# ``runs-on: self-hosted`` and the arch-pinned array ``runs-on: [self-hosted,
# X64]``. Expression forms such as
# ``runs-on: ${{ inputs.runner || 'self-hosted' }}`` are dispatch-selectable
# and intentionally outside the rewrite's scope.
LITERAL_SELF_HOSTED = re.compile(
    r"^\s*runs-on:\s*(?:self-hosted\b|\[[^\]]*self-hosted)"
)


def iter_workflow_lines():
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            yield path, lineno, line


def test_literal_self_hosted_runners_carry_public_runner_marker():
    violations = []
    for path, lineno, line in iter_workflow_lines():
        if not LITERAL_SELF_HOSTED.match(line):
            continue
        match = PUBLIC_RUNNER_REWRITE.match(line)
        if match is None:
            violations.append(
                f"{path.name}:{lineno}: missing '# PUBLIC_RUNNER: <runner>' "
                f"marker: {line.strip()}"
            )
        elif "self-hosted" in match.group("runner"):
            violations.append(
                f"{path.name}:{lineno}: PUBLIC_RUNNER must name a "
                f"GitHub-hosted runner: {line.strip()}"
            )
    assert not violations, "\n".join(violations)


def test_unit_tests_jobs_run_self_hosted():
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / "unit-tests.yml").read_text(encoding="utf-8")
    )
    runners = {
        job_id: job.get("runs-on") for job_id, job in workflow["jobs"].items()
    }
    # Jobs pin the self-hosted ARM64 fleet via the arch-label array form
    # ``[self-hosted, ARM64]`` (see test_build_rust_extension... for why the
    # bare label is unsafe); accept both the array and the bare string.
    not_self_hosted = {
        job_id: runner
        for job_id, runner in runners.items()
        if "self-hosted" not in (runner if isinstance(runner, list) else [runner])
    }
    assert not not_self_hosted, (
        "unit-tests.yml jobs are expected to run on self-hosted runners "
        f"(with a # PUBLIC_RUNNER marker for the public mirror), got: "
        f"{not_self_hosted}"
    )


def test_unit_tests_setup_python_steps_are_preceded_by_ensure_python():
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / "unit-tests.yml").read_text(encoding="utf-8")
    )
    violations = []
    for job_id, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        for index, step in enumerate(steps):
            if not str(step.get("uses", "")).startswith("actions/setup-python@"):
                continue
            wanted = step.get("with", {}).get("python-version")
            seeded = any(
                earlier.get("uses") == "./.github/actions/ensure-python"
                and earlier.get("with", {}).get("python-version") == wanted
                for earlier in steps[:index]
            )
            if not seeded:
                violations.append(
                    f"{job_id}: setup-python for {wanted!r} lacks a preceding "
                    f"ensure-python step for the same version"
                )
    assert not violations, "\n".join(violations)


def test_unit_tests_lfs_is_pulled_post_checkout_not_at_checkout():
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / "unit-tests.yml").read_text(encoding="utf-8")
    )
    lfs_checkouts = [
        job_id
        for job_id, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and step.get("with", {}).get("lfs")
    ]
    assert not lfs_checkouts, (
        "checkout with lfs:true requires a pre-existing git-lfs binary the "
        f"fleet may lack; use ensure-git-lfs after checkout instead: {lfs_checkouts}"
    )
    for job_id in ("python-selected", "python-full"):
        steps = workflow["jobs"][job_id].get("steps", [])
        assert any(
            step.get("uses") == "./.github/actions/ensure-git-lfs"
            for step in steps
        ), (
            f"{job_id} runs impact-selected tests that read the LFS-tracked "
            "reports/*.db files and must call ensure-git-lfs after checkout"
        )


def test_setup_python_env_composite_seeds_tool_cache_first():
    action = yaml.safe_load(
        (REPO_ROOT / ".github" / "actions" / "setup-python-env" / "action.yml")
        .read_text(encoding="utf-8")
    )
    uses = [str(step.get("uses", "")) for step in action["runs"]["steps"]]
    ensure_at = next(
        (i for i, u in enumerate(uses) if u == "./.github/actions/ensure-python"),
        None,
    )
    setup_at = next(
        (i for i, u in enumerate(uses) if u.startswith("actions/setup-python@")),
        None,
    )
    assert ensure_at is not None, "setup-python-env must call ensure-python"
    assert setup_at is not None, "setup-python-env must call actions/setup-python"
    assert ensure_at < setup_at, (
        "ensure-python must seed the tool cache before actions/setup-python runs"
    )


def test_build_rust_extension_jobs_pin_runner_arch():
    """Each wheel-build job must pin an architecture-specific runner image.

    The wheel build runs on GitHub-hosted runners (commit 831a296f). Pointing
    both jobs at the same image would build an x64 wheel under the ``*-arm-*``
    artifact name (and warm the wrong per-arch cache), so each job pins a
    distinct arch: ``ubuntu-latest`` (x64) and ``ubuntu-24.04-arm`` (arm64).
    """
    workflow = yaml.safe_load(
        (WORKFLOWS_DIR / "build-rust-extension.yml").read_text(encoding="utf-8")
    )
    runners = {
        job_id: job.get("runs-on") for job_id, job in workflow["jobs"].items()
    }
    assert runners.get("build-x64") == "ubuntu-latest", runners
    assert runners.get("build-arm") == "ubuntu-24.04-arm", runners
