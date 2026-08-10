"""Unit tests for the raw new-release counter used by dynamic pagination.

ADR-057 D1: the page-scan policy must steer on the site's own today/yesterday
badges, not on how many entries survive the phase gates — a page can be 100%
fresh and still select nothing.
"""

from javdb.parsing.models import IndexPageResult, MovieIndexEntry
from javdb.pipeline.index_selection import count_new_release_entries


def _page(*tag_lists, has_movie_list=True):
    return IndexPageResult(
        has_movie_list=has_movie_list,
        movies=[
            MovieIndexEntry(href=f'/v/x{i}', video_code=f'ABC-{i}', tags=list(tags))
            for i, tags in enumerate(tag_lists)
        ],
    )


def test_counts_traditional_chinese_badges():
    page = _page(['含中字磁鏈', '今日新種'], ['含磁鏈', '昨日新種'], ['含磁鏈'])
    assert count_new_release_entries(page) == 2


def test_counts_simplified_and_english_badges():
    page = _page(['今日新种'], ['Yesterday'], ['Today'], ['DL'])
    assert count_new_release_entries(page) == 3


def test_magnet_only_page_is_not_fresh():
    assert count_new_release_entries(_page(['含磁鏈'], ['CnSub DL'], ['無磁鏈'])) == 0


def test_entry_without_video_code_still_counts():
    # Selection skips code-less entries; the freshness signal must not, or a
    # page of unparsed codes would read as the end of the fresh block.
    page = IndexPageResult(
        has_movie_list=True,
        movies=[MovieIndexEntry(href='/v/x', video_code='', tags=['今日新種'])],
    )
    assert count_new_release_entries(page) == 1


def test_page_without_movie_list_is_zero():
    assert count_new_release_entries(_page(['今日新種'], has_movie_list=False)) == 0


def test_none_page_is_zero():
    assert count_new_release_entries(None) == 0
