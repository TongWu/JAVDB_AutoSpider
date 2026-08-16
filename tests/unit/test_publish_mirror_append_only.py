"""Regression tests for the append-only public mirror builder (BFR-036).

The mirror used to be rebuilt from scratch with ``git filter-repo`` on every
publish and force-pushed. filter-repo is deterministic, so that was stable
while its inputs stood still -- but ``exclude_paths`` is one of its inputs.
Editing that list re-keyed every rewritten commit, and on 2026-08-12 a routing
fix re-keyed 2612 of 2618 commits while producing a byte-identical published
tree. The public ``dev`` and ``main`` branches were left with no merge base, so
GitHub rendered the entire repository as the promotion PR's diff.

The invariant that prevents a repeat is pinned by
``test_exclude_paths_change_does_not_rekey_published_commits``: a config edit
must change what FUTURE commits carry and must never disturb what is already
published. Everything else here guards the machinery that invariant rests on.

These build real git repositories in ``tmp_path`` and drive the script through
its CLI, because the failure modes being pinned are all in the plumbing --
which is also where the ``.gitignore`` bug below came from.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIRROR_SCRIPT = REPO_ROOT / ".github" / "scripts" / "publish_mirror.py"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-to-public.yml"

# Both are in exclude_paths, so neither exists on the public mirror.
pytestmark = pytest.mark.skipif(
    not MIRROR_SCRIPT.exists(),
    reason="publish_mirror.py is stripped from the public mirror",
)

TRAILER = "Private-Commit:"

COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "Ted",
    "GIT_AUTHOR_EMAIL": "ted@wu.engineer",
    "GIT_COMMITTER_NAME": "Ted",
    "GIT_COMMITTER_EMAIL": "ted@wu.engineer",
}


class Repo:
    """A throwaway git repository with a tiny commit-authoring API."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True,
            # Pointing HOME at the temp dir isolates ~/.gitconfig but not
            # /etc/gitconfig; a system-wide commit.gpgsign would fail every
            # commit here. Disable both config layers outright.
            env={**COMMIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin",
                 "HOME": str(self.path),
                 "GIT_CONFIG_GLOBAL": "/dev/null",
                 "GIT_CONFIG_SYSTEM": "/dev/null"},
        )
        assert result.returncode == 0, "git %s failed:\n%s" % (
            " ".join(args), result.stderr
        )
        return result.stdout.strip()

    def write(self, relpath: str, content: str) -> None:
        target = self.path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str, files: dict[str, str]) -> str:
        for relpath, content in files.items():
            self.write(relpath, content)
        # ``-f`` so a deliberately gitignored-but-tracked file can be staged;
        # that combination is exactly what the mirror used to silently drop.
        self.git("add", "-f", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tree_paths(self, ref: str) -> set[str]:
        listing = self.git("ls-tree", "-r", "--name-only", ref)
        return {line for line in listing.splitlines() if line}


def write_config(path: Path, exclude_paths: list[str], modifications: dict | None = None):
    path.write_text(
        yaml.safe_dump(
            {
                "exclude_paths": exclude_paths,
                "workflow_modifications": modifications or {},
                "branches": {"publish_branch": "public-sync",
                             "public_target_branch": "dev"},
            }
        ),
        encoding="utf-8",
    )


def replay(repo: Repo, config: Path, *extra: str) -> subprocess.CompletedProcess:
    # Strip GIT_* and GITHUB_OUTPUT from the inherited environment. The script
    # appends new_commits=/result_sha= to $GITHUB_OUTPUT, so running these tests
    # on Actions would otherwise have every case write to the real step-output
    # file; inherited GIT_DIR / GIT_INDEX_FILE would likewise steer the script
    # away from the repo under test.
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "GITHUB_OUTPUT"
    }
    env.update(COMMIT_ENV)
    return subprocess.run(
        [sys.executable, str(MIRROR_SCRIPT), "replay", "--config", str(config), *extra],
        cwd=repo.path, capture_output=True, text=True, env=env,
    )


