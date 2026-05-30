# tests/unit/test_parse_contract.py
from javdb.spider.parse_contract import PARSE_CONTRACT, fields_for


def test_index_critical_fields_present():
    idx = fields_for("index")
    assert idx["href"]["severity"] == "critical"
    assert idx["video_code"]["severity"] == "critical"
    assert idx["rate"]["severity"] == "soft"


def test_critical_has_min_fill_soft_has_baseline_rel():
    for spec in fields_for("index").values():
        if spec["severity"] == "critical":
            assert "min_fill" in spec
        else:
            assert "baseline_rel" in spec


def test_fields_for_unknown_page_type_is_empty():
    assert fields_for("nope") == {}
