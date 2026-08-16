"""Contract tests for the private->public secret-scrub in the mirror builder.

``.publish-config.yml``'s ``exclude_paths`` list drives
``.github/scripts/publish_mirror.py``, which strips files on their way to the
public mirror. Three properties are easy to break and impossible to notice from
the workflow log, so they are pinned here:

1. **Glob entries must actually match.** ``*.key``, ``*.env`` and friends are
   fnmatch patterns, not literals. They were silently no-ops for months while
   the workflow fed them to ``git filter-repo``'s ``--path`` (an exact/prefix
   matcher). Every glob entry in the config must strip what it claims to.
2. **A surviving excluded path must fail the job.** The verification used to
   print ``WARNING`` and exit 0, after which the workflow pushed the unscrubbed
   history anyway. A failed scrub must block the push.
3. **The matcher under test must be the production matcher.** These tests used
   to carry their own copy of filter-repo's matching rules, which is exactly
   the drift that let a whole class of survivor through. They now call
   ``publish_mirror.is_excluded`` directly, so the config, the scrub and the
   tests cannot disagree.

Two further tests guard the flip side: the exclusions must not sweep up the
``.env.example`` templates, which are deliberately public (the handbook tells
self-hosters to ``cp docker/.env.example .env``), and the exact-literal BFG
placeholder entry must not catch any path that should still publish.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-to-public.yml"
PUBLISH_CONFIG = REPO_ROOT / ".publish-config.yml"
MIRROR_SCRIPT = REPO_ROOT / ".github" / "scripts" / "publish_mirror.py"

# All three are in ``exclude_paths``, so none exists on the public mirror.
# See test_workflow_public_runner_markers.py for the same guard.
IS_PUBLIC_MIRROR = not PUBLISH_WORKFLOW.exists()

pytestmark = pytest.mark.skipif(
    IS_PUBLIC_MIRROR,
    reason="the publish workflow, its config and its script are stripped from the mirror",
)

# Inert 40-byte object-id placeholder left behind by an old BFG scrub. The
# ``.env`` blob it names is absent from the repository, so it leaks nothing,
# but it is confusing noise on the public mirror and ``*.env`` does not match
# it (the name ends in ``.git-id``).
BFG_PLACEHOLDER = ".env.REMOVED.git-id.REMOVED.git-id"


def _load_mirror_module():
    """Import publish_mirror.py by path — .github/scripts is not a package."""
    spec = importlib.util.spec_from_file_location("publish_mirror", MIRROR_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mirror = None if IS_PUBLIC_MIRROR else _load_mirror_module()


def _exclude_paths() -> list[str]:
    config = yaml.safe_load(PUBLISH_CONFIG.read_text(encoding="utf-8"))
    return list(config["exclude_paths"])


def _is_glob(pattern: str) -> bool:
    return any(char in pattern for char in mirror.GLOB_METACHARS)


def _excludes(pattern: str, path: str) -> bool:
    """The production matcher. Never reimplement it here — see point 3 above."""
    return mirror.is_excluded(pattern, path)


def test_config_still_contains_glob_entries():
    """Sanity anchor: the tests below are vacuous if no globs are configured."""
    globs = [p for p in _exclude_paths() if _is_glob(p)]
    assert globs, "expected glob entries (e.g. '*.key') in exclude_paths"


_GLOB_PROBE_CASES = [
    ("*.db", "scratch.db"),
    ("*.db", "reports/history.db"),
    ("*.env", ".env"),
    ("*.env.local", ".env.local"),
    ("*.env.*.local", "frontend/.env.production.local"),
    ("*.key", "server.key"),
    ("*.key", "certs/nested/server.key"),
    ("*.pem", "tls/cert.pem"),
    ("secrets/", "secrets/token.txt"),
    ("**/secrets/", "a/b/secrets/token.txt"),
]


@pytest.mark.parametrize("pattern,path", _GLOB_PROBE_CASES)
def test_glob_entries_are_not_no_ops(pattern, path):
    """A glob entry must strip what it names, at the root and when nested.

    The regression this pins: routing these to an exact/prefix matcher made
    every one of them match nothing at all, so the config looked configured
    while excluding nothing.
    """
    assert pattern in _exclude_paths(), (
        f"{pattern!r} is no longer configured; update the probe cases"
    )
    assert _excludes(pattern, path)


def test_every_configured_glob_has_a_probe_case():
    """Every glob entry in exclude_paths must be exercised above, or a glob
    that quietly stopped matching (a typo, a routing change) would go
    unnoticed instead of failing test_glob_entries_are_not_no_ops."""
    configured_globs = {p for p in _exclude_paths() if _is_glob(p)}
    probed = {pattern for pattern, _ in _GLOB_PROBE_CASES}
    missing = configured_globs - probed
    assert not missing, f"glob(s) in exclude_paths with no probe case: {missing}"


@pytest.fixture
def scrub_repo(tmp_path, monkeypatch):
    """A one-commit repo whose tree carries paths a scrub should catch.

    ``verify_scrub`` reads commits with ``git ls-tree``, so it needs a real
    repository and the process cwd pointed at it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(repo),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "Ted", "GIT_AUTHOR_EMAIL": "ted@wu.engineer",
        "GIT_COMMITTER_NAME": "Ted", "GIT_COMMITTER_EMAIL": "ted@wu.engineer",
    }

    def git(*args):
        result = subprocess.run(["git", *args], cwd=repo, env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    (repo / "secrets").mkdir()
    (repo / "secrets" / "token.txt").write_text("s3cret\n", encoding="utf-8")
    # A non-ASCII name: git octal-escapes and double-quotes these by default,
    # which is exactly what would hide a survivor from a path-list matcher.
    (repo / "secrets" / "密钥.txt").write_text("s3cret\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    monkeypatch.chdir(repo)
    return git("rev-parse", "HEAD")


def test_scrub_failure_blocks_the_push(scrub_repo, capsys):
    """A surviving excluded path must fail the gate, not warn and continue.

    The verification used to print WARNING and exit 0, after which the workflow
    published the unscrubbed content anyway.
    """
    assert mirror.verify_scrub(["secrets/"], [scrub_repo]) is False
    output = capsys.readouterr().out
    assert "::error::Refusing to publish" in output, (
        "a surviving excluded path must raise a GitHub Actions error; a warning "
        "would let the mirror publish unscrubbed content"
    )
    assert "secrets/token.txt" in output, "the survivor must be named"


def test_scrub_passes_when_nothing_survives(scrub_repo, capsys):
    """The gate must not be a rubber stamp — a clean tree has to pass."""
    assert mirror.verify_scrub(["nothing-here/"], [scrub_repo]) is True
    assert "::error::" not in capsys.readouterr().out


def test_scrub_sees_non_ascii_survivors(scrub_repo, capsys):
    """A non-ASCII survivor must be visible to the matcher.

    git octal-escapes and double-quotes non-ASCII paths by default. The
    verification runs with ``core.quotepath=false`` precisely so such a path
    still matches a literal prefix like ``secrets/``; without it this survivor
    would be reported clean.
    """
    assert mirror.verify_scrub(["secrets/"], [scrub_repo]) is False
    assert "密钥.txt" in capsys.readouterr().out


@pytest.mark.parametrize("template", [".env.example", "docker/.env.example"])
def test_public_env_templates_are_not_excluded(template):
    """The ``.env.example`` templates must survive the scrub.

    ``docs/handbook/*/self-hoster/docker-deploy.md`` instructs public users to
    ``cp docker/.env.example .env``, and ``docker/docker-build.sh`` reads it. A
    blanket ``.env.*`` exclusion (harmless while the globs were no-ops) would
    delete both from the mirror the moment the globs started working.
    """
    assert (REPO_ROOT / template).exists(), f"{template} is expected to be tracked"
    hits = [p for p in _exclude_paths() if _excludes(p, template)]
    assert not hits, f"{template} would be stripped from the public mirror by {hits}"


@pytest.mark.parametrize(
    "sensitive",
    [
        ".env",
        "apps/api/.env",
        ".env.local",
        "frontend/.env.production.local",
        "server.key",
        "certs/server.key",
        "tls/cert.pem",
        "reports/history.db",
        "scratch.db",
        "secrets/token.txt",
        "nested/secrets/token.txt",
        "config.py",
    ],
)
def test_sensitive_paths_are_excluded(sensitive):
    """Each secret-bearing shape the config claims to cover must actually match."""
    assert any(
        _excludes(pattern, sensitive) for pattern in _exclude_paths()
    ), f"no exclude_paths entry would strip {sensitive!r} from the public mirror"


@pytest.mark.parametrize(
    "survivor",
    [
        "nested/secrets/token.txt",
        "apps/api/secrets/coordinator.jwt",
        "a/b/c/secrets/token.txt",
    ],
)
def test_double_star_glob_matcher_catches_nested_survivors(survivor):
    """``**/secrets/`` survivors must be visible to the verification matcher.

    This is exactly the class of survivor the old pathspec probe was blind to.
    """
    assert _excludes("**/secrets/", survivor), (
        f"{survivor!r} would not be reported as a survivor of '**/secrets/'"
    )


def test_double_star_patterns_have_a_root_level_companion():
    """Every ``**/``-prefixed entry needs a companion covering the root level.

    A ``**/x`` glob requires a preceding ``/`` under *both* matchers, so it
    never covers a root-level ``x``. The old pathspec probe reported such an
    entry clean unconditionally, which hid the gap; the new matcher reports it
    clean too, because filter-repo genuinely was not asked to strip the root
    case. The safety therefore rests entirely on the paired literal entry
    (``secrets/`` alongside ``**/secrets/``), so pin that pairing here.
    """
    exclude_paths = _exclude_paths()
    double_star = [p for p in exclude_paths if p.startswith("**/")]
    assert double_star, (
        "expected at least one '**/'-prefixed entry in exclude_paths; if the "
        "last one was removed on purpose, retire this test with it"
    )

    for pattern in double_star:
        remainder = pattern[len("**/") :]
        root_probe = remainder + "probe.txt" if remainder.endswith("/") else remainder

        assert not _excludes(pattern, root_probe), (
            f"assumption changed: {pattern!r} now covers the root-level "
            f"{root_probe!r} on its own — rewrite this test"
        )
        companions = [
            p
            for p in exclude_paths
            if p != pattern and _excludes(p, root_probe)
        ]
        assert companions, (
            f"{pattern!r} only covers nested paths, and no other exclude_paths "
            f"entry covers the root-level {root_probe!r}; a root-level file "
            f"would survive the scrub and the verification cannot see it"
        )


def test_bfg_object_id_placeholder_is_excluded():
    """The inert BFG placeholder must be scrubbed from the public mirror.

    ``.env.REMOVED.git-id.REMOVED.git-id`` is a 40-byte file whose only content
    is the object id of the ``.env`` blob an old BFG run stripped. That blob is
    confirmed absent from the repository, so the placeholder leaks nothing — it
    is published noise. No other entry covers it, hence the exact literal.
    """
    exclude_paths = _exclude_paths()
    assert BFG_PLACEHOLDER in exclude_paths, (
        f"{BFG_PLACEHOLDER!r} is published to the public mirror unless it is "
        "listed in exclude_paths"
    )
    # Listed as an exact literal, so it routes to --path (not --path-glob) and
    # can only ever match itself or something beneath it as a directory.
    assert not _is_glob(BFG_PLACEHOLDER)
    assert _excludes(BFG_PLACEHOLDER, BFG_PLACEHOLDER)
    # Justifies the entry: nothing else in the config would have caught it.
    # ``*.env`` does not match — the name ends in ``.git-id``.
    assert not any(
        _excludes(pattern, BFG_PLACEHOLDER)
        for pattern in exclude_paths
        if pattern != BFG_PLACEHOLDER
    ), f"{BFG_PLACEHOLDER!r} is already covered; the literal entry is redundant"


@pytest.mark.parametrize(
    "keeper",
    [
        # Deliberately public templates (see test_public_env_templates_...).
        ".env.example",
        "docker/.env.example",
        # Other BFG placeholders that really exist in this history. They are
        # out of scope for this entry: the ones under logs/ are already covered
        # by the 'logs/' exclusion, and these publish today.
        "README.md.REMOVED.git-id",
        "utils/parser.py.REMOVED.git-id",
    ],
)
def test_bfg_placeholder_entry_has_no_collateral(keeper):
    """The literal placeholder entry must not strip anything else."""
    assert not _excludes(BFG_PLACEHOLDER, keeper), (
        f"the {BFG_PLACEHOLDER!r} entry would also strip {keeper!r}"
    )