@pytest.fixture
def private(tmp_path) -> Repo:
    """A private repo with three commits, one of which only touches reports/."""
    repo = Repo(tmp_path / "private")
    repo.commit("initial", {"README.md": "hello\n", "reports/day1.csv": "a\n"})
    repo.commit("feat: add the app", {"app.py": "print('hi')\n"})
    repo.commit("Auto-commit: pipeline results", {"reports/day2.csv": "b\n"})
    return repo


def test_exclude_paths_change_does_not_rekey_published_commits(private, tmp_path):
    """A config edit must not disturb already-published history. THE BFR-036 case.

    Under the old filter-repo rebuild, adding one entry to ``exclude_paths``
    re-keyed the entire mirror, destroying the merge base with the public main
    and turning the next promotion PR into a whole-repository diff.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"

    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0
    published_tip = private.git("rev-parse", "refs/heads/mirror")
    published_log = private.git("rev-list", "refs/heads/mirror")

    # A new private commit, and a NEW exclusion added at the same time.
    private.commit("docs: add a note", {"NOTES.md": "note\n", "scratch.tmp": "x\n"})
    write_config(config, ["reports/", "*.tmp"])

    second = replay(private, config, "--base", "refs/heads/mirror",
                    "--result-ref", "refs/heads/mirror2")
    assert second.returncode == 0, second.stdout + second.stderr
    # The drift gate must not read a config edit as public-side drift — that is
    # why it tests provenance rather than comparing trees.
    assert "the last commit this tool published" in second.stdout, (
        "the config edit tripped the drift gate; publishing after an "
        "exclude_paths change must stay routine"
    )

    assert private.git("rev-parse", "refs/heads/mirror") == published_tip, (
        "the previously published tip moved"
    )
    assert private.git(
        "merge-base", "--is-ancestor", "refs/heads/mirror", "refs/heads/mirror2"
    ) == "", "the published history is no longer an ancestor of the new mirror"

    new_log = private.git("rev-list", "refs/heads/mirror2")
    assert new_log.endswith(published_log), (
        "previously published commits were re-keyed by the exclude_paths edit"
    )
    # ...and the new entry still governs the newly published commit.
    assert "scratch.tmp" not in private.tree_paths("refs/heads/mirror2")


def test_tracked_but_gitignored_file_reaches_the_mirror(tmp_path):
    """A tracked file that ``.gitignore`` also lists must still publish.

    Building each tree by materialising the commit and running ``git add -A``
    looks equivalent but is not: ``git add`` honours ``.gitignore``, so
    ``.dockerignore`` -- tracked here, and listed in this repo's own
    ``.gitignore`` -- vanished from the mirror with no error anywhere. The
    builder reads the commit's tree through the index instead, which consults
    no ignore rules.
    """
    repo = Repo(tmp_path / "private")
    root = repo.commit("initial", {
        ".gitignore": ".dockerignore\n",
        ".dockerignore": "node_modules\n",
        "README.md": "hi\n",
    })
    repo.commit("feat: something", {"app.py": "x\n"})

    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    result = replay(repo, config, "--base", "refs/heads/mirror",
                    "--bootstrap-private", root, "--result-ref", "refs/heads/mirror")
    assert result.returncode == 0, result.stdout + result.stderr
    assert ".dockerignore" in repo.tree_paths("refs/heads/mirror")


def test_commit_touching_only_excluded_paths_is_pruned(private, tmp_path):
    """An Auto-commit that only writes reports/ must not reach the mirror."""
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])

    result = replay(private, config, "--base", "refs/heads/mirror",
                    "--bootstrap-private", root, "--result-ref", "refs/heads/mirror")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skip (empty after scrub)" in result.stdout

    subjects = private.git("log", "--format=%s", "refs/heads/mirror").splitlines()
    assert "Auto-commit: pipeline results" not in subjects
    assert "feat: add the app" in subjects


def test_excluded_paths_are_scrubbed(private, tmp_path):
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    published = private.tree_paths("refs/heads/mirror")
    assert not [p for p in published if p.startswith("reports/")]
    assert {"README.md", "app.py"} <= published


def test_replay_is_idempotent(private, tmp_path):
    """Re-publishing with no new private commits must produce nothing.

    Note the fixture's tip is an Auto-commit that the scrub empties, so the
    cursor sits one commit back and that pruned commit is re-examined on every
    run. Re-examining it must stay a no-op rather than republishing it.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0
    first = private.git("rev-parse", "refs/heads/mirror")

    for _ in range(2):
        result = replay(private, config, "--base", "refs/heads/mirror",
                        "--result-ref", "refs/heads/mirror")
        assert result.returncode == 0
        assert "(0 new commit(s))" in result.stdout
        assert private.git("rev-parse", "refs/heads/mirror") == first


