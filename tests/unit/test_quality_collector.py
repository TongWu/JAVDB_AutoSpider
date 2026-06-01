"""Tests for ADR-024 Phase 1 production-download evidence collection."""

from __future__ import annotations

import sys
from types import ModuleType
from types import SimpleNamespace

import javdb.quality.collector as collector_module
from javdb.quality.collector import (
    PRODUCTION_TARGET_ROLE,
    _build_context,
    collect_production_evidence,
    run_collection,
)

_ZERO_SUMMARY = collector_module._empty_summary()


class FakeQualityRepo:
    def __init__(self) -> None:
        self.evidence = []
        self.evaluations = []

    def upsert_evidence(self, record) -> None:
        self.evidence.append(record)

    def upsert_evaluation(self, record) -> None:
        self.evaluations.append(record)


def test_collects_evidence_and_evaluation_per_torrent():
    repo = FakeQualityRepo()
    torrent = {"hash": " HASH1 ", "name": "ABC-123-C 中文字幕"}
    files = [
        {"name": "ABC-123-C.mkv", "size": 5_000_000_000},
        {"name": "ABC-123-C.srt", "size": 60_000},
    ]

    summary = collect_production_evidence(
        torrents=[torrent],
        fetch_files=lambda info_hash: files if info_hash == "hash1" else None,
        repo=repo,
        context_for=lambda _torrent: {
            "movie_href": "/v/abc",
            "video_code": "ABC-123",
            "javdb_category": "subtitle",
            "magnet_name": torrent["name"],
            "javdb_tags": ["中文字幕"],
            "javdb_size_text": None,
        },
    )

    assert summary["evidence_written"] == 1
    assert summary["evaluations_written"] == 1
    assert summary["probe_unavailable"] == 0
    assert len(repo.evidence) == 1
    assert len(repo.evaluations) == 1

    evidence = repo.evidence[0]
    assert evidence.info_hash == "hash1"
    assert evidence.target_role == PRODUCTION_TARGET_ROLE
    assert evidence.metadata_status == "metadata_received"
    assert evidence.main_video_size_bytes == 5_000_000_000
    assert evidence.features == {"main_video_name": "ABC-123-C.mkv"}

    evaluation = repo.evaluations[0]
    assert evaluation.info_hash == "hash1"
    assert evaluation.movie_href == "/v/abc"
    assert evaluation.policy_mode == "shadow"
    assert evaluation.decision == "accepted_shadow"
    assert evaluation.shadow_rank is None
    assert evaluation.would_replace_current_choice is False


def test_records_probe_unavailable_when_metadata_missing():
    repo = FakeQualityRepo()

    summary = collect_production_evidence(
        torrents=[{"hash": "HASH1", "name": "ABC-123"}],
        fetch_files=lambda _info_hash: None,
        repo=repo,
        context_for=lambda _torrent: {},
    )

    assert summary["probe_unavailable"] == 1
    assert summary["evidence_written"] == 1
    assert summary["evaluations_written"] == 0
    assert len(repo.evidence) == 1
    assert repo.evidence[0].info_hash == "hash1"
    assert repo.evidence[0].metadata_status == "probe_unavailable"
    assert repo.evidence[0].reasons == ["probe_unavailable"]


def test_skips_torrents_without_hash():
    repo = FakeQualityRepo()

    summary = collect_production_evidence(
        torrents=[{"name": "ABC-123"}],
        fetch_files=lambda _info_hash: [],
        repo=repo,
        context_for=lambda _torrent: {},
    )

    assert summary["skipped"] == 1
    assert summary["evidence_written"] == 0
    assert repo.evidence == []
    assert repo.evaluations == []


def test_build_context_uses_acquisition_outcome_join():
    torrent = {"hash": "HASH1", "name": "ABC-123-C", "category": "qB category"}
    outcome = SimpleNamespace(
        href="/v/abc",
        video_code="ABC-123",
        category="subtitle",
    )

    context = _build_context(torrent, outcome)

    assert context == {
        "movie_href": "/v/abc",
        "video_code": "ABC-123",
        "javdb_category": "subtitle",
        "magnet_name": "ABC-123-C",
        "javdb_tags": [],
        "javdb_size_text": None,
    }


def test_build_context_without_outcome_does_not_use_qb_category():
    torrent = {"hash": "HASH1", "name": "ABC-123", "category": "qB category"}

    context = _build_context(torrent, None)

    assert context["movie_href"] == ""
    assert context["video_code"] is None
    assert context["javdb_category"] is None
    assert context["magnet_name"] == "ABC-123"


def test_run_collection_without_categories_returns_zero_without_qb_scan(monkeypatch):
    calls = []
    warnings = []
    fake_ff = ModuleType("service")
    fake_ff.initialize_proxy_helper = lambda *_args: calls.append("init")
    fake_ff.test_qbittorrent_connection = (
        lambda *_args: calls.append("connect") or True
    )
    fake_ff.get_recent_torrents = (
        lambda *_args, **_kwargs: calls.append("scan") or []
    )

    monkeypatch.setitem(sys.modules, "javdb.integrations.qb.file_filter.service", fake_ff)
    monkeypatch.setattr(
        collector_module.logger,
        "warning",
        lambda message, *args: warnings.append(message % args if args else message),
    )

    assert run_collection(categories=None) == _ZERO_SUMMARY
    assert calls == []
    assert any("no categories" in warning.lower() for warning in warnings)


