"""ADR-005 PR-3a caller migrations route through Repo classes."""

from argparse import Namespace
from unittest.mock import MagicMock

import pytest


def _raw_db_forbidden(name):
    def _raise(*args, **kwargs):
        raise AssertionError(f"raw db function called: {name}")

    return _raise


def test_history_manager_sqlite_paths_use_history_repo(monkeypatch):
    import javdb.storage.history_manager as hm

    repo = MagicMock()
    repo.load_history.return_value = {"/v/A": {"VideoCode": "A"}}
    repo.upsert_history.return_value = None
    repo.batch_update_last_visited.return_value = 2
    repo.check_torrent_in_history.return_value = True
    repo_cls = MagicMock(return_value=repo)

    monkeypatch.setattr(hm, "use_sqlite", lambda: True)
    monkeypatch.setattr(hm, "use_csv", lambda: False)
    monkeypatch.setattr(hm, "_ensure_db", lambda: None)
    monkeypatch.setattr(hm, "HistoryRepo", repo_cls, raising=False)

    import javdb.storage.db.db_history_read as read_db
    import javdb.storage.db.db_history_write as write_db
    import javdb.storage.db.db_session as session_db

    monkeypatch.setattr(
        read_db, "db_load_history", _raw_db_forbidden("db_load_history")
    )
    monkeypatch.setattr(
        write_db, "db_upsert_history", _raw_db_forbidden("db_upsert_history")
    )
    monkeypatch.setattr(
        write_db,
        "db_stage_history_write",
        _raw_db_forbidden("db_stage_history_write"),
    )
    monkeypatch.setattr(
        read_db,
        "db_batch_update_last_visited",
        _raw_db_forbidden("db_batch_update_last_visited"),
    )
    monkeypatch.setattr(
        read_db,
        "db_check_torrent_in_history",
        _raw_db_forbidden("db_check_torrent_in_history"),
    )
    monkeypatch.setattr(session_db, "get_active_session_id", lambda: "sess-audit")
    monkeypatch.setattr(session_db, "get_active_write_mode", lambda: "audit")

    assert hm.load_parsed_movies_history("history.csv", phase=1) == {
        "/v/A": {"VideoCode": "A"}
    }
    hm.save_parsed_movie_to_history(
        "history.csv",
        "/v/A",
        1,
        "A",
        {"subtitle": "magnet:?xt=urn:btih:sub"},
        size_links={"subtitle": "1 GiB"},
        file_count_links={"subtitle": 2},
        resolution_links={"subtitle": "1080p"},
        actor_name="Actor",
        actor_gender="F",
        actor_link="/actors/a",
        supporting_actors="[]",
    )
    hm.batch_update_last_visited("history.csv", {"/v/A", "/v/B"})
    assert hm.check_torrent_in_history("history.csv", "/v/A", "subtitle") is True

    assert repo_cls.call_count == 4
    repo.load_history.assert_called_once_with(phase=1)
    repo.upsert_history.assert_called_once()
    repo.batch_update_last_visited.assert_called_once()
    repo.check_torrent_in_history.assert_called_once_with("/v/A", "subtitle")


def test_history_manager_pending_writes_use_history_repo_staging(monkeypatch):
    import javdb.storage.history_manager as hm

    repo = MagicMock()
    repo_cls = MagicMock(return_value=repo)

    monkeypatch.setattr(hm, "use_sqlite", lambda: True)
    monkeypatch.setattr(hm, "use_csv", lambda: False)
    monkeypatch.setattr(hm, "_ensure_db", lambda: None)
    monkeypatch.setattr(hm, "HistoryRepo", repo_cls, raising=False)

    import javdb.storage.db.db_history_write as write_db
    import javdb.storage.db.db_session as session_db

    monkeypatch.setattr(
        write_db,
        "db_stage_history_write",
        _raw_db_forbidden("db_stage_history_write"),
    )
    monkeypatch.setattr(
        write_db, "db_upsert_history", _raw_db_forbidden("db_upsert_history")
    )
    monkeypatch.setattr(session_db, "get_active_session_id", lambda: "sess-pending")
    monkeypatch.setattr(session_db, "get_active_write_mode", lambda: "pending")

    hm.save_parsed_movie_to_history(
        "history.csv",
        "/v/P",
        2,
        "P",
        {"subtitle": "magnet:?xt=urn:btih:p"},
    )

    repo.stage_movie.assert_called_once()
    assert repo.stage_torrent.call_count == 2
    repo.upsert_history.assert_not_called()


