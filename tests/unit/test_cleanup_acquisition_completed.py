import pytest

from javdb.integrations.pikpak.bridge import service as pikpak_service
from javdb.ops.reconcile import service as reconcile_service
from javdb.ops.reconcile.models import AcquisitionOutcomeRecord


@pytest.fixture
def repo(acquisition_outcome_repo):
    return acquisition_outcome_repo


def test_apply_cleanup_completed_promotes_hashes_and_orphan_minimal_insert(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))

    result = reconcile_service.apply_cleanup_completed(
        {"hashes": ["h1", "h2"]},
        repo=repo,
    )

    got_h1 = repo.get("h1")
    got_h2 = repo.get("h2")

    assert got_h1 is not None
    assert got_h1.state == "completed"
    assert got_h2 is not None
    assert got_h2.state == "completed"
    assert got_h2.href == ""
    assert result.marked_completed == 2


class _FakeQBClient:
    def __init__(self, base_urls, username, password, use_proxy):
        self.base_urls = list(base_urls)
        self.username = username
        self.password = password
        self.use_proxy = use_proxy
        self.completed_requests = []
        self.delete_calls = []

    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        self.completed_requests.append((tuple(categories), torrent_filter))
        if torrent_filter != "completed":
            return []
        if list(categories) == list(pikpak_service.CATEGORIES):
            return [{"hash": "primary-hash", "name": "primary done"}]
        if list(categories) == [pikpak_service.TORRENT_CATEGORY_ADHOC]:
            return [{"hash": "adhoc-hash", "name": "adhoc done"}]
        return []

    def delete_torrents(self, hashes, delete_files=True):
        self.delete_calls.append((list(hashes), delete_files))
        return True


@pytest.mark.parametrize(
    "dry_run, expected_calls",
    [
        (False, [
            {"scanned": 1, "deleted": 1, "hashes": ["primary-hash"]},
            {"scanned": 1, "deleted": 1, "hashes": ["adhoc-hash"]},
        ]),
        (True, []),
    ],
)
def test_pikpak_bridge_only_pushes_cleanup_outcomes_when_not_dry_run(
    monkeypatch,
    dry_run,
    expected_calls,
):
    apply_calls = []

    monkeypatch.setattr(pikpak_service, "QBittorrentClient", _FakeQBClient)
    monkeypatch.setattr(pikpak_service, "initialize_proxy_helper", lambda *args, **kwargs: None)
    monkeypatch.setattr(pikpak_service, "should_proxy_module", lambda *args, **kwargs: False)
    monkeypatch.setattr(pikpak_service, "qb_base_url_candidates", lambda *args, **kwargs: ["http://qb.local"])
    monkeypatch.setattr(pikpak_service, "QB_URL_ADHOC", "http://adhoc.local")
    monkeypatch.setattr(pikpak_service, "QB_USERNAME_ADHOC", "adhoc-user")
    monkeypatch.setattr(pikpak_service, "QB_PASSWORD_ADHOC", "adhoc-pass")
    monkeypatch.setattr(pikpak_service, "_apply_cleanup_completed", lambda stats, repo=None: apply_calls.append(stats))

    result = pikpak_service._pikpak_bridge_impl(
        days=7,
        dry_run=dry_run,
        batch_mode=True,
        use_proxy=None,
        from_pipeline=False,
    )

    assert result["total_torrents"] == 0
    assert apply_calls == expected_calls