def test_rewritten_private_history_aborts_the_replay(private, tmp_path):
    """If the cursor is not an ancestor of the source, stop and ask a human.

    Replaying onto a cursor the private history no longer contains would
    silently drop or duplicate work.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    # Rewrite private history so the recorded cursor is orphaned.
    private.git("checkout", "-q", "--orphan", "rewritten")
    private.commit("rebuilt history", {"README.md": "different\n"})

    result = replay(private, config, "--base", "refs/heads/mirror",
                    "--source", "rewritten", "--result-ref", "refs/heads/mirror2")
    assert result.returncode == 1
    assert "not an ancestor" in result.stdout


def test_a_stale_trailer_on_the_mirror_cannot_rewind_the_cursor(private, tmp_path):
    """A trailer is a line in a message, not proof of provenance.

    Cherry-picking an old mirror commit onto the branch carries its still-valid
    ``Private-Commit`` trailer along. That commit becomes the tip, so it is read
    as the newest anchor — the drift check then sees nothing above it and passes
    — while the cursor jumps backwards and republishes history already mirrored.
    The cursor must only ever move forward.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    # A second publish, so the mirror carries two trailers.
    private.commit("feat: more work", {"more.py": "x\n"})
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--result-ref", "refs/heads/mirror").returncode == 0

    # Re-apply the OLDER mirror commit's message on top — what a cherry-pick of
    # that commit leaves behind.
    stale = private.git("log", "--format=%B", "refs/heads/mirror").split(
        "%s " % TRAILER)[-1].split()[0]
    old_body = private.git("log", "-1", "--format=%B", "refs/heads/mirror~1")
    private.git("checkout", "-q", "-B", "mirror", "refs/heads/mirror")
    private.git("commit", "-q", "--allow-empty", "-m", old_body)
    private.git("checkout", "-q", "main")
    private.commit("feat: newer still", {"newest.py": "x\n"})

    result = replay(private, config, "--base", "refs/heads/mirror",
                    "--result-ref", "refs/heads/mirror2")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "not a descendant" in (result.stdout + result.stderr)
    assert stale  # the older private sha really was reachable as a trailer