def test_run_collection_wires_runtime_dependencies(monkeypatch):
    calls = []
    db_paths = []
    repos = []
    ops_path = "ops-db"
    reports_path = "reports-db"
    torrents = [{"hash": "HASH1", "name": "ABC-123-C 中文字幕"}]
    files = [
        {"name": "ABC-123-C.mkv", "size": 5_000_000_000},
        {"name": "ABC-123-C.srt", "size": 60_000},
    ]

    class FakeSession:
        def close(self) -> None:
            calls.append(("session.close",))

    class FakeDbContext:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            db_paths.append(self.path)
            return f"conn:{self.path}"

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeAcquisitionOutcomeRepo:
        def __init__(self, conn) -> None:
            calls.append(("acquisition_repo", conn))

        def get(self, qb_hash):
            calls.append(("acquisition_get", qb_hash))
            if qb_hash == "hash1":
                return SimpleNamespace(
                    href="/v/abc",
                    video_code="ABC-123",
                    category="subtitle",
                )
            return None

    class FakeTorrentQualityRepo(FakeQualityRepo):
        def __init__(self, conn) -> None:
            super().__init__()
            self.conn = conn
            repos.append(self)

    session = FakeSession()
    fake_requests = ModuleType("requests")
    fake_requests.Session = lambda: session

    fake_readonly = ModuleType("readonly")
    fake_readonly.wait_for_metadata_readiness = (
        lambda got_torrents, *, fetch_files: calls.append(
            ("metadata_wait", got_torrents)
        )
    )

    fake_ff = ModuleType("service")
    fake_ff.initialize_proxy_helper = (
        lambda use_proxy: calls.append(("init", use_proxy))
    )
    fake_ff.test_qbittorrent_connection = (
        lambda use_proxy: calls.append(("connect", use_proxy)) or True
    )
    fake_ff.login_to_qbittorrent = (
        lambda sess, use_proxy: calls.append(("login", sess, use_proxy)) or True
    )
    fake_ff.get_recent_torrents = (
        lambda sess, *, days, categories, use_proxy: calls.append(
            ("recent", sess, days, categories, use_proxy)
        )
        or torrents
    )
    fake_ff.get_torrent_files = (
        lambda sess, info_hash, use_proxy: calls.append(
            ("files", sess, info_hash, use_proxy)
        )
        or files
    )
    fake_ff.run_file_filter_api = lambda *_args, **_kwargs: None

    fake_db = ModuleType("db")
    fake_db.OPERATIONS_DB_PATH = ops_path
    fake_db.REPORTS_DB_PATH = reports_path
    fake_db.get_db = lambda path: FakeDbContext(path)

    fake_acquisition_repo = ModuleType("acquisition_outcome_repo")
    fake_acquisition_repo.AcquisitionOutcomeRepo = FakeAcquisitionOutcomeRepo
    fake_quality_repo = ModuleType("torrent_quality_repo")
    fake_quality_repo.TorrentQualityRepo = FakeTorrentQualityRepo
    fake_reconcile_service = ModuleType("service")
    fake_reconcile_service.apply_cleanup_completed = lambda *_args, **_kwargs: None
    fake_reconcile_service.record_queued = lambda *_args, **_kwargs: None
    fake_reconcile_service.run = lambda *_args, **_kwargs: None

    import javdb.integrations.qb as qb_package
    import javdb.integrations.qb.file_filter as file_filter_package

    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "javdb.integrations.qb.readonly", fake_readonly)
    monkeypatch.setattr(qb_package, "readonly", fake_readonly, raising=False)
    monkeypatch.setitem(
        sys.modules, "javdb.integrations.qb.file_filter.service", fake_ff
    )
    monkeypatch.setattr(file_filter_package, "service", fake_ff, raising=False)
    monkeypatch.setitem(sys.modules, "javdb.ops.reconcile.service", fake_reconcile_service)
    monkeypatch.setitem(sys.modules, "javdb.storage.db", fake_db)
    monkeypatch.setitem(
        sys.modules,
        "javdb.storage.repos.acquisition_outcome_repo",
        fake_acquisition_repo,
    )
    monkeypatch.setitem(
        sys.modules,
        "javdb.storage.repos.torrent_quality_repo",
        fake_quality_repo,
    )

    summary = run_collection(days=3, categories=["JavDB"], use_proxy=True)

    assert summary["evidence_written"] == 1
    assert summary["evaluations_written"] == 1
    assert calls[:4] == [
        ("init", True),
        ("connect", True),
        ("login", session, True),
        ("recent", session, 3, ["JavDB"], True),
    ]
    assert ("metadata_wait", torrents) in calls
    assert ("acquisition_get", "hash1") in calls
    assert db_paths == [ops_path, reports_path]
    assert len(repos) == 1
    assert repos[0].conn == f"conn:{reports_path}"
    assert len(repos[0].evidence) == 1
    assert len(repos[0].evaluations) == 1
    assert repos[0].evidence[0].info_hash == "hash1"
    assert repos[0].evaluations[0].info_hash == "hash1"
    assert repos[0].evaluations[0].movie_href == "/v/abc"
    assert repos[0].evaluations[0].javdb_category == "subtitle"
    assert ("files", session, "hash1", True) in calls
    assert ("session.close",) in calls
