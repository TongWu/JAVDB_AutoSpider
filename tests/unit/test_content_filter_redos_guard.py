"""Write-boundary ReDoS guard for content-filter regex rules (issue #222).

`regex_write_risk` is the only line of defense before a stored regex reaches the
ingestion engine, which has no execution timeout. It must reject both classic
catastrophic-backtracking shapes: nested quantifiers AND quantified alternation.
"""

import pytest

from apps.cli.ops.content_filter import regex_write_risk


@pytest.mark.parametrize(
    "pattern",
    [
        # Nested-quantifier shapes (already guarded).
        "(a+)+",
        "(a*)+",
        "(.*)+",
        # Quantified-alternation shapes (the issue #222 bypass).
        "(a|a)+",
        "(a|ab)+",
        "(.|.)+",
        "([ab]|[cd])+",
        "(x|y)*",
        "(http|https)+",  # benign-looking but deliberately flagged (documented trade-off)
        "(a|b|c)+",       # >2 alternatives still caught
    ],
)
def test_catastrophic_patterns_are_rejected(pattern):
    assert regex_write_risk(pattern) is not None


@pytest.mark.parametrize(
    "pattern",
    [
        "comedy",            # plain literal
        r"(?i)\bvr\b",       # quantifier-free group
        "a|b",               # bare alternation, not under a quantifier
        "(a|b)",             # grouped alternation, not quantified
        "(abc)+",            # quantified group, no alternation or inner quantifier
        "/actors/Evk",       # path literal
    ],
)
def test_benign_patterns_are_allowed(pattern):
    assert regex_write_risk(pattern) is None


def test_alternation_message_is_distinct_from_nested():
    # The two shapes report different guidance so the operator knows what to fix.
    assert "alternation" in regex_write_risk("(a|a)+")
    assert "nested quantifiers" in regex_write_risk("(a+)+")


def test_overlong_pattern_is_rejected_first():
    assert "too long" in regex_write_risk("a" * 201)
