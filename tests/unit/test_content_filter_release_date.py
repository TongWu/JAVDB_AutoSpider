# tests/unit/test_content_filter_release_date.py
"""Release-date content-filter branch (ADR-040 WS4a / IMP-ADR040-03)."""

from dataclasses import dataclass, field

from javdb.spider.services.content_filter import Rule, evaluate


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    release_date: str = ""


def _date_rule(mode, value, rid=1, enabled=True):
    return Rule(id=rid, dimension="release_date", mode=mode, value=value, enabled=enabled)


def test_before_drops_on_or_after_bound():
    assert evaluate(_Detail(release_date="2020-01-01"), [_date_rule("before", "2020-01-01")]).keep is False
    assert evaluate(_Detail(release_date="2021-06-30"), [_date_rule("before", "2020-01-01")]).keep is False


def test_before_keeps_strictly_earlier():
    assert evaluate(_Detail(release_date="2019-12-31"), [_date_rule("before", "2020-01-01")]).keep is True


def test_after_drops_on_or_before_bound():
    assert evaluate(_Detail(release_date="2020-01-01"), [_date_rule("after", "2020-01-01")]).keep is False
    assert evaluate(_Detail(release_date="2019-01-01"), [_date_rule("after", "2020-01-01")]).keep is False


def test_after_keeps_strictly_later():
    assert evaluate(_Detail(release_date="2020-01-02"), [_date_rule("after", "2020-01-01")]).keep is True


def test_before_drop_reason_text():
    dec = evaluate(_Detail(release_date="2021-06-30"), [_date_rule("before", "2020-01-01")])
    assert any("before 2020-01-01" in r for r in dec.reasons)


def test_missing_release_date_never_drops():
    assert evaluate(_Detail(release_date=""), [_date_rule("after", "2020-01-01")]).keep is True


def test_unparseable_release_date_never_drops():
    assert evaluate(_Detail(release_date="not-a-date"), [_date_rule("before", "2020-01-01")]).keep is True


def test_unparseable_rule_value_ignored():
    assert evaluate(_Detail(release_date="2021-01-01"), [_date_rule("before", "bogus")]).keep is True


def test_release_date_with_time_suffix_is_truncated():
    assert evaluate(_Detail(release_date="2019-12-31 12:00"), [_date_rule("before", "2020-01-01")]).keep is True


def test_disabled_release_date_rule_ignored():
    assert evaluate(_Detail(release_date="2021-01-01"), [_date_rule("before", "2020-01-01", enabled=False)]).keep is True
