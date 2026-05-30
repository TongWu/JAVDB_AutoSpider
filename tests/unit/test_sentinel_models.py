# tests/unit/test_sentinel_models.py
from javdb.ops.sentinel.models import (
    FieldFill, DriftFinding, SentinelVerdict, SentinelOptions, utc_now_iso,
)


def test_utc_now_iso_trailing_z():
    assert utc_now_iso().endswith("Z")


def test_field_fill_fields():
    f = FieldFill(page_type="index", field="href", fill_rate=0.98, sample_count=120)
    assert f.fill_rate == 0.98


def test_verdict_defaults():
    v = SentinelVerdict()
    assert v.critical is False
    assert v.findings == []
    assert v.evaluated == 0


def test_options_defaults():
    o = SentinelOptions()
    assert o.min_sample == 30
    assert o.baseline_window == 14