def test_detail_runner_finalize_uses_history_repo_for_actor_updates(monkeypatch):
    import javdb.spider.detail.runner as runner

    repo = MagicMock()
    repo_cls = MagicMock(return_value=repo)
    actor_updates = [("/v/A", "Actor", "F", "/actors/a", "[]")]

    monkeypatch.setattr(runner, "use_sqlite", lambda: True)
    monkeypatch.setattr(runner, "HistoryRepo", repo_cls, raising=False)
    monkeypatch.setattr(
        runner,
        "db_batch_update_movie_actors",
        _raw_db_forbidden("db_batch_update_movie_actors"),
        raising=False,
    )
    batch_last_visited = MagicMock()
    monkeypatch.setattr(runner, "batch_update_last_visited", batch_last_visited)

    runner.finalize_detail_phase(
        use_history_for_saving=True,
        dry_run=False,
        history_file="history.csv",
        visited_hrefs={"/v/A"},
        actor_updates=actor_updates,
    )

    repo.batch_update_movie_actors.assert_called_once_with(actor_updates)
    batch_last_visited.assert_called_once_with("history.csv", {"/v/A"})


def _patch_legacy_actor_update_dependencies(monkeypatch, legacy, repo_cls):
    monkeypatch.setattr(legacy, "use_sqlite", lambda: True)
    monkeypatch.setattr(legacy, "HistoryRepo", repo_cls)
    monkeypatch.setattr(legacy, "has_complete_subtitles", lambda *_: False)
    monkeypatch.setattr(legacy, "extract_magnets", lambda *_: {})
    monkeypatch.setattr(legacy, "should_process_movie", lambda *_: (False, []))
    monkeypatch.setattr(legacy, "parsed_links", set())
    monkeypatch.setattr(
        legacy,
        "db_batch_update_movie_actors",
        _raw_db_forbidden("db_batch_update_movie_actors"),
        raising=False,
    )


def test_legacy_sequential_actor_updates_use_history_repo(monkeypatch):
    import javdb.storage.db.db as db_facade

    monkeypatch.setattr(
        db_facade,
        "db_batch_update_movie_actors",
        _raw_db_forbidden("db_batch_update_movie_actors"),
    )

    import javdb.legacy._spider_legacy as legacy

    repo = MagicMock()
    repo_cls = MagicMock(return_value=repo)
    _patch_legacy_actor_update_dependencies(monkeypatch, legacy, repo_cls)
    monkeypatch.setattr(
        legacy,
        "fetch_detail_page_with_fallback",
        lambda *_, **__: (
            ["magnet"],
            "Actor",
            "F",
            "/actors/a",
            "[]",
            True,
            False,
            False,
        ),
    )

    legacy.process_phase_entries_sequential(
        entries=[{"href": "/v/L1", "page": 1, "video_code": "L1"}],
        phase=1,
        history_data={},
        history_file="history.csv",
        csv_path="out.csv",
        fieldnames=[],
        dry_run=False,
        use_history_for_saving=True,
        use_cookie=False,
        is_adhoc_mode=False,
        session=object(),
        use_proxy=False,
        use_cf_bypass=False,
    )

    repo.batch_update_movie_actors.assert_called_once_with(
        [("/v/L1", "Actor", "F", "/actors/a", "[]")]
    )


def test_legacy_parallel_actor_updates_use_history_repo(monkeypatch):
    import javdb.storage.db.db as db_facade

    monkeypatch.setattr(
        db_facade,
        "db_batch_update_movie_actors",
        _raw_db_forbidden("db_batch_update_movie_actors"),
    )

    import javdb.legacy._spider_legacy as legacy

    repo = MagicMock()
    repo_cls = MagicMock(return_value=repo)
    _patch_legacy_actor_update_dependencies(monkeypatch, legacy, repo_cls)
    monkeypatch.setattr(legacy, "PROXY_POOL", [{"name": "test-proxy"}])

    class FakeWorker:
        def __init__(self, *_, detail_queue, result_queue, **__):
            self.detail_queue = detail_queue
            self.result_queue = result_queue

        def start(self):
            task = self.detail_queue.get_nowait()
            self.result_queue.put(
                legacy.DetailResult(
                    task=task,
                    magnets=["magnet"],
                    actor_info="Actor",
                    actor_gender="F",
                    actor_link="/actors/a",
                    supporting_actors="[]",
                    parse_success=True,
                    used_cf_bypass=False,
                )
            )

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(legacy, "ProxyWorker", FakeWorker)

    legacy.process_detail_entries_parallel(
        entries=[{"href": "/v/L2", "page": 1, "video_code": "L2"}],
        phase=1,
        history_data={},
        history_file="history.csv",
        csv_path="out.csv",
        fieldnames=[],
        dry_run=False,
        use_history_for_saving=True,
        use_cookie=False,
        is_adhoc_mode=False,
    )

    repo.batch_update_movie_actors.assert_called_once_with(
        [("/v/L2", "Actor", "F", "/actors/a", "[]")]
    )


