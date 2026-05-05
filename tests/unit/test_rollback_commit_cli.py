from apps.cli import commit_session, rollback


def test_rollback_defaults_to_dry_run_and_apply_opts_in():
    assert rollback._parse_args(["--session-id", "1"]).dry_run is True
    assert rollback._parse_args(["--session-id", "1", "--dry-run"]).dry_run is True
    assert rollback._parse_args(["--session-id", "1", "--apply"]).dry_run is False


def test_rollback_returns_partial_failure_on_real_drift(monkeypatch, capsys):
    monkeypatch.setattr(rollback, "init_db", lambda: None)
    monkeypatch.setattr(rollback, "close_db", lambda: None)
    monkeypatch.setattr(rollback, "_resolve_target_sessions", lambda _args: [7])
    monkeypatch.setattr(
        rollback,
        "db_rollback_session",
        lambda *_args, **_kwargs: {"history": {"drift_skipped": 1}},
    )

    rc = rollback.main(["--session-id", "7", "--apply"])

    assert rc == 4
    assert '"drift_total": 1' in capsys.readouterr().out


def test_commit_session_explicit_id_survives_window_lookup_failure(monkeypatch):
    marked = []
    close_calls = []

    monkeypatch.setattr(commit_session, "init_db", lambda: None)
    monkeypatch.setattr(commit_session, "close_db", lambda: close_calls.append(True))
    monkeypatch.setattr(
        commit_session,
        "db_find_in_progress_sessions",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("lookup failed")),
    )
    monkeypatch.setattr(
        commit_session,
        "db_mark_session_committed",
        lambda sid: marked.append(sid) or 1,
    )

    rc = commit_session.main([
        "--session-id", "7",
        "--run-started-at", "2026-05-04T00:00:00Z",
    ])

    assert rc == 0
    assert marked == [7]
    assert close_calls == [True]