def test_cursor_off_the_first_parent_chain_aborts_the_replay(private, tmp_path):
    """Plain ancestry is not enough — the cursor must be on the first-parent chain.

    Fast-forwarding the source branch onto a merge whose *first* parent is a
    side branch leaves the cursor an ancestor while the first-parent walk goes
    down that side branch. The range then contains commits older than the
    cursor, which would republish stale snapshots — a visible revert on the
    mirror — before the merge commit restores them.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    # Branch from the root, then merge main *into* the side branch and
    # fast-forward main onto the result: first parent is the side branch.
    private.git("checkout", "-q", "-b", "side", root)
    private.commit("feat: side work", {"side.py": "x\n"})
    private.git("merge", "-q", "--no-ff", "main", "-m", "Merge main into side")
    merged = private.git("rev-parse", "HEAD")
    private.git("checkout", "-q", "main")
    private.git("merge", "-q", "--ff-only", merged)

    result = replay(private, config, "--base", "refs/heads/mirror",
                    "--result-ref", "refs/heads/mirror2")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "first-parent chain" in result.stdout
    # The plain-ancestry guard this replaced would have waved it through: the
    # cursor is still an ancestor of the source, just not on its first-parent
    # chain. Repo.git asserts on a non-zero exit, so this call is the check.
    body = private.git("log", "-1", "--format=%B", "refs/heads/mirror")
    cursor = next(line.split()[1] for line in body.splitlines()
                  if line.startswith("Private-Commit:"))
    private.git("merge-base", "--is-ancestor", cursor, "HEAD")


def test_full_rebuild_scrubs_history_retroactively(private, tmp_path):
    """``--full`` is the escape hatch for a retroactive scrub.

    Append-only publishing cannot remove a path from already-published commits.
    ``--full`` rebuilds every commit under today's exclude_paths, which is why
    the workflow force pushes it and why it is opt-in.
    """
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--full",
                  "--result-ref", "refs/heads/mirror").returncode == 0
    assert "README.md" in private.tree_paths("refs/heads/mirror")

    write_config(config, ["reports/", "README.md"])
    assert replay(private, config, "--full",
                  "--result-ref", "refs/heads/rebuilt").returncode == 0

    rebuilt = private.git("rev-list", "refs/heads/rebuilt")
    for commit in rebuilt.splitlines():
        assert "README.md" not in private.tree_paths(commit), (
            "a full rebuild must scrub the path from every commit, not just the tip"
        )


def test_sync_cursor_trailer_names_the_commit_that_produced_it(private, tmp_path):
    """The ``Private-Commit:`` trailer is the cursor, and it must be honest.

    It names the private commit whose content produced this published commit --
    not the private tip at publish time. The fixture's tip is a pruned
    Auto-commit, so the newest trailer points one commit back, at the last
    change that actually published something.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    body = private.git("log", "-1", "--format=%B", "refs/heads/mirror")
    trailer = [l for l in body.splitlines() if l.startswith("Private-Commit:")]
    assert len(trailer) == 1
    assert trailer[0].split()[1] == private.git("rev-parse", "HEAD~1")
    assert private.git("log", "-1", "--format=%s", "refs/heads/mirror") == (
        private.git("log", "-1", "--format=%s", "HEAD~1")
    )


