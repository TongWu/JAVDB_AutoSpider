# tests/unit/test_content_filter_age.py
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


def _age_rule(mode, value, rid=1):
    return Rule(id=rid, dimension="age", mode=mode, value=value, enabled=True)


def test_no_ages_keeps():
    d = _Detail(actors=[_Actor(href="/actors/a")])
    assert evaluate(d, [_age_rule("min_age", "18")], None).keep is True


def test_min_age_drops_when_known_actor_too_young():
    dec = evaluate(_Detail(), [_age_rule("min_age", "18")], {"/actors/a": 17})
    assert dec.keep is False
    assert any("minimum age 18" in r for r in dec.reasons)


def test_min_age_keeps_when_known_meets_bound():
    assert evaluate(_Detail(), [_age_rule("min_age", "18")], {"/actors/a": 22}).keep is True


def test_max_age_drops_when_known_actor_too_old():
    assert evaluate(_Detail(), [_age_rule("max_age", "40")], {"/x": 45}).keep is False


def test_unknown_actor_never_drops():
    # one known actor that satisfies the bound; another actor is unknown (absent)
    assert evaluate(_Detail(), [_age_rule("min_age", "18")], {"/known": 30}).keep is True


def test_invalid_age_value_ignored():
    assert evaluate(_Detail(), [_age_rule("min_age", "??")], {"/x": 5}).keep is True


def test_disabled_age_rule_ignored():
    r = Rule(id=1, dimension="age", mode="min_age", value="18", enabled=False)
    assert evaluate(_Detail(), [r], {"/x": 5}).keep is True
