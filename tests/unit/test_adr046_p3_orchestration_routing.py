"""ADR-046 Phase 3: commit/rollback orchestration routes through repos."""
import inspect
import javdb.storage.sessions.commit as commit_mod
import javdb.storage.rollback.core as rollback_core


def test_commit_module_routes_through_history_repo():
    src = inspect.getsource(commit_mod)
    assert "HistoryRepo(" in src, "commit must construct HistoryRepo"
    # the drain call goes through the repo, not the bare facade fn
    assert "HistoryRepo().commit_session(" in src or "repo.commit_session(" in src


def test_rollback_core_routes_through_session_lifecycle_repo():
    src = inspect.getsource(rollback_core)
    assert "SessionLifecycleRepo" in src, "rollback must route via SessionLifecycleRepo"
