# tests/unit/test_sentinel_drift_fixtures.py
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill


def _baseline(_pt, f):
    return {"rate": 0.95, "comment_count": 0.9, "release_date": 0.92}.get(f)


def test_href_selector_break_is_critical_gate():
    # href silently stopped parsing across the run
    fills = [
        FieldFill("index", "href", 0.0, 120),
        FieldFill("index", "video_code", 1.0, 120),
        FieldFill("index", "title", 1.0, 120),
    ]
    v = evaluate(fills, min_sample=30, baseline_fn=_baseline)
    assert v.critical is True
    assert any(f.field == "href" and f.severity == "critical" for f in v.findings)


def test_rate_selector_break_is_soft_warn_only():
    # div.score broke: rate collapses, criticals intact
    fills = [
        FieldFill("index", "href", 1.0, 120),
        FieldFill("index", "video_code", 1.0, 120),
        FieldFill("index", "title", 1.0, 120),
        FieldFill("index", "rate", 0.0, 120),
    ]
    v = evaluate(fills, min_sample=30, baseline_fn=_baseline)
    assert v.critical is False
    assert any(f.field == "rate" and f.severity == "soft" for f in v.findings)