def test_dedup_sqlite_paths_use_operations_repo(monkeypatch):
    import javdb.spider.services.dedup as dedup

    repo = MagicMock()
    repo.load_rclone_inventory.return_value = {
        "abc-123": [
            {
                "VideoCode": "abc-123",
                "SensorCategory": "censored",
                "SubtitleCategory": "subtitle",
                "FolderPath": "2026/ABC-123",
                "FolderSize": 10,
                "FileCount": 1,
                "DateTimeScanned": "2026-01-01 00:00:00",
            }
        ]
    }
    repo.load_dedup_records.return_value = []
    repo.append_dedup_record.return_value = 7
    repo.mark_records_deleted.return_value = 2
    repo.cleanup_deleted_records.return_value = 3
    repo_cls = MagicMock(return_value=repo)

    monkeypatch.setattr(dedup, "use_sqlite", lambda: True)
    monkeypatch.setattr(dedup, "use_csv", lambda: False)
    monkeypatch.setattr(dedup, "_ensure_db", lambda: None)
    monkeypatch.setattr(dedup, "OperationsRepo", repo_cls, raising=False)
    monkeypatch.setattr(dedup, "_pending_paths_cache", None)

    import javdb.storage.db.db_operations as ops_db

    for name in (
        "db_load_rclone_inventory",
        "db_load_dedup_records",
        "db_append_dedup_record",
        "db_mark_records_deleted",
        "db_cleanup_deleted_records",
        "db_save_dedup_records",
    ):
        monkeypatch.setattr(ops_db, name, _raw_db_forbidden(name))

    inventory = dedup.load_rclone_inventory("rclone.csv")
    assert list(inventory) == ["ABC-123"]

    assert dedup.load_dedup_csv("dedup.csv") == []

    record = dedup.DedupRecord(
        video_code="ABC-123",
        existing_sensor="censored",
        existing_subtitle="subtitle",
        existing_gdrive_path="remote:/ABC-123",
        existing_folder_size=10,
        new_torrent_category="hacked_subtitle",
        deletion_reason="upgrade",
        detect_datetime="2026-01-01 00:00:00",
        is_deleted="False",
        delete_datetime="",
    )
    assert dedup.append_dedup_record("dedup.csv", record) is True
    assert dedup.mark_records_deleted(
        "dedup.csv", [("remote:/ABC-123", "2026-01-02 00:00:00")]
    ) == 2
    assert dedup.cleanup_deleted_records("dedup.csv", older_than_days=14) == 3
    dedup.save_dedup_csv("dedup.csv", [{"video_code": "ABC-123"}])
    assert dedup.export_dedup_db_to_csv("out.csv") == 0

    repo.load_rclone_inventory.assert_called_once_with()
    repo.load_dedup_records.assert_called()
    repo.append_dedup_record.assert_called_once_with(record._asdict())
    repo.mark_records_deleted.assert_called_once_with(
        [("remote:/ABC-123", "2026-01-02 00:00:00")]
    )
    repo.cleanup_deleted_records.assert_called_once_with(14)
    repo.save_dedup_records.assert_called_once_with([{"video_code": "ABC-123"}])


