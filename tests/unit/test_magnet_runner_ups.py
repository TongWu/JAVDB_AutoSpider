"""ADR-024 IMP-10: Top-K runner-up extraction must not change production selection."""

from __future__ import annotations

from javdb.parsing.magnet_categorize import (
    _bucket_magnets,
    _python_categorize,
    collect_runner_ups,
)


def _m(name, tags, size, ts, href):
    return {"name": name, "tags": tags, "size": size, "timestamp": ts,
            "href": href, "file_count": 1}


def _subtitle_set():
    # Two subtitle candidates; the newer/larger one is the production pick.
    return [
        _m("ABC-123-C big", ["中文字幕"], "8GB", "2026-06-10", "magnet:?xt=urn:btih:AAA"),
        _m("ABC-123-C small", ["中文字幕"], "3GB", "2026-06-09", "magnet:?xt=urn:btih:BBB"),
        _m("ABC-123-C old", ["中文字幕"], "5GB", "2026-06-01", "magnet:?xt=urn:btih:CCC"),
    ]


def test_production_selection_unchanged_by_runner_up_pass():
    magnets = _subtitle_set()
    before = _python_categorize([dict(m) for m in magnets])
    collect_runner_ups([dict(m) for m in magnets], k=2)  # must not mutate / matter
    after = _python_categorize([dict(m) for m in magnets])
    assert before == after
    # the production subtitle pick is the newest (sort: timestamp desc, size desc)
    assert after["subtitle"] == "magnet:?xt=urn:btih:AAA"


def test_runner_ups_exclude_the_selected_best_per_category():
    runner_ups = collect_runner_ups(_subtitle_set(), k=2)
    subs = runner_ups["subtitle"]
    hrefs = [m["href"] for m in subs]
    assert "magnet:?xt=urn:btih:AAA" not in hrefs  # AAA is the production pick
    assert hrefs == ["magnet:?xt=urn:btih:BBB", "magnet:?xt=urn:btih:CCC"]  # ranked order


def test_runner_ups_bounded_by_k():
    runner_ups = collect_runner_ups(_subtitle_set(), k=1)
    assert [m["href"] for m in runner_ups["subtitle"]] == ["magnet:?xt=urn:btih:BBB"]


def test_hacked_no_subtitle_runner_ups_suppressed_when_hacked_subtitle_present():
    # Mirrors production's elif exclusivity: when a hacked_subtitle pick exists,
    # production leaves hacked_no_subtitle empty, so it has no production pick to
    # find runner-ups against — collect_runner_ups must suppress it.
    magnets = [
        _m("X-UC big", [], "9GB", "2026-06-10", "magnet:?xt=urn:btih:HS1"),
        _m("X-U a", [], "8GB", "2026-06-10", "magnet:?xt=urn:btih:HN1"),
        _m("X-U b", [], "7GB", "2026-06-09", "magnet:?xt=urn:btih:HN2"),
    ]
    runner_ups = collect_runner_ups(magnets, k=2)
    assert runner_ups["hacked_no_subtitle"] == []
    # confirm production really does pick hacked_subtitle and leave the other empty
    result = _python_categorize([dict(m) for m in magnets])
    assert result["hacked_subtitle"] == "magnet:?xt=urn:btih:HS1"
    assert result["hacked_no_subtitle"] == ""


def test_hacked_no_subtitle_runner_ups_kept_when_no_hacked_subtitle():
    magnets = [
        _m("X-U a", [], "8GB", "2026-06-10", "magnet:?xt=urn:btih:HN1"),
        _m("X-U b", [], "7GB", "2026-06-09", "magnet:?xt=urn:btih:HN2"),
    ]
    runner_ups = collect_runner_ups(magnets, k=2)
    # no hacked_subtitle pick → hacked_no_subtitle keeps its runner-ups
    assert [m["href"] for m in runner_ups["hacked_no_subtitle"]] == ["magnet:?xt=urn:btih:HN2"]


def test_no_runner_ups_when_single_candidate():
    one = [_m("solo", ["中文字幕"], "4GB", "2026-06-10", "magnet:?xt=urn:btih:ZZZ")]
    runner_ups = collect_runner_ups(one, k=2)
    assert runner_ups["subtitle"] == []


def test_categories_present_even_when_empty():
    runner_ups = collect_runner_ups([], k=2)
    assert set(runner_ups) == {
        "hacked_subtitle", "hacked_no_subtitle", "subtitle", "no_subtitle",
    }
    assert all(v == [] for v in runner_ups.values())


# --- Drift guard: _bucket_magnets[cat][0] must equal _python_categorize's pick.
# These pin the two parallel bucketing implementations together so a future edit
# to either one that diverges fails loudly (the refactor kept production's
# _python_categorize untouched, so this equivalence is the only safety net).

def _selected_href(bucket, cat):
    return bucket[cat][0]["href"] if bucket[cat] else ""


def test_bucket_selection_matches_python_categorize_independent_categories():
    # One candidate per independent category (no hacked_no_subtitle, so the
    # production elif coupling between the two hacked keys does not interfere).
    magnets = [
        _m("ABC sub", ["中文字幕"], "8GB", "2026-06-10", "magnet:?xt=urn:btih:SUB"),
        _m("ABC-UC", [], "9GB", "2026-06-10", "magnet:?xt=urn:btih:HSUB"),
        _m("ABC 4k", [], "20GB", "2026-06-10", "magnet:?xt=urn:btih:NS4K"),
        _m("ABC plain", [], "6GB", "2026-06-10", "magnet:?xt=urn:btih:NSN"),
    ]
    result = _python_categorize([dict(m) for m in magnets])
    bucket = _bucket_magnets([dict(m) for m in magnets])
    for cat in ("subtitle", "hacked_subtitle", "no_subtitle"):
        assert result[cat] == _selected_href(bucket, cat), cat
    # no hacked_no_subtitle candidate in this fixture
    assert result["hacked_no_subtitle"] == ""
    assert bucket["hacked_no_subtitle"] == []
    # the 4K candidate wins no_subtitle
    assert result["no_subtitle"] == "magnet:?xt=urn:btih:NS4K"


def test_bucket_selection_matches_for_hacked_no_subtitle():
    # Only a hacked_no_subtitle candidate (-U, not -UC) present.
    magnets = [
        _m("XYZ-U", [], "5GB", "2026-06-10", "magnet:?xt=urn:btih:HNS"),
    ]
    result = _python_categorize([dict(m) for m in magnets])
    bucket = _bucket_magnets([dict(m) for m in magnets])
    assert result["hacked_no_subtitle"] == _selected_href(bucket, "hacked_no_subtitle")
    assert result["hacked_no_subtitle"] == "magnet:?xt=urn:btih:HNS"
    assert bucket["hacked_subtitle"] == []
    assert bucket["no_subtitle"] == []  # -U excludes it from no_subtitle
