"""ADR-046 Phase 1: the detail-phase + history-manager boundaries bind the
explicit session onto the HistoryRepo they construct."""


def test_finalize_detail_phase_binds_session_to_repo(monkeypatch):
    import javdb.spider.detail.runner as runner

    captured = {}

    class FakeRepo:
        def __init__(self, *, db_path=None, session_id=None):
            captured["actors_session"] = session_id

        def batch_update_movie_actors(self, updates):
            return len(updates)

    monkeypatch.setattr(runner, "HistoryRepo", FakeRepo)
    monkeypatch.setattr(runner, "use_sqlite", lambda: True)
    monkeypatch.setattr(
        runner, "batch_update_last_visited",
        lambda history_file, visited, *, session_id: captured.update(
            visited_session=session_id
        ),
    )

    runner.finalize_detail_phase(
        use_history_for_saving=True,
        dry_run=False,
        history_file="x.csv",
        visited_hrefs={"https://javdb.com/v/A"},
        actor_updates=[("https://javdb.com/v/A", "Actor", "female", "/a/x", "")],
        session_id="SID-RUNNER",
    )

    assert captured["actors_session"] == "SID-RUNNER"
    assert captured["visited_session"] == "SID-RUNNER"


def test_history_manager_batch_update_last_visited_binds_session(monkeypatch):
    import javdb.storage.history_manager as hm

    captured = {}

    class FakeRepo:
        def __init__(self, *, db_path=None, session_id=None):
            captured["session_id"] = session_id

        def batch_update_last_visited(self, hrefs):
            return len(list(hrefs))

    monkeypatch.setattr(hm, "HistoryRepo", FakeRepo)
    monkeypatch.setattr(hm, "use_sqlite", lambda: True)
    monkeypatch.setattr(hm, "use_csv", lambda: False)
    monkeypatch.setattr(hm, "_ensure_db", lambda: None)

    hm.batch_update_last_visited(
        "x.csv", {"https://javdb.com/v/A"}, session_id="SID-HM",
    )
    assert captured["session_id"] == "SID-HM"


def test_csv_mode_batch_update_last_visited_accepts_session_id(monkeypatch, tmp_path):
    """ADR-046 regression: in CSV mode the module-level batch_update_last_visited
    is the Rust override; it must still accept the keyword-only session_id
    (ignored — CSV has no sessions) so finalize_detail_phase doesn't TypeError."""
    import importlib
    import javdb.storage.history_manager as hm

    # Force pure-CSV mode for the reload so `if not use_sqlite()` installs the
    # Rust override. The JAVDB_FORBID_DB_WRITES kill switch makes storage_mode()
    # return 'csv' *before* it consults _storage_mode_override, so it overrides
    # the autouse _isolate_sqlite fixture (which pins the override to 'db').
    monkeypatch.setenv("JAVDB_FORBID_DB_WRITES", "1")
    importlib.reload(hm)
    try:
        # Reload genuinely entered CSV mode and installed the Rust-backed
        # override (skip the assertion if the Rust wheel is unavailable, in
        # which case the pure-Python version already accepts session_id).
        assert hm.use_sqlite() is False
        if hm.RUST_HISTORY_AVAILABLE:
            assert hm.batch_update_last_visited.__module__ == hm.__name__
        # empty set → no-op; must NOT raise TypeError on the session_id kwarg
        hm.batch_update_last_visited(str(tmp_path / "h.csv"), set(), session_id="s")
    finally:
        # restore default-mode binding (Python impl) for other tests
        monkeypatch.undo()
        importlib.reload(hm)