def test_run_service_main_saves_spider_stats_through_stats_repo(monkeypatch, tmp_path):
    import javdb.spider.app.run_service as run_service

    args = Namespace(
        start_page=1,
        end_page=1,
        phase="none",
        url=None,
        dry_run=True,
        ignore_history=True,
        use_history=False,
        all=False,
        ignore_release_date=True,
        use_proxy=None,
        no_proxy=False,
        always_bypass_time=None,
        max_movies_phase1=None,
        max_movies_phase2=None,
        sequential=True,
        enable_dedup=False,
        no_rclone_filter=True,
        enable_redownload=False,
        redownload_threshold=None,
        disable_all_filters=True,
        output_file="out.csv",
    )
    repo = MagicMock()
    repo_cls = MagicMock(return_value=repo)

    monkeypatch.setattr(run_service, "parse_arguments", lambda: args)
    monkeypatch.setattr(run_service, "resolve_proxy_override", lambda *_: None)
    monkeypatch.setattr(run_service, "should_proxy_module", lambda *_, **__: False)
    monkeypatch.setattr(run_service, "describe_proxy_override", lambda *_: "off")
    monkeypatch.setattr(run_service.state, "setup_proxy_pool", lambda *_: None)
    monkeypatch.setattr(run_service.state, "initialize_request_handler", lambda: None)
    monkeypatch.setattr(
        run_service.state,
        "ensure_report_dated_dir",
        lambda *_: str(tmp_path),
    )
    monkeypatch.setattr(run_service.state, "ensure_reports_dir", lambda: None)
    monkeypatch.setattr(run_service, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(run_service, "StatsRepo", repo_cls, raising=False)
    monkeypatch.setattr(
        run_service,
        "fetch_all_index_pages",
        lambda **_: {
            "all_index_results_phase1": [],
            "all_index_results_phase2": [],
            "any_proxy_banned": False,
            "use_proxy": False,
            "use_cf_bypass": False,
            "csv_path": str(tmp_path / "out.csv"),
            "last_valid_page": None,
        },
    )
    monkeypatch.setattr(run_service, "generate_summary_report", lambda **_: None)
    monkeypatch.setattr(run_service, "set_active_session", lambda *_: None)

    import javdb.infra.config as config
    import javdb.storage.db.db_connection as db_connection
    import javdb.storage.db.db_migrations as db_migrations
    import javdb.storage.db.db_reports as db_reports
    import javdb.storage.db.db_session as db_session
    import javdb.storage.db.db_stats as db_stats

    monkeypatch.setattr(config, "use_db_storage", lambda: True)
    monkeypatch.setattr(db_migrations, "init_db", lambda force=False: None)
    monkeypatch.setattr(db_connection, "verify_d1_schema_versions", lambda: None)
    monkeypatch.setattr(db_reports, "db_create_report_session", lambda **_: "sess-1")
    monkeypatch.setattr(
        db_reports, "db_find_in_progress_session_ids_for_run_csv", lambda *_: []
    )
    monkeypatch.setattr(db_reports, "db_get_session_status", lambda *_: ("audit",))
    monkeypatch.setattr(db_session, "_resolve_write_mode", lambda *_: "audit")
    monkeypatch.setattr(db_session, "set_active_session_id", lambda *_: None)
    monkeypatch.setattr(db_session, "set_active_run_identity", lambda *_: None)
    monkeypatch.setattr(db_session, "set_active_write_mode", lambda *_: None)
    monkeypatch.setattr(
        db_stats,
        "db_save_spider_stats",
        _raw_db_forbidden("db_save_spider_stats"),
    )

    run_service._main()

    repo.save_spider_stats.assert_called_once()
    assert repo.save_spider_stats.call_args.args[0] == "sess-1"
    assert repo.save_spider_stats.call_args.args[1]["total_discovered"] == 0


def test_history_repo_exposes_audit_upsert_wrapper(monkeypatch):
    from javdb.storage.repos.history_repo import HistoryRepo
    import javdb.storage.db.db_history_write as write_db

    mock_upsert = MagicMock(return_value=11)
    monkeypatch.setattr(write_db, "db_upsert_history", mock_upsert)

    repo = HistoryRepo(db_path="/tmp/history.db")
    result = repo.upsert_history(
        "/v/A",
        "A",
        {"subtitle": "magnet:?xt=urn:btih:a"},
        size_links={"subtitle": "1 GiB"},
        file_count_links={"subtitle": 1},
        resolution_links={"subtitle": "1080p"},
        actor_name="Actor",
        actor_gender="F",
        actor_link="/actors/a",
        supporting_actors="[]",
    )

    assert result == 11
    mock_upsert.assert_called_once_with(
        "/v/A",
        "A",
        {"subtitle": "magnet:?xt=urn:btih:a"},
        size_links={"subtitle": "1 GiB"},
        file_count_links={"subtitle": 1},
        resolution_links={"subtitle": "1080p"},
        actor_name="Actor",
        actor_gender="F",
        actor_link="/actors/a",
        supporting_actors="[]",
        db_path="/tmp/history.db",
    )
