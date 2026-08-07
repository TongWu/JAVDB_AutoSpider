from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from javdb.pipeline.index_code_blacklist import (
    extract_code_keyword,
    filter_blacklisted_code_keywords,
    load_daily_code_keyword_blacklist,
)
from javdb.spider.fetch import index as index_fetch
from tests.unit.index_blacklist_helpers import spy_select


class _NoopSleepManager:
    def sleep(self) -> None:
        pass

    def apply_volume_multiplier(self, *__args: object, **__kwargs: object) -> None:
        pass


def _entry(code: str):
    from javdb.parsing.models import MovieIndexEntry

    return MovieIndexEntry(href=f"/v/{code}", video_code=code)


def _page_result():
    from javdb.parsing.models import IndexPageResult

    return IndexPageResult(
        has_movie_list=True,
        movies=[_entry("IDBD-123"), _entry("ABC-123")],
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("IDBD-123", "IDBD"),
        ("idbd-123", "IDBD"),
        ("FTKTABF-088", "FTKTABF"),
        ("259LUXU-1234", ""),
        ("n0656", "N"),
        ("", ""),
        (None, ""),
    ],
)
def test_extract_code_keyword(code, expected):
    assert extract_code_keyword(code) == expected


def test_filter_drops_blacklisted_and_counts_once():
    movies = [_entry("IDBD-123"), _entry("ABC-123"), _entry("idbd-456")]
    counts: dict[str, int] = {}
    kept = filter_blacklisted_code_keywords(movies, {"IDBD"}, counts)

    assert [m.video_code for m in kept] == ["ABC-123"]
    assert counts == {"IDBD": 2}


def test_filter_empty_blacklist_keeps_everything():
    movies = [_entry("IDBD-123")]
    counts: dict[str, int] = {}
    assert filter_blacklisted_code_keywords(movies, set(), counts) == movies
    assert counts == {}


def test_filter_coerces_missing_video_code_attribute():
    movie = SimpleNamespace()
    counts: dict[str, int] = {}
    assert filter_blacklisted_code_keywords([movie], {"IDBD"}, counts) == [movie]
    assert counts == {}


def test_load_daily_code_keyword_blacklist_uses_runtime_config(monkeypatch):
    from javdb.spider.runtime import config as runtime_config

    monkeypatch.setattr(runtime_config, "BLACKLIST_CODE_KEYWORDS", {"IDBD"})

    assert load_daily_code_keyword_blacklist(None) == {"IDBD"}
    assert load_daily_code_keyword_blacklist("https://javdb.com/actors/EvkJ") == frozenset()


def _patch_sequential_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    def _get_page_url(page_num: int, custom_url: str | None = None) -> str:
        del custom_url
        return f"page-{page_num}"

    monkeypatch.setattr(index_fetch, "get_page_url", _get_page_url)
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


def _run_sequential(tmp_path: Path, custom_url: str | None = None) -> dict[str, object]:
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


def test_daily_sequential_filters_blacklisted_code_keyword(monkeypatch, tmp_path):
    _patch_sequential_dependencies(monkeypatch)
    monkeypatch.setattr(
        index_fetch, "load_daily_code_keyword_blacklist", lambda _custom_url: {"IDBD"}
    )
    phase_calls = []
    monkeypatch.setattr(index_fetch, "select_index_entries", spy_select(phase_calls))

    _run_sequential(tmp_path, custom_url=None)

    assert phase_calls == [(1, ["ABC-123"]), (2, ["ABC-123"])]


def test_adhoc_sequential_bypasses_code_keyword_blacklist(monkeypatch, tmp_path):
    _patch_sequential_dependencies(monkeypatch)
    monkeypatch.setattr(
        index_fetch,
        "load_daily_code_keyword_blacklist",
        lambda custom_url: set() if custom_url is not None else {"IDBD"},
    )
    phase_calls = []
    monkeypatch.setattr(index_fetch, "select_index_entries", spy_select(phase_calls))

    _run_sequential(tmp_path, custom_url="https://javdb.com/actors/EvkJ")

    assert phase_calls == [
        (1, ["IDBD-123", "ABC-123"]),
        (2, ["IDBD-123", "ABC-123"]),
    ]
