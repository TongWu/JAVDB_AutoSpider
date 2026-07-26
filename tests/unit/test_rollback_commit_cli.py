from apps.cli.db import commit_session, rollback
import javdb.storage.rollback.core as rollback_core


class _Args:
    session_id = 7
    run_id = None
    attempt = None
    run_started_at = "2026-05-04T00:00:00Z"
    include_orphaned = True


def test_rollback_defaults_to_dry_run_and_apply_opts_in():
    assert rollback._parse_args(["--session-id", "1"]).dry_run is True
    assert rollback._parse_args(["--session-id", "1", "--dry-run"]).dry_run is True
    assert rollback._parse_args(["--session-id", "1", "--apply"]).dry_run is False


def test_rollback_resolve_unions_explicit_and_window_sessions(monkeypatch):
    seen = []
    monkeypatch.setattr(
        rollback_core,
        "find_window_sessions",
        lambda since, **_kw: seen.append(since) or [7, 8],
    )

    # 2026-05-08: window scan only kicks in when --include-orphaned is set
    # (or no other source yielded anything).  ``_Args`` opts into the
    # legacy behaviour via ``include_orphaned=True``.
    assert rollback_core._resolve_target_sessions(
        _Args(), "2026-05-04 00:00:00",
    ) == [7, 8]
    assert seen == ["2026-05-04 00:00:00"]


def test_rollback_normalizes_offset_timestamp_to_utc():
    assert (
        rollback_core.normalize_run_started_at("2026-05-04T19:30:00-04:00")
        == "2026-05-04 23:30:00"
    )


def test_rollback_normalize_returns_none_for_invalid_timestamp():
    assert rollback_core.normalize_run_started_at("not-a-time") is None


def test_rollback_returns_partial_failure_on_real_drift(monkeypatch, capsys):
    monkeypatch.setattr(rollback, "init_db", lambda: None)
    monkeypatch.setattr(rollback, "close_db", lambda: None)
    monkeypatch.setattr(
        rollback_core,
        "_resolve_target_sessions",
        lambda _args, _normalized: [7],
    )
    monkeypatch.setattr(
        rollback_core, "_detect_cross_day", lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        rollback_core,
        "db_rollback_session",
        lambda *_args, **_kwargs: {"history": {"drift_skipped": 1}},
    )
    monkeypatch.setattr(rollback_core, "_emit_metrics", lambda summary: None)

    rc = rollback.main(["--session-id", "7", "--apply"])

    assert rc == 4
    assert '"drift_total": 1' in capsys.readouterr().out


def test_commit_session_explicit_id_survives_window_lookup_failure(monkeypatch):
    marked = []
    close_calls = []

    monkeypatch.setattr(commit_session, "init_db", lambda: None)
    monkeypatch.setattr(commit_session, "close_db", lambda: close_calls.append(True))

    # Window lookup now routes through SessionLifecycleRepo (ADR-032 Phase
    # 2a). Stub the Repo so find_in_progress_sessions raises, exercising the
    # explicit-id survival path.
    class _RaisingRepo:
        def find_in_progress_sessions(self, **_kwargs):
            raise RuntimeError("lookup failed")

        def find_sessions_by_run(self, _run_id, _attempt):
            # Only the *window* lookup fails here. Ownership must still
            # resolve, or this test would silently become a test of the
            # ownership-failure path instead — and only under CI, which
            # sets GITHUB_RUN_ID while a local run does not.
            return ["7"]

    monkeypatch.setattr(
        commit_session,
        "SessionLifecycleRepo",
        lambda *a, **k: _RaisingRepo(),
    )
    # Session status flips now route through SessionLifecycle.transition
    # (ADR-019); commit_session imports it as a module-level `transition`.
    monkeypatch.setattr(
        commit_session,
        "transition",
        lambda sid, to, **_kwargs: marked.append(sid) or 1,
    )

    rc = commit_session.main([
        "--session-id", "7",
        "--run-started-at", "2026-05-04T00:00:00Z",
    ])

    assert rc == 0
    # --session-id is parsed as str post-2026-05-13 (TEXT snowflake PK).
    assert marked == ["7"]
    assert close_calls == [True]


class _AuditPreState:
    """Non-pending pre-state — keeps the drain path out of these tests."""

    write_mode = "audit"
    status = "in_progress"


def _stub_commit_window(monkeypatch, window, own_run_sessions):
    """Wire commit_session.main() up to fake window / run-identity lookups.

    ``own_run_sessions`` may be an exception instance, which the fake repo
    raises to exercise the lookup-failure paths.
    """
    marked = []
    monkeypatch.setattr(commit_session, "init_db", lambda: None)
    monkeypatch.setattr(commit_session, "close_db", lambda: None)

    class _Repo:
        def find_in_progress_sessions(self, **_kwargs):
            return list(window)

        def find_sessions_by_run(self, _run_id, _attempt):
            if isinstance(own_run_sessions, Exception):
                raise own_run_sessions
            return list(own_run_sessions)

    monkeypatch.setattr(
        commit_session, "SessionLifecycleRepo", lambda *a, **k: _Repo(),
    )
    monkeypatch.setattr(
        commit_session, "read_session_pre_state",
        lambda _sid: _AuditPreState(),
    )
    monkeypatch.setattr(
        commit_session, "transition",
        lambda sid, to, **_kwargs: marked.append(sid) or 1,
    )
    return marked


