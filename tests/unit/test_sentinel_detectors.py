# tests/unit/test_sentinel_detectors.py
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill


def _no_baseline(_pt, _f):
    return None


def test_critical_below_min_fill_sets_critical():
    fills = [FieldFill("index", "href", 0.10, 100)]  # min_fill 0.99
    v = evaluate(fills, min_sample=30, baseline_fn=_no_baseline)
    assert v.critical is True
    assert v.findings[0].field == "href"
    assert v.findings[0].severity == "critical"


def test_critical_ok_when_above_min_fill():
    v = evaluate([FieldFill("index", "href", 1.0, 100)], min_sample=30, baseline_fn=_no_baseline)
    assert v.critical is False
    assert v.findings == []


def test_soft_below_baseline_rel_is_soft_not_critical():
    # rate baseline 0.9, baseline_rel 0.5 -> threshold 0.45; observed 0.10 -> soft
    v = evaluate([FieldFill("index", "rate", 0.10, 100)], min_sample=30,
                 baseline_fn=lambda pt, f: 0.9 if f == "rate" else None)
    assert v.critical is False
    assert len(v.findings) == 1
    assert v.findings[0].severity == "soft"


def test_sample_guard_skips_small_runs():
    v = evaluate([FieldFill("index", "href", 0.0, 5)], min_sample=30, baseline_fn=_no_baseline)
    assert v.critical is False
    assert v.findings == []
    assert v.evaluated == 0


def test_soft_skipped_when_no_baseline_yet():
    v = evaluate([FieldFill("index", "rate", 0.0, 100)], min_sample=30, baseline_fn=_no_baseline)
    assert v.findings == []  # cannot judge soft drift without a baseline
