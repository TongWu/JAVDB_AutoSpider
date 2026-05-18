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
    # --session-id is parsed as str post-2026-05-13 (TEXT snowflake PK).
    assert marked == ["7"]
    assert close_calls == [True]