def test_a_preexisting_trailer_in_the_private_message_cannot_hijack_the_cursor(
    private, tmp_path
):
    """The cursor is the trailer this script appends, not one in the source body.

    A private commit message can legitimately contain a ``Private-Commit:`` line
    — any commit discussing this mechanism might. Reading the body top-down
    would return that one instead of the real cursor, which either aborts every
    later publish (not an ancestor) or silently replays mirrored history.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    decoy = "0" * 40
    private.git("commit", "-q", "--allow-empty", "-m",
                f"docs: describe the mirror\n\nPrivate-Commit: {decoy}\n")
    private.commit("feat: real work", {"later.py": "x\n"})

    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    result = replay(private, config, "--base", "refs/heads/mirror",
                    "--result-ref", "refs/heads/mirror2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert decoy not in result.stdout, "the decoy trailer was read as the cursor"
    assert "Private commits to replay: 0" in result.stdout


def test_direct_commits_on_the_mirror_are_not_silently_overwritten(private, tmp_path):
    """A fast-forward push is not proof that nothing was destroyed.

    Each replayed commit's tree is a full snapshot of the filtered private
    commit, not a patch. So a commit made straight to the mirror branch still
    fast-forwards on the next publish — the parent is the current tip — while
    its content is quietly dropped from the tree. Refuse instead, naming what
    would be lost.

    The test is provenance, not content: comparing trees would fire on every
    ``exclude_paths`` / ``workflow_modifications`` edit, because the tree
    published under the old config legitimately differs from what the new one
    produces — and making those edits cheap is the whole point of BFR-036.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0

    # Someone edits the mirror branch directly, then private work continues.
    private.git("checkout", "-q", "refs/heads/mirror")
    private.git("checkout", "-q", "-B", "mirror")
    private.commit("docs: tweak straight on the mirror", {"PUBLIC_ONLY.md": "hi\n"})
    private.git("checkout", "-q", "main")
    private.commit("feat: more private work", {"another.py": "x\n"})

    blocked = replay(private, config, "--base", "refs/heads/mirror",
                     "--result-ref", "refs/heads/mirror2")
    assert blocked.returncode == 1, blocked.stdout + blocked.stderr
    assert "did not produce" in blocked.stdout
    assert "docs: tweak straight on the mirror" in blocked.stdout, (
        "the operator must be told exactly which commit would be lost"
    )
    # Nothing was produced, so nothing could have been pushed.
    exists = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "refs/heads/mirror2"],
        cwd=private.path, capture_output=True,
    )
    assert exists.returncode != 0, "a result ref was written despite the refusal"

    # The override is available once the operator has reviewed the paths.
    allowed = replay(private, config, "--base", "refs/heads/mirror",
                     "--allow-public-drift", "--result-ref", "refs/heads/mirror3")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "PUBLIC_ONLY.md" not in private.tree_paths("refs/heads/mirror3")


