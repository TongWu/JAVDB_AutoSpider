"""Pin the category <-> indicator mapping in javdb.spider.contracts.

This mapping is the single source of truth for converting between legacy
torrent category names and the (SubtitleIndicator, CensorIndicator) integer
pair persisted in MovieHistory / TorrentHistory / ReportTorrents. Three
storage modules (_db_history_write, _db_history_read, _db_reports) previously
re-derived the reverse direction by hand; they now import the functions below.
These tests guard the contract so a future edit to the map cannot silently
diverge a caller's view of a category.
"""

import pytest

from javdb.spider.contracts import (
    CATEGORY_TO_INDICATORS,
    INDICATORS_TO_CATEGORY,
    TORRENT_CATEGORIES,
    category_to_indicators,
    indicators_to_category,
)


# Explicit wire contract: category name -> (SubtitleIndicator, CensorIndicator).
# These pairs are written to INTEGER columns, so any drift here is a data bug.
_KNOWN_PAIRS = {
    "hacked_subtitle": (1, 0),
    "hacked_no_subtitle": (0, 0),
    "subtitle": (1, 1),
    "no_subtitle": (0, 1),
}


@pytest.mark.parametrize("category, indicators", _KNOWN_PAIRS.items())
def test_forward_mapping_is_pinned(category, indicators):
    assert category_to_indicators(category) == indicators


@pytest.mark.parametrize("category, indicators", _KNOWN_PAIRS.items())
def test_reverse_mapping_is_pinned(category, indicators):
    assert indicators_to_category(*indicators) == category


@pytest.mark.parametrize("category", _KNOWN_PAIRS)
def test_round_trip_category_to_indicators_to_category(category):
    """category -> indicators -> category is the identity for every category."""
    assert indicators_to_category(*category_to_indicators(category)) == category


def test_all_torrent_categories_are_mapped():
    """Every declared torrent category has a forward mapping entry."""
    assert set(TORRENT_CATEGORIES) == set(_KNOWN_PAIRS)
    assert set(CATEGORY_TO_INDICATORS) == set(_KNOWN_PAIRS)


def test_reverse_dict_is_exact_inverse_of_forward_dict():
    assert INDICATORS_TO_CATEGORY == {v: k for k, v in CATEGORY_TO_INDICATORS.items()}
    # And bijective: no two categories collapse onto the same indicator pair.
    assert len(INDICATORS_TO_CATEGORY) == len(CATEGORY_TO_INDICATORS)


def test_forward_default_for_unknown_category():
    """Unknown category falls back to (0, 1) — i.e. plain no_subtitle."""
    assert category_to_indicators("not_a_category") == (0, 1)


@pytest.mark.parametrize("pair", [(2, 5), (9, 9), (-1, 0), (0, 7)])
def test_reverse_default_for_unknown_indicator_pair(pair):
    """Any out-of-domain indicator pair falls back to 'no_subtitle'.

    This matches the old hand-unrolled if/elif's else branch exactly.
    """
    assert indicators_to_category(*pair) == "no_subtitle"
