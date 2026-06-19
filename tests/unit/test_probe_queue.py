"""ADR-024 IMP-10: bounded Top-K runner-up enqueue + gated capture."""

from __future__ import annotations

from javdb.quality.probe_queue import enqueue_runner_ups, maybe_capture_runner_ups


class _FakeRepo:
    def __init__(self):
        self.rows = []

    def enqueue(self, cand, *, enqueued_at):
        self.rows.append(cand)


def _m(name, href):
    return {"name": name, "tags": ["中文字幕"], "size": "5GB",
            "timestamp": "2026-06-10", "href": href, "file_count": 1}


def _two_subtitle_candidates():
    return [
        _m("best", "magnet:?xt=urn:btih:" + "a" * 40),
        _m("runner", "magnet:?xt=urn:btih:" + "b" * 40),
    ]


def test_enqueues_runner_ups_with_movie_context():
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("rb", "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567")],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo,
        context={"movie_href": "/v/abc", "video_code": "ABC-123"},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1
    row = repo.rows[0]
    assert row.movie_href == "/v/abc"
    assert row.javdb_category == "subtitle"
    assert row.info_hash == "0123456789abcdef0123456789abcdef01234567"


def test_global_cap_limits_total_enqueued():
    repo = _FakeRepo()
    many = [_m(f"r{i}", f"magnet:?xt=urn:btih:{i:040x}") for i in range(8)]
    runner_ups = {"subtitle": many, "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": "/v/abc"},
        global_cap=3, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 3
    assert len(repo.rows) == 3


def test_skips_magnet_without_btih():
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("bad", "magnet:?dn=no-hash")],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": "/v/abc"},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0


def test_skips_present_but_invalid_btih():
    # A btih that exists but is not a valid v1 hash (too short) must be dropped,
    # not enqueued as a junk info_hash.
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("short", "magnet:?xt=urn:btih:abc")],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": "/v/abc"},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []


def test_base32_btih_normalized_to_40_hex():
    import base64

    hexhash = "aabbccddeeff00112233445566778899aabbccdd"  # 40 hex = 20 bytes
    b32 = base64.b32encode(bytes.fromhex(hexhash)).decode()  # 32-char base32
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("b32", f"magnet:?xt=urn:btih:{b32}")],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": "/v/abc"},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1
    assert repo.rows[0].info_hash == hexhash  # normalized to lowercase hex


def test_skips_when_no_movie_href():
    repo = _FakeRepo()
    runner_ups = {"subtitle": [_m("r", "magnet:?xt=urn:btih:" + "a" * 40)],
                  "hacked_subtitle": [], "hacked_no_subtitle": [], "no_subtitle": []}
    n = enqueue_runner_ups(
        runner_ups, repo=repo, context={"movie_href": ""},
        global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0  # without a movie context the candidate can't be scored later


def test_maybe_capture_disabled_is_noop():
    repo = _FakeRepo()
    n = maybe_capture_runner_ups(
        _two_subtitle_candidates(), context={"movie_href": "/v/abc"},
        repo=repo, enabled=False, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 0 and repo.rows == []


def test_maybe_capture_enabled_enqueues_runner_up_only():
    repo = _FakeRepo()
    n = maybe_capture_runner_ups(
        _two_subtitle_candidates(), context={"movie_href": "/v/abc"},
        repo=repo, enabled=True, k=2, global_cap=10, enqueued_at="2026-06-19T00:00:00Z",
    )
    assert n == 1  # 2 candidates → 1 runner-up
    assert repo.rows[0].info_hash == "b" * 40
