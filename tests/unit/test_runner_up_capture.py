"""ADR-024 IMP-10: spider-seam runner-up capture adapter."""

from __future__ import annotations

from javdb.spider.detail.runner import _capture_runner_ups_for_result


class _Repo:
    def __init__(self):
        self.rows = []

    def enqueue(self, cand, *, enqueued_at):
        self.rows.append(cand)


class _Detail:
    """Fake parsed detail exposing the legacy magnet list."""

    def __init__(self, magnets):
        self._magnets = magnets

    def get_magnets_as_legacy(self):
        return self._magnets


def _m(name, href):
    return {"name": name, "tags": ["中文字幕"], "size": "5GB",
            "timestamp": "2026-06-10", "href": href, "file_count": 1}


def _result():
    return {
        "movie_detail": _Detail([
            _m("best", "magnet:?xt=urn:btih:" + "a" * 40),
            _m("runner", "magnet:?xt=urn:btih:" + "b" * 40),
        ]),
    }


def test_disabled_captures_nothing():
    repo = _Repo()
    n = _capture_runner_ups_for_result(
        _result(), href="/v/abc", video_code="ABC-123",
        repo=repo, enabled=False, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []


def test_enabled_enqueues_runner_up_with_context():
    repo = _Repo()
    n = _capture_runner_ups_for_result(
        _result(), href="/v/abc", video_code="ABC-123",
        repo=repo, enabled=True, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1  # 2 candidates → 1 runner-up
    row = repo.rows[0]
    assert row.info_hash == "b" * 40
    assert row.movie_href == "/v/abc"
    assert row.video_code == "ABC-123"


def test_missing_movie_detail_is_safe_noop():
    repo = _Repo()
    n = _capture_runner_ups_for_result(
        {}, href="/v/abc", video_code="ABC-123",
        repo=repo, enabled=True, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []
