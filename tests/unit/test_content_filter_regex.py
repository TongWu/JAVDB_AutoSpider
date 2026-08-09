# tests/unit/test_content_filter_regex.py
"""Regex content-filter engine branches (ADR-040 WS4a / IMP-ADR040-03)."""

from dataclasses import dataclass, field

from javdb.spider.services.content_filter import FilterDecision, Rule, evaluate


@dataclass
class _Link:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    release_date: str = ""


def _rule(dimension, mode, value, rid=1, enabled=True):
    return Rule(id=rid, dimension=dimension, mode=mode, value=value, enabled=enabled)


def test_regex_exclude_drops_matching_tag():
    detail = _Detail(tags=[_Link(name="VR Exclusive", href="/tags/vr")])
    dec = evaluate(detail, [_rule("tag", "regex_exclude", r"(?i)\bvr\b")])
    assert dec.keep is False
    assert dec.reasons == ["excluded by tag rule: (?i)\\bvr\\b"]


def test_regex_exclude_matches_href():
    detail = _Detail(actors=[_Link(name="Lead", href="/actors/EvkJ")])
    dec = evaluate(detail, [_rule("actor", "regex_exclude", r"/actors/Evk")])
    assert dec.keep is False


def test_regex_exclude_keeps_when_no_match():
    detail = _Detail(tags=[_Link(name="Drama", href="/tags/drama")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", r"comedy")]).keep is True


def test_regex_include_requires_a_match():
    detail = _Detail(tags=[_Link(name="Drama", href="/tags/drama")])
    dec = evaluate(detail, [_rule("tag", "regex_include", r"comedy")])
    assert dec.keep is False
    assert any("missing required regex include" in r for r in dec.reasons)


def test_regex_include_passes_when_one_tag_matches():
    detail = _Detail(
        tags=[_Link(name="Drama", href="/tags/drama"), _Link(name="Comedy", href="/tags/comedy")]
    )
    assert evaluate(detail, [_rule("tag", "regex_include", r"(?i)comedy")]).keep is True


def test_bad_pattern_fails_open_exclude():
    # An unbalanced group is a re.error; it must be skipped, never raised, never drop.
    detail = _Detail(tags=[_Link(name="VR", href="/tags/vr")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", r"(unclosed")]).keep is True


def test_bad_pattern_fails_open_include():
    detail = _Detail(tags=[_Link(name="VR", href="/tags/vr")])
    # A broken include rule must not impose a phantom requirement -> keeps.
    assert evaluate(detail, [_rule("tag", "regex_include", r"[")]).keep is True


def test_blank_regex_value_does_not_match():
    detail = _Detail(tags=[_Link(name="Drama", href="/tags/drama")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", "  ")]).keep is True


def test_disabled_regex_rule_ignored():
    detail = _Detail(tags=[_Link(name="VR", href="/tags/vr")])
    assert evaluate(detail, [_rule("tag", "regex_exclude", r"vr", enabled=False)]).keep is True


def test_regex_include_matches_actor_name():
    # The allow-list permits (actor, regex_include); a matching actor NAME must
    # satisfy the requirement (regression: the field list must pass actor objects,
    # not bare `a.name` strings, so `_matches_regex` can read .name/.href).
    detail = _Detail(actors=[_Link(name="Yua Mikami", href="/actors/x")])
    assert evaluate(detail, [_rule("actor", "regex_include", r"Yua")]).keep is True


def test_regex_include_matches_actor_href():
    detail = _Detail(actors=[_Link(name="Lead", href="/actors/EvkJ")])
    assert evaluate(detail, [_rule("actor", "regex_include", r"/actors/Evk")]).keep is True


def test_regex_include_actor_no_match_drops():
    detail = _Detail(actors=[_Link(name="Someone Else", href="/actors/y")])
    dec = evaluate(detail, [_rule("actor", "regex_include", r"Yua")])
    assert dec.keep is False
    assert any("missing required regex include" in r for r in dec.reasons)


def test_regex_include_actor_not_satisfied_by_tag():
    # Dimension-scoped: an actor:regex_include must NOT be satisfied by a same-named tag.
    detail = _Detail(
        actors=[_Link(name="Bob", href="/actors/bob")],
        tags=[_Link(name="Drama", href="/tags/drama")],
    )
    dec = evaluate(detail, [_rule("actor", "regex_include", r"Drama")])
    assert dec.keep is False
    assert any("missing required regex include" in r for r in dec.reasons)


def test_regex_include_tag_not_satisfied_by_actor():
    # Dimension-scoped: a tag:regex_include must NOT be satisfied by a same-named actor.
    detail = _Detail(
        actors=[_Link(name="Drama", href="/actors/d")],
        tags=[_Link(name="Comedy", href="/tags/c")],
    )
    assert evaluate(detail, [_rule("tag", "regex_include", r"Drama")]).keep is False
