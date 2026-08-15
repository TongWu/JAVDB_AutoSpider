"""Contract tests for the private->public secret-scrub in ``publish-to-public.yml``.

``.publish-config.yml``'s ``exclude_paths`` list is fed to ``git filter-repo``
to strip files from the ENTIRE history of the public mirror. Two properties of
that plumbing are easy to break and impossible to notice from the workflow log,
so they are pinned here:

1. **Globs must be routed to ``--path-glob``.** ``git filter-repo``'s ``--path``
   is an alias for ``--path-match``: an exact file path or directory prefix. It
   does not interpret ``*``/``?``/``[``. Passing ``*.key`` as ``--path`` matches
   nothing at all, so every glob entry in the config becomes a silent no-op —
   the exclusion appears configured while excluding nothing.
2. **A surviving excluded path must fail the job.** The verification loop used
   to print ``WARNING`` and exit 0, after which the workflow force-pushed the
   unscrubbed history to the public repo anyway. A failed scrub must block the
   push.
3. **The verification must use filter-repo's matcher, not a git pathspec.**
   ``git log --all -- '<pattern>'`` is a different matcher with different
   rules: a ``**/``-prefixed pathspec matches nothing at all (verified on git
   2.43), so a surviving ``nested/secrets/token.txt`` was reported "clean" and
   sailed straight through the now-fatal gate. Because a ``**/`` glob covers
   only the nested case under *either* matcher, each such entry also needs a
   companion entry covering the root level.

Two further tests guard the flip side: making the globs effective must not sweep
up the ``.env.example`` templates, which are deliberately public (the handbook
tells self-hosters to ``cp docker/.env.example .env``), and the exact-literal
BFG placeholder entry must not catch any path that should still publish.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-to-public.yml"
PUBLISH_CONFIG = REPO_ROOT / ".publish-config.yml"

# Both files are themselves in ``exclude_paths``, so neither exists on the
# public mirror. See test_workflow_public_runner_markers.py for the same guard.
IS_PUBLIC_MIRROR = not PUBLISH_WORKFLOW.exists()

pytestmark = pytest.mark.skipif(
    IS_PUBLIC_MIRROR,
    reason="publish-to-public.yml and .publish-config.yml are stripped from the mirror",
)

REWRITE_STEP_NAME = "Rewrite history to remove excluded files"
VERIFICATION_MARKER = "Verification - checking if excluded files exist"

# Mirror of the routing rule in the rewrite step's bash ``case``. An entry with
# any of these characters is a glob and must go to ``--path-glob``.
GLOB_METACHARS = "*?["

# Inert 40-byte object-id placeholder left behind by an old BFG scrub. The
# ``.env`` blob it names is absent from the repository, so it leaks nothing,
# but it is confusing noise on the public mirror and ``*.env`` does not match
# it (the name ends in ``.git-id``).
BFG_PLACEHOLDER = ".env.REMOVED.git-id.REMOVED.git-id"


def _exclude_paths() -> list[str]:
    config = yaml.safe_load(PUBLISH_CONFIG.read_text(encoding="utf-8"))
    return list(config["exclude_paths"])


def _rewrite_step_script() -> str:
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["publish"]["steps"]:
        if step.get("name") == REWRITE_STEP_NAME:
            return step["run"]
    raise AssertionError(f"step {REWRITE_STEP_NAME!r} not found in publish job")


def _verification_section() -> str:
    """The part of the rewrite step that checks for scrub survivors."""
    script = _rewrite_step_script()
    assert VERIFICATION_MARKER in script, "verification block not found in rewrite step"
    return script.split(VERIFICATION_MARKER)[-1]


def _collapse_whitespace(script: str) -> str:
    """``script`` with every whitespace run reduced to a single space.

    Lets a shell-shape assertion ignore the YAML block-scalar indentation it
    happens to be written at.
    """
    return re.sub(r"\s+", " ", script)


def _is_glob(pattern: str) -> bool:
    return any(char in pattern for char in GLOB_METACHARS)


def _filter_repo_excludes(pattern: str, path: str) -> bool:
    """Whether git-filter-repo would drop ``path`` given ``pattern``.

    Mirrors git-filter-repo 2.47's semantics: literal entries use
    ``filename_matches`` (exact match or leading-directory match), glob entries
    use ``fnmatch`` plus the extra pattern ``AppendFilter`` appends when the
    glob does not already end in ``*`` (``pattern + "*"`` for a trailing
    slash, else ``pattern + "/*"``) so a directory glob covers its contents.
    """
    if _is_glob(pattern):
        candidates = [pattern]
        if not pattern.endswith("*"):
            candidates.append(pattern + ("*" if pattern.endswith("/") else "/*"))
        return any(fnmatch.fnmatch(path, candidate) for candidate in candidates)
    trimmed = pattern.rstrip("/")
    return path == trimmed or path.startswith(trimmed + "/")


def test_config_still_contains_glob_entries():
    """Sanity anchor: the tests below are vacuous if no globs are configured."""
    globs = [p for p in _exclude_paths() if _is_glob(p)]
    assert globs, "expected glob entries (e.g. '*.key') in exclude_paths"


def test_rewrite_step_routes_globs_to_path_glob():
    script = _rewrite_step_script()
    assert "--path-glob" in script, (
        "the rewrite step must pass glob entries to git filter-repo as "
        "--path-glob; --path is an exact/prefix match and silently ignores "
        "glob metacharacters, turning every glob exclusion into a no-op"
    )
    # The pre-fix loop appended --path for every entry unconditionally. Match on
    # whitespace-collapsed text: the shell is embedded in a YAML block scalar, so
    # a re-indent must not be able to silently defeat this regression check.
    collapsed = _collapse_whitespace(script)
    assert (
        'for path in "${EXCLUDE_PATHS[@]}"; do FILTER_ARGS+=("--path" "$path")'
        not in collapsed
    ), "unconditional --path loop reintroduced: glob exclusions would be no-ops"
    assert 'FILTER_ARGS+=("--path-glob" "$path")' in script


def test_scrub_verification_fails_the_job():
    verification = _verification_section()
    assert "exit 1" in verification, (
        "a surviving excluded path must fail the job — otherwise the workflow "
        "force-pushes unscrubbed history to the public repo"
    )
    assert "WARNING" not in verification, (
        "the scrub result must be an error that blocks the push, not a warning"
    )


@pytest.mark.parametrize("template", [".env.example", "docker/.env.example"])
def test_public_env_templates_are_not_excluded(template):
    """The ``.env.example`` templates must survive the scrub.

    ``docs/handbook/*/self-hoster/docker-deploy.md`` instructs public users to
    ``cp docker/.env.example .env``, and ``docker/docker-build.sh`` reads it. A
    blanket ``.env.*`` exclusion (harmless while the globs were no-ops) would
    delete both from the mirror the moment the globs started working.
    """
    assert (REPO_ROOT / template).exists(), f"{template} is expected to be tracked"
    hits = [p for p in _exclude_paths() if _filter_repo_excludes(p, template)]
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
        _filter_repo_excludes(pattern, sensitive) for pattern in _exclude_paths()
    ), f"no exclude_paths entry would strip {sensitive!r} from the public mirror"


def test_verification_matches_with_filter_repo_semantics_not_git_pathspec():
    """The survivor check must not be a ``git log -- '<pattern>'`` pathspec probe.

    git pathspec and git-filter-repo are *different matchers*. Verified
    empirically on git 2.43: ``git log --all --full-history -- '**/secrets/'``
    matches nothing whatsoever — neither the root-level nor the nested case —
    while filter-repo's ``--path-glob`` does strip the nested case. So a
    surviving ``nested/secrets/token.txt`` was reported "clean" and would have
    sailed straight through the now-fatal gate to the force push.

    The replacement enumerates every path in the rewritten history and matches
    each one the way filter-repo's ``newname()`` does.
    """
    verification = _verification_section()
    assert 'git log --all --full-history -- "$path"' not in verification, (
        "the git-pathspec survivor probe is back; it reports '**/'-prefixed "
        "patterns clean no matter what actually survived the scrub"
    )
    assert "--name-only" in verification, (
        "the verification must enumerate the paths in the rewritten history"
    )
    assert "fnmatch" in verification, (
        "the verification must reproduce filter-repo's fnmatch glob semantics"
    )
    # git octal-escapes and double-quotes non-ASCII paths by default, which the
    # in-git pathspec probe never saw (it matched raw bytes) but which would
    # make every such survivor invisible to a path-list matcher. This history
    # contains 28 such paths, several of them under ``reports/``.
    assert "core.quotepath=false" in verification, (
        "non-ASCII survivors would be octal-escaped and double-quoted, so they "
        "would silently fail to match a literal prefix like 'reports/'"
    )


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
    assert _filter_repo_excludes("**/secrets/", survivor), (
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

        assert not _filter_repo_excludes(pattern, root_probe), (
            f"assumption changed: {pattern!r} now covers the root-level "
            f"{root_probe!r} on its own — rewrite this test"
        )
        companions = [
            p
            for p in exclude_paths
            if p != pattern and _filter_repo_excludes(p, root_probe)
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
    assert _filter_repo_excludes(BFG_PLACEHOLDER, BFG_PLACEHOLDER)
    # Justifies the entry: nothing else in the config would have caught it.
    # ``*.env`` does not match — the name ends in ``.git-id``.
    assert not any(
        _filter_repo_excludes(pattern, BFG_PLACEHOLDER)
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
    assert not _filter_repo_excludes(BFG_PLACEHOLDER, keeper), (
        f"the {BFG_PLACEHOLDER!r} entry would also strip {keeper!r}"
    )
