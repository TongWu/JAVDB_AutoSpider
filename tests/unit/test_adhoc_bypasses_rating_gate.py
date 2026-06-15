"""Pin: AdHoc selection bypasses the ADR-040 rating threshold.

ADR-054 WS2 supersedes ADR-040 Phase-3 subscriptions by reusing the AdHoc
spider path. In that path, phase-2 selection is tag-based and never applies
PHASE2_MIN_RATE / PHASE2_MIN_COMMENTS.
"""

from types import SimpleNamespace

from javdb.pipeline.index_selection import (
    PHASE2_MIN_COMMENTS,
    PHASE2_MIN_RATE,
    select_index_entries,
)


def _fake_entry(
    *, video_code: str, href: str, tags: list[str], rate: str, comment_count: str
) -> SimpleNamespace:
    return SimpleNamespace(
        video_code=video_code,
        href=href,
        tags=tags,
        rate=rate,
        comment_count=comment_count,
        to_legacy_dict=lambda: {"video_code": video_code, "href": href},
    )


def _page_result(entries: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(has_movie_list=True, movies=entries)


def test_adhoc_phase2_keeps_low_rate_magnet_entry():
    """A non-subtitle magnet entry below the rating gate survives adhoc mode."""
    low_rate = "1.0"
    low_comments = "3"
    assert float(low_rate) < PHASE2_MIN_RATE
    assert int(low_comments) < PHASE2_MIN_COMMENTS
    entry = _fake_entry(
        video_code="LOW-001",
        href="/v/low001",
        tags=["含磁鏈"],
        rate=low_rate,
        comment_count=low_comments,
    )
    page = _page_result([entry])

    kept_adhoc = select_index_entries(page, page_num=1, phase=2, is_adhoc_mode=True)

    assert [e["video_code"] for e in kept_adhoc] == ["LOW-001"]


def test_daily_phase2_drops_the_same_low_rate_entry():
    """The daily path drops it, proving the adhoc bypass is a real difference."""
    entry = _fake_entry(
        video_code="LOW-001",
        href="/v/low001",
        tags=["含磁鏈", "今日新種"],
        rate="1.0",
        comment_count="3",
    )
    page = _page_result([entry])

    kept_daily = select_index_entries(page, page_num=1, phase=2, is_adhoc_mode=False)

    assert kept_daily == []
