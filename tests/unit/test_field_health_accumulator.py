# tests/unit/test_field_health_accumulator.py
from dataclasses import dataclass, field as dfield

from javdb.ops.sentinel.field_health import FieldHealthAccumulator


@dataclass
class _Row:
    href: str = ""
    video_code: str = ""
    title: str = ""
    rate: str = ""
    comment_count: str = ""
    release_date: str = ""


def test_fill_rates_count_non_empty():
    acc = FieldHealthAccumulator()
    acc.observe("index", [
        _Row(href="/v/1", video_code="A-1", title="t", rate="4.1"),
        _Row(href="/v/2", video_code="A-2", title="t2", rate=""),  # rate empty
    ])
    fills = {f.field: f for f in acc.fill_rates()}
    assert fills["href"].fill_rate == 1.0
    assert fills["rate"].fill_rate == 0.5
    assert fills["href"].sample_count == 2


def test_observe_accumulates_across_calls():
    acc = FieldHealthAccumulator()
    acc.observe("index", [_Row(href="/v/1", video_code="A-1", title="t")])
    acc.observe("index", [_Row(href="", video_code="A-2", title="t")])
    fills = {f.field: f for f in acc.fill_rates()}
    assert fills["href"].fill_rate == 0.5
    assert fills["href"].sample_count == 2


def test_empty_observation_yields_no_fills():
    acc = FieldHealthAccumulator()
    assert acc.fill_rates() == []