def test_a_content_neutral_merge_on_the_mirror_does_not_block_publishing(
    private, tmp_path
):
    """The documented repair must not deadlock the next publish.

    Merging the public main back into the mirror branch — what the handbook
    tells you to do after a promotion lands squashed — adds a commit with no
    ``Private-Commit`` trailer. Gating purely on provenance would then refuse
    every later publish until someone reached for ``allow_public_drift``, i.e.
    the documented repair would break the thing it repairs. Foreign commits
    only matter when they actually changed content.
    """
    root = private.git("rev-list", "--max-parents=0", "HEAD")
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    assert replay(private, config, "--base", "refs/heads/mirror",
                  "--bootstrap-private", root,
                  "--result-ref", "refs/heads/mirror").returncode == 0
    mirror_tip = private.git("rev-parse", "refs/heads/mirror")

    # A merge that brings in nothing new — an empty commit stands in for it,
    # since both carry no trailer and leave the tree untouched.
    private.git("checkout", "-q", "-B", "mirror", "refs/heads/mirror")
    private.git("commit", "-q", "--allow-empty", "-m", "Merge main into dev")
    private.git("checkout", "-q", "main")
    private.commit("feat: later work", {"later.py": "x\n"})

    result = replay(private, config, "--base", "refs/heads/mirror",
                    "--result-ref", "refs/heads/mirror2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reproduces everything they changed" in result.stdout
    assert private.git(
        "merge-base", "--is-ancestor", "refs/heads/mirror", "refs/heads/mirror2"
    ) == "", "the merge commit must stay on the published history"
    assert mirror_tip in private.git("rev-list", "refs/heads/mirror2")


def test_workflow_forks_a_new_target_branch_from_the_default_branch():
    """A target_branch that does not exist yet must not start a new history.

    ``git fetch`` creates no remote-tracking ref for a branch the public repo
    does not have, so the base would be missing and the first replayed commit
    would be parentless — a branch with no merge base against the public
    default branch, which is unmergeable and not what the input promises.
    """
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    build = next(s for s in workflow["jobs"]["publish"]["steps"]
                 if s.get("name") == "Build mirror commits")
    script = build["run"]

    assert "rev-parse --verify" in script, (
        "the build step must check whether the target branch exists on the "
        "public repo before using it as the base"
    )
    assert "git remote set-head public --auto" in script, (
        "the fallback must ask the remote for its real default branch"
    )
    assert 'BASE_REF="refs/remotes/$REMOTE_HEAD"' in script, (
        "a missing target branch must fork from the public repo's default branch"
    )
    # Deriving the fallback from the config instead would be inert: with no
    # target_branch input, the configured default IS the missing target.
    assert "DEFAULT_PUBLIC_BRANCH" not in script, (
        "the fallback must not be derived from the configured target branch — "
        "without a target_branch input the two are the same value, so the "
        "fallback would just name the missing branch again"
    )
    assert "TARGET_BRANCH_MISSING=true" in script, (
        "the build step must record that the branch is missing; the push step "
        "needs it to know a no-op publish still has work to do"
    )

    # Creating the branch IS the push, so a zero-commit publish must not skip
    # it — otherwise the job ends green with the branch never created.
    push = next(s for s in workflow["jobs"]["publish"]["steps"]
                if s.get("name") == "Push to public repository")
    skip_guard = push["run"].split("push_method=noop")[0]
    assert '"$TARGET_BRANCH_MISSING" != "true"' in skip_guard, (
        "the no-op skip must not fire when the target branch still has to be "
        "created"
    )


def test_workflow_refuses_to_publish_from_a_non_default_branch():
    """Publishing from a feature branch would strand the cursor — enforce it.

    The cursor names the source commit. A squash or rebase merge leaves that
    branch commit out of the default branch, and every later publish then
    aborts with "not an ancestor". Dry runs stay allowed: they push nothing.
    """
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    guard = next(
        (s for s in steps if s.get("name") == "Guard the publish source"), None
    )
    assert guard is not None, (
        "no guard step: the workflow would publish whatever ref it was "
        "dispatched from"
    )
    assert guard["env"]["DEFAULT_BRANCH"] == (
        "${{ github.event.repository.default_branch }}"
    ), "the guard must compare against the repo's real default branch"
    assert "exit 1" in guard["run"], "a non-default source must fail the job"
    assert "$DRY_RUN" in guard["run"], (
        "dry runs must stay allowed from a branch — they push nothing and are "
        "how this workflow gets exercised before merge"
    )
    # The guard is useless after the push has already happened.
    names = [s.get("name") for s in steps]
    assert names.index("Guard the publish source") < names.index(
        "Push to public repository"
    )


def test_missing_cursor_without_bootstrap_is_an_error(private, tmp_path):
    """A pre-append-only mirror carries no cursor; guessing one is unsafe."""
    config = tmp_path / "publish.yml"
    write_config(config, ["reports/"])
    private.git("branch", "legacy-mirror", "HEAD")

    result = replay(private, config, "--base", "refs/heads/legacy-mirror",
                    "--result-ref", "refs/heads/mirror")
    assert result.returncode != 0
    assert "bootstrap" in (result.stdout + result.stderr).lower()


def test_workflow_never_force_pushes_the_public_repo_on_the_normal_path():
    """Only a full remirror may force push; the normal publish must fast-forward.

    A force push is what let a re-keyed history replace the mirror without
    anything failing. On the normal path a rejected push is the signal that
    something is wrong, and it must stay a failure.
    """
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    push = next(s for s in steps if s.get("name") == "Push to public repository")
    script = push["run"]

    marker = "# Deliberately NOT a force push"
    assert marker in script, (
        "the normal publish path is no longer marked; this test can no longer "
        "tell it apart from the full-remirror path"
    )
    normal_path = script.split(marker, 1)[1]

    assert "git push public " in normal_path
    assert "git push -f" not in normal_path, (
        "the normal publish path must not force push the public repo"
    )
    assert "was rejected" in normal_path, (
        "a rejected push must surface as an actionable error, not be forced through"
    )
    # The force push must remain reachable, but only under the explicit input.
    assert "git push -f public" in script.split(marker, 1)[0]