def test_commit_session_window_skips_other_runs_live_session(monkeypatch):
    """Regression (2026-07-26): concurrent AdHoc runs.

    ``--run-started-at`` is a bare time window, so an overlapping run's
    still-spidering session lands inside it. Committing it mid-run
    strands every row staged afterwards in the Pending* tables (255 rows
    in the production incident). Only sessions carrying this run's
    ``RunId`` may be swept up.
    """
    monkeypatch.setenv("GITHUB_RUN_ID", "30195210787")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    marked = _stub_commit_window(
        monkeypatch,
        window=["S-mine", "S-other-run"],
        own_run_sessions=["S-explicit", "S-mine"],
    )

    rc = commit_session.main([
        "--session-id", "S-explicit",
        "--run-started-at", "2026-07-26T08:46:03Z",
        "--no-claim-commit",
    ])

    assert rc == 0
    assert sorted(marked) == ["S-explicit", "S-mine"]
    assert "S-other-run" not in marked


def test_commit_session_window_unrestricted_without_run_identity(monkeypatch):
    """Local runs stamp ``RunId`` NULL and have no concurrent peers."""
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)

    marked = _stub_commit_window(
        monkeypatch,
        window=["S-a", "S-b"],
        own_run_sessions=[],
    )

    rc = commit_session.main([
        "--run-started-at", "2026-07-26T08:46:03Z",
        "--no-claim-commit",
    ])

    assert rc == 0
    assert sorted(marked) == ["S-a", "S-b"]


def test_commit_session_window_failure_falls_back_to_run_identity(monkeypatch):
    """A failed window scan must not silently drop this run's siblings.

    Production always passes `--session-id` alongside the window flag, so
    committing only the explicit session would leave the run's other
    sessions `in_progress` for the 48h stale sweep to roll back — losing
    their staged writes. Ownership already resolved which sessions are
    ours, so use that instead.
    """
    monkeypatch.setenv("GITHUB_RUN_ID", "30195210787")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    marked = []
    monkeypatch.setattr(commit_session, "init_db", lambda: None)
    monkeypatch.setattr(commit_session, "close_db", lambda: None)

    class _WindowFailsRepo:
        def find_in_progress_sessions(self, **_kwargs):
            raise RuntimeError("D1 timeout")

        def find_sessions_by_run(self, _run_id, _attempt):
            return ["S-explicit", "S-sibling"]

    monkeypatch.setattr(
        commit_session, "SessionLifecycleRepo", lambda *a, **k: _WindowFailsRepo(),
    )
    monkeypatch.setattr(
        commit_session, "read_session_pre_state", lambda _sid: _AuditPreState(),
    )
    monkeypatch.setattr(
        commit_session, "transition",
        lambda sid, to, **_kwargs: marked.append(sid) or 1,
    )

    rc = commit_session.main([
        "--session-id", "S-explicit",
        "--run-started-at", "2026-07-26T08:46:03Z",
        "--no-claim-commit",
    ])

    assert rc == 0
    assert sorted(marked) == ["S-explicit", "S-sibling"]


def test_commit_session_run_lookup_failure_is_not_an_empty_set(monkeypatch):
    """A failed RunId lookup must not silently behave like "owns nothing".

    Collapsing the two would skip the whole window and still exit 0, so a
    sibling session this run created would sit `in_progress` until the 48h
    stale sweep rolled it back. Without an explicit `--session-id` there is
    nothing left to commit, so this is a hard error.
    """
    monkeypatch.setenv("GITHUB_RUN_ID", "30195210787")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    marked = _stub_commit_window(
        monkeypatch,
        window=["S-mine"],
        own_run_sessions=RuntimeError("D1 timeout"),
    )

    rc = commit_session.main([
        "--run-started-at", "2026-07-26T08:46:03Z",
        "--no-claim-commit",
    ])

    assert rc == 1
    assert marked == []


def test_commit_session_run_lookup_failure_commits_explicit_then_fails(
    monkeypatch, capsys,
):
    """Explicit session still commits, but the step must go red.

    Both DailyIngestion and AdHocIngestion pass `--session-id` *and* the
    window flag, so this is the branch a transient D1 error actually takes
    in production. Exiting 0 with only an ERROR line would leave the run
    green while the unidentified sibling sessions stay `in_progress` and
    get rolled back by the 48h stale sweep. Committing the explicit session
    first keeps its writes (a `committed` session is shielded from the
    failure cleanup); the non-zero exit is what makes a human look.
    """
    monkeypatch.setenv("GITHUB_RUN_ID", "30195210787")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    marked = _stub_commit_window(
        monkeypatch,
        window=["S-mine"],
        own_run_sessions=RuntimeError("D1 timeout"),
    )

    rc = commit_session.main([
        "--session-id", "S-explicit",
        "--run-started-at", "2026-07-26T08:46:03Z",
        "--no-claim-commit",
    ])

    assert rc == 1
    assert marked == ["S-explicit"]
    assert '"ownership_lookup_failed": true' in capsys.readouterr().out
