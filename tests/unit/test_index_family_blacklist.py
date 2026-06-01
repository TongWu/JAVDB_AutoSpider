from __future__ import annotations

from javdb.parsing.models import MovieIndexEntry
from javdb.pipeline.index_family_blacklist import (
    normalize_family_blacklist,
    filter_blacklisted_families,
)


def _entry(code, family):
    return MovieIndexEntry(href=f"/v/{code}", video_code=code, video_code_family=family)


def test_normalize_trims_and_drops_empties():
    assert normalize_family_blacklist([" western_studio_date ", "", None]) == {"western_studio_date"}


def test_normalize_empty_opts_out():
    assert normalize_family_blacklist([]) == set()
    assert normalize_family_blacklist(None) == set()


def test_filter_drops_blacklisted_and_counts_once():
    movies = [
        _entry("Wifey.2026.05.30", "western_studio_date"),
        _entry("ABC-123", "classic_hyphenated"),
        _entry("259LUXU-1234", ""),
    ]
    counts = {}
    kept = filter_blacklisted_families(movies, {"western_studio_date"}, counts)

    assert [m.video_code for m in kept] == ["ABC-123", "259LUXU-1234"]
    assert counts == {"western_studio_date": 1}


def test_filter_empty_blacklist_keeps_everything():
    movies = [_entry("Wifey.2026.05.30", "western_studio_date")]
    counts = {}
    assert filter_blacklisted_families(movies, set(), counts) == movies
    assert counts == {}
