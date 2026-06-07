# tests/unit/test_content_filter_age_wiring.py
from dataclasses import dataclass, field

from javdb.spider.services.content_filter import Rule, evaluate


@dataclass
class _Actor:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)


def _has_age_rule(rules):
    return any(
        getattr(r, "enabled", False) and getattr(r, "dimension", "") == "age"
        for r in (rules or [])
    )


def test_opt_in_predicate():
    assert _has_age_rule([Rule(1, "age", "min_age", "18", True)]) is True
    assert _has_age_rule([Rule(1, "tag", "exclude", "vr", True)]) is False
    assert _has_age_rule([]) is False
    assert _has_age_rule([Rule(1, "age", "min_age", "18", False)]) is False


def test_runner_drops_when_age_map_violates_rule():
    rules = [Rule(1, "age", "min_age", "18", True)]
    decision = evaluate(_Detail(actors=[_Actor(href="/actors/a")]), rules, {"/actors/a": 16})
    assert decision.keep is False  # runner skips persist when keep is False
