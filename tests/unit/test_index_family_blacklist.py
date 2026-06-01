from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from javdb.parsing.models import IndexPageResult
from javdb.pipeline.index_family_blacklist import (
    normalize_family_blacklist,
    filter_blacklisted_families,
)
from javdb.spider.fetch import index as index_fetch
from tests.unit.index_blacklist_helpers import _entry, spy_filter, spy_select


class _NoopSleepManager:
    def sleep(self):
        pass

    def apply_volume_multiplier(self, *_args, **_kwargs):
        pass


def _page_result():
    return IndexPageResult(
        has_movie_list=True,
        movies=[
            _entry("Wifey.2026.05.30", "western_studio_date"),
            _entry("ABC-123", "classic_hyphenated"),
        ],
    )


def _patch_sequential_dependencies(monkeypatch, *, config_blacklist):
    from javdb.spider.runtime import config as runtime_config

    monkeypatch.setattr(
        runtime_config,
        "DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST",
        config_blacklist,
    )
    monkeypatch.setattr(index_fetch, "get_page_url", lambda page_num, custom_url=None: f"page-{page_num}")
    monkeypatch.setattr(
        index_fetch,
        "fetch_index_page_with_fallback",
        lambda *_args, **kwargs: ("<html>", True, False, kwargs["use_proxy"], kwargs["use_cf_bypass"], False),
    )
    monkeypatch.setattr(index_fetch, "parse_index_page", lambda _html, _page_num: _page_result())
    monkeypatch.setattr(index_fetch, "_sleep_manager", lambda _runtime=None: _NoopSleepManager())
    monkeypatch.setattr(
        index_fetch,
        "_sentinel_field_health",
        SimpleNamespace(start_run=lambda: None, current=lambda: None),
    )


def _run_sequential(tmp_path: Path, custom_url=None):
    output_csv = "out.csv"
    output_dated_dir = tmp_path
    csv_path = tmp_path / output_csv
    return index_fetch._fetch_all_index_pages_sequential(
        runtime=None,
        session=object(),
        start_page=1,
        end_page=1,
        parse_all=False,
        phase_mode="all",
        custom_url=custom_url,
        ignore_release_date=False,
        use_proxy=False,
        use_cf_bypass=False,
        max_consecutive_empty=1,
        output_csv=output_csv,
        output_dated_dir=str(output_dated_dir),
        csv_path=str(csv_path),
        user_specified_output=True,
        parsed_movies_history_phase1={},
        parsed_movies_history_phase2={},
    )


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


def test_daily_sequential_filters_blacklisted_family_once_before_both_phases(monkeypatch, tmp_path):
    _patch_sequential_dependencies(monkeypatch, config_blacklist=["western_studio_date"])

    filter_calls = []
    original_filter = filter_blacklisted_families

    phase_calls = []

    monkeypatch.setattr(index_fetch, "filter_blacklisted_families", spy_filter(filter_calls, original_filter))
    monkeypatch.setattr(index_fetch, "select_index_entries", spy_select(phase_calls))

    result = _run_sequential(tmp_path, custom_url=None)

    assert filter_calls == [(["Wifey.2026.05.30", "ABC-123"], {"western_studio_date"})]
    assert phase_calls == [(1, ["ABC-123"]), (2, ["ABC-123"])]
    assert [entry["href"] for entry in result["all_index_results_phase1"]] == ["/v/ABC-123"]
    assert [entry["href"] for entry in result["all_index_results_phase2"]] == ["/v/ABC-123"]


def test_adhoc_sequential_bypasses_family_blacklist(monkeypatch, tmp_path):
    _patch_sequential_dependencies(monkeypatch, config_blacklist=["western_studio_date"])

    filter_spy = Mock(side_effect=AssertionError("family blacklist should be bypassed"))
    phase_calls = []

    monkeypatch.setattr(index_fetch, "filter_blacklisted_families", filter_spy)
    monkeypatch.setattr(index_fetch, "select_index_entries", spy_select(phase_calls))

    result = _run_sequential(tmp_path, custom_url="https://javdb.com/actors/EvkJ")

    filter_spy.assert_not_called()
    assert phase_calls == [
        (1, ["Wifey.2026.05.30", "ABC-123"]),
        (2, ["Wifey.2026.05.30", "ABC-123"]),
    ]
    assert [entry["href"] for entry in result["all_index_results_phase1"]] == [
        "/v/Wifey.2026.05.30",
        "/v/ABC-123",
    ]
