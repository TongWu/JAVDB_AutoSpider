# javdb/spider/parse_contract.py
"""Declarative parse contract for the site-contract drift sentinel (ADR-035).

Field names mirror javdb/parsing/models.py. 'critical' fields gate the commit
(absolute min_fill); 'soft' fields warn (fill below baseline_rel x baseline)."""

from __future__ import annotations

PARSE_CONTRACT: dict[str, dict[str, dict]] = {
    "index": {
        "href":         {"severity": "critical", "min_fill": 0.99},
        "video_code":   {"severity": "critical", "min_fill": 0.99},
        "title":        {"severity": "critical", "min_fill": 0.95},
        "rate":         {"severity": "soft",     "baseline_rel": 0.5},
        "comment_count":{"severity": "soft",     "baseline_rel": 0.5},
        "release_date": {"severity": "soft",     "baseline_rel": 0.5},
    },
    # detail-page fields are contract-ready; wiring the detail boundary is a
    # documented Phase-1 fast-follow (see plan header).
    "detail": {
        "video_code":   {"severity": "critical", "min_fill": 0.99},
        "title":        {"severity": "critical", "min_fill": 0.95},
        "magnets":      {"severity": "critical", "min_fill": 0.90},
        "actors":       {"severity": "soft",     "baseline_rel": 0.5},
        "rate":         {"severity": "soft",     "baseline_rel": 0.5},
        "release_date": {"severity": "soft",     "baseline_rel": 0.5},
    },
}


def fields_for(page_type: str) -> dict[str, dict]:
    return PARSE_CONTRACT.get(page_type, {})
