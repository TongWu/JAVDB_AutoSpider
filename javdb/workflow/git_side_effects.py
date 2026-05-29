from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class GitCommitRequest:
    files_to_add: Sequence[str]
    commit_message: str
    from_pipeline: bool
    git_username: str
    git_password: str
    git_repo_url: str
    git_branch: str


@dataclass(frozen=True)
class GitCommitResult:
    committed: bool
    skipped_reason: str | None
    error: str | None


def _has_git_credentials(git_username: str, git_password: str) -> bool:
    from javdb.infra.git_helper import has_git_credentials

    return has_git_credentials(git_username, git_password)


def _flush_log_handlers() -> None:
    from javdb.infra.git_helper import flush_log_handlers

    flush_log_handlers()


def _git_commit_and_push(**kwargs: object) -> None:
    from javdb.infra.git_helper import git_commit_and_push

    git_commit_and_push(**kwargs)


def commit_workflow_outputs(request: GitCommitRequest) -> GitCommitResult:
    if not _has_git_credentials(request.git_username, request.git_password):
        return GitCommitResult(
            committed=False,
            skipped_reason="missing-credentials",
            error=None,
        )

    try:
        _flush_log_handlers()
        _git_commit_and_push(
            files_to_add=list(request.files_to_add),
            commit_message=request.commit_message,
            from_pipeline=request.from_pipeline,
            git_username=request.git_username,
            git_password=request.git_password,
            git_repo_url=request.git_repo_url,
            git_branch=request.git_branch,
        )
    except Exception as exc:
        return GitCommitResult(committed=False, skipped_reason=None, error=str(exc))

    return GitCommitResult(committed=True, skipped_reason=None, error=None)
