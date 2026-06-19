"""ADR-024 IMP-10: remote probe lifecycle orchestration."""

from __future__ import annotations

from javdb.quality.probe_runner import probe_candidates
from javdb.storage.repos.torrent_probe_repo import ProbeCandidate


class _Repo:
    def __init__(self, pending):
        self._pending = pending
        self.status = {}

    def list_pending(self, *, limit=None):
        return self._pending[: limit or len(self._pending)]

    def mark_status(self, info_hash, movie_href, status, *, probed_at):
        self.status[(info_hash, movie_href)] = status


class _EvidenceRepo:
    def __init__(self):
        self.rows = []

    def upsert_evidence(self, rec):
        self.rows.append(rec)


class _Client:
    """Fake qB probe client. ``files_by_hash`` drives poll outcomes."""

    def __init__(self, files_by_hash, supports=True):
        self._files = files_by_hash
        self._supports = supports
        self.added = []
        self.deleted = []

    def supports_metadata_only_probe(self):
        return self._supports

    def add_torrent(self, magnet, name=None, category=None, paused=False, stop_condition=None):
        self.added.append((magnet, category, stop_condition, paused))
        return True

    def get_torrent_files(self, info_hash):
        return self._files.get(info_hash)

    def delete_torrents(self, hashes, delete_files=True):
        self.deleted.append((tuple(hashes), delete_files))
        return True


def _cand(h):
    return ProbeCandidate(info_hash=h, movie_href="/v/abc",
                          magnet_uri=f"magnet:?xt=urn:btih:{h}", javdb_category="subtitle")


def test_happy_path_collects_evidence_and_deletes_keep_files():
    files = {"HASH1": [{"name": "ABC.mkv", "size": 5_000_000_000, "priority": 1}]}
    client = _Client(files)
    qrepo = _Repo([_cand("HASH1")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )

    assert summary["probed"] == 1
    assert client.added[0][1] == "JavDB Quality Shadow"
    assert client.added[0][2] == "MetadataReceived"
    # D7: must be active (paused=False) or qB never fetches metadata and the
    # stop condition never fires.
    assert client.added[0][3] is False
    assert client.deleted == [(("HASH1",), False)]  # deleteFiles=false
    ev = erepo.rows[0]
    assert ev.target_role == "quality_probe"
    assert ev.metadata_status == "metadata_received"
    assert qrepo.status[("HASH1", "/v/abc")] == "probed"


def test_capability_unsupported_fails_closed():
    client = _Client({}, supports=False)
    qrepo = _Repo([_cand("HASH1")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )

    assert summary["capability_unsupported"] == 1
    assert client.added == []  # never added a torrent
    assert erepo.rows[0].metadata_status == "probe_capability_unsupported"


def test_timeout_records_pending_timeout_and_deletes():
    client = _Client({})  # files never arrive
    qrepo = _Repo([_cand("HASH1")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=2,
    )

    assert summary["timeout"] == 1
    assert client.deleted == [(("HASH1",), False)]  # cleaned up even on timeout
    assert erepo.rows[0].metadata_status == "pending_timeout"
    assert qrepo.status[("HASH1", "/v/abc")] == "failed"


def test_no_pending_is_a_noop():
    client = _Client({})
    summary = probe_candidates(
        client=client, queue_repo=_Repo([]), evidence_repo=_EvidenceRepo(),
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )
    assert summary == {"probed": 0, "timeout": 0, "capability_unsupported": 0,
                       "scanned": 0, "errors": 0}


def test_one_candidate_failure_does_not_abort_batch_and_still_cleans_up():
    """A candidate that raises mid-probe is isolated: it is marked failed and its
    probe torrent is still deleted, and later candidates are still processed."""

    class _FlakyClient:
        def __init__(self):
            self.added = []
            self.deleted = []

        def supports_metadata_only_probe(self):
            return True

        def add_torrent(self, magnet, name=None, category=None, paused=False, stop_condition=None):
            self.added.append(magnet)
            return True

        def get_torrent_files(self, info_hash):
            if info_hash == "BAD":
                raise RuntimeError("qB blew up for this candidate")
            return [{"name": "ok.mkv", "size": 4_000_000_000, "priority": 1}]

        def delete_torrents(self, hashes, delete_files=True):
            self.deleted.append((tuple(hashes), delete_files))
            return True

    client = _FlakyClient()
    qrepo = _Repo([_cand("BAD"), _cand("GOOD")])
    erepo = _EvidenceRepo()

    summary = probe_candidates(
        client=client, queue_repo=qrepo, evidence_repo=erepo,
        now="2026-06-19T00:00:00Z", poll=lambda: None, max_polls=1,
    )

    assert summary["scanned"] == 2
    assert summary["errors"] == 1      # BAD isolated
    assert summary["probed"] == 1      # GOOD still processed
    assert qrepo.status[("BAD", "/v/abc")] == "failed"
    assert qrepo.status[("GOOD", "/v/abc")] == "probed"
    # both probe torrents removed (keep files = False), even the failed one
    assert (("BAD",), False) in client.deleted
    assert (("GOOD",), False) in client.deleted
