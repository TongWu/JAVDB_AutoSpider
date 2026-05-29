from __future__ import annotations

from javdb.workflow import git_side_effects
from javdb.workflow.git_side_effects import GitCommitRequest, GitCommitResult


def test_commit_workflow_outputs_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(git_side_effects, "_has_git_credentials", lambda username, password: False)

    result = git_side_effects.commit_workflow_outputs(
        GitCommitRequest(
            files_to_add=("logs/",),
            commit_message="Auto-commit: test",
            from_pipeline=True,
            git_username="",
            git_password="",
            git_repo_url="https://example.invalid/repo.git",
            git_branch="main",
        )
    )

    assert result == GitCommitResult(committed=False, skipped_reason="missing-credentials", error=None)


def test_commit_workflow_outputs_calls_git_helper(monkeypatch):
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(git_side_effects, "_has_git_credentials", lambda username, password: True)
    monkeypatch.setattr(git_side_effects, "_flush_log_handlers", lambda: calls.append({"flushed": True}))
    monkeypatch.setattr(
        git_side_effects,
        "_git_commit_and_push",
        lambda **kwargs: calls.append(kwargs),
    )

    result = git_side_effects.commit_workflow_outputs(
        GitCommitRequest(
            files_to_add=("logs/", "reports"),
            commit_message="Auto-commit: test",
            from_pipeline=True,
            git_username="user",
            git_password="token",
            git_repo_url="https://example.invalid/repo.git",
            git_branch="main",
        )
    )

    assert result == GitCommitResult(committed=True, skipped_reason=None, error=None)
    assert calls[0] == {"flushed": True}
    assert calls[1]["files_to_add"] == ["logs/", "reports"]
    assert calls[1]["commit_message"] == "Auto-commit: test"
