# tests/unit/test_sentinel_health.py
from javdb.ops.sentinel.health import FieldHealth, compute_field_health


def _baseline(_pt, f):
    return {"rate": 0.90}.get(f)


def _rows():
    # (page_type, field, fill_rate, sample_count, observed_at)
    return [
        ("index", "href", 0.10, 100, "t"),   # critical, below 0.99 -> critical_drift
        ("index", "video_code", 1.0, 100, "t"),  # critical, ok
        ("index", "rate", 0.10, 100, "t"),   # soft, baseline 0.90 -> thr 0.45 -> soft_drift
        ("index", "comment_count", 0.80, 100, "t"),  # soft, no baseline -> no_baseline
        ("index", "href", 0.0, 5, "t"),      # below min_sample -> insufficient_sample
    ]


def test_status_matrix():
    out = compute_field_health(_rows(), baseline_fn=_baseline, min_sample=30)
    by = {(h.field, h.fill_rate): h for h in out}
    assert by[("href", 0.10)].status == "critical_drift"
    assert by[("video_code", 1.0)].status == "ok"
    assert by[("rate", 0.10)].status == "soft_drift"
    assert by[("rate", 0.10)].baseline == 0.90
    assert by[("comment_count", 0.80)].status == "no_baseline"
    assert by[("href", 0.0)].status == "insufficient_sample"


def test_unknown_field_is_skipped():
    out = compute_field_health(
        [("index", "not_a_contract_field", 0.0, 100, "t")],
        baseline_fn=_baseline, min_sample=30)
    assert out == []


def test_returns_fieldhealth_instances():
    out = compute_field_health(
        [("index", "href", 1.0, 100, "t")], baseline_fn=_baseline, min_sample=30)
    assert isinstance(out[0], FieldHealth)
    assert out[0].severity == "critical"
    assert out[0].threshold == 0.99
