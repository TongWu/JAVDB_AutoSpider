"""Pure content-filter engine for parsed movie details."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class Rule:
    id: int
    dimension: str
    mode: str
    value: str
    enabled: bool


@dataclass(frozen=True)
class FilterDecision:
    keep: bool
    reasons: list[str]


def evaluate(detail, rules: Iterable[Rule], actor_ages=None) -> FilterDecision:
    """Evaluate parsed movie detail metadata against content-filter rules."""
    enabled_rules = [rule for rule in rules if rule.enabled]

    for rule in enabled_rules:
        if _matches_exclude_rule(detail, rule):
            return FilterDecision(
                keep=False,
                reasons=[_exclude_reason(rule)],
            )

    reasons: list[str] = []

    include_tag_rules = [
        rule
        for rule in enabled_rules
        if rule.dimension == 'tag' and rule.mode == 'include'
        and _normalized_match_value(rule.value)
    ]
    if include_tag_rules and not any(
        _matches_link(rule.value, tag)
        for rule in include_tag_rules
        for tag in detail.tags
    ):
        expected = ', '.join(_clean_value(rule.value) for rule in include_tag_rules)
        reasons.append(f'missing required tag include: {expected}')

    include_regex_rules = [
        rule
        for rule in enabled_rules
        if rule.dimension in ('actor', 'tag') and rule.mode == 'regex_include'
        and _is_valid_regex(rule.value)
    ]
    # Dimension-scoped: an `actor` regex_include is satisfied only by a matching
    # actor (name/href); a `tag` regex_include only by a matching tag — mirroring
    # `regex_exclude` and the plain `tag include` dimension semantics. A rule must
    # NOT be satisfied by a field of the other dimension (e.g. an actor rule by a
    # same-named tag). The set is satisfied if at least one rule matches at least
    # one field of its own dimension.
    if include_regex_rules and not any(
        _regex_include_satisfied(rule, detail) for rule in include_regex_rules
    ):
        expected = ', '.join(_clean_value(rule.value) for rule in include_regex_rules)
        reasons.append(f'missing required regex include: {expected}')

    for rule in enabled_rules:
        if rule.dimension != 'gender':
            continue
        reason = _gender_drop_reason(detail, rule)
        if reason:
            reasons.append(reason)

    reasons.extend(_age_drop_reasons(enabled_rules, actor_ages))
    reasons.extend(_release_date_drop_reasons(enabled_rules, detail))

    if reasons:
        return FilterDecision(keep=False, reasons=reasons)
    return FilterDecision(keep=True, reasons=[])


def _matches_exclude_rule(detail, rule: Rule) -> bool:
    if rule.mode == 'regex_exclude':
        if rule.dimension == 'actor':
            return any(_matches_regex(rule.value, actor) for actor in detail.actors)
        if rule.dimension == 'tag':
            return any(_matches_regex(rule.value, tag) for tag in detail.tags)
        return False
    if rule.mode != 'exclude':
        return False
    if rule.dimension == 'actor':
        return any(_matches_link(rule.value, actor) for actor in detail.actors)
    if rule.dimension == 'tag':
        return any(_matches_link(rule.value, tag) for tag in detail.tags)
    return False


def _exclude_reason(rule: Rule) -> str:
    return f'excluded by {rule.dimension} rule: {_clean_value(rule.value)}'


def _gender_drop_reason(detail, rule: Rule) -> str:
    if rule.mode == 'require_lead':
        lead_gender = detail.actors[0].gender if detail.actors else ''
        expected = _clean_value(rule.value).lower()
        if lead_gender.lower() != expected:
            return f'lead actor gender mismatch: expected {expected}, got {lead_gender}'
    if (
        rule.mode == 'exclude_all_male'
        and detail.actors
        and all(actor.gender.lower() == 'male' for actor in detail.actors)
    ):
        return 'all actors are male'
    return ''


def _matches_link(value: str, item) -> bool:
    expected = _normalized_match_value(value)
    if not expected:
        return False
    return expected in {
        _normalized_match_value(getattr(item, 'name', '')),
        _normalized_match_value(getattr(item, 'href', '')),
    }


def _matches_regex(pattern: str, item) -> bool:
    """True if ``pattern`` (a regex) matches the item's name or href.

    Fails open: a blank pattern or a ``re.error`` (bad pattern) never matches and
    never raises, mirroring the age ``int()`` guard. Operator-authored regex runs
    inside the ingestion loop, so a catastrophic pattern must not crash it.
    """
    pat = _clean_value(pattern)
    if not pat:
        return False
    try:
        compiled = re.compile(pat)
    except re.error:
        return False
    name = str(getattr(item, "name", "") or "")
    href = str(getattr(item, "href", "") or "")
    return bool(compiled.search(name) or compiled.search(href))


def _is_valid_regex(pattern: str) -> bool:
    pat = _clean_value(pattern)
    if not pat:
        return False
    try:
        re.compile(pat)
    except re.error:
        return False
    return True


def _regex_include_satisfied(rule: Rule, detail) -> bool:
    """True if ``rule`` (a ``regex_include``) matches at least one field of its own
    dimension — actors for ``actor``, tags for ``tag``. Dimension-scoped so an
    actor rule is never satisfied by a same-named tag (and vice versa), matching
    the ``regex_exclude`` / plain ``tag include`` dimension semantics."""
    items = detail.actors if rule.dimension == 'actor' else detail.tags
    return any(_matches_regex(rule.value, item) for item in items)


def _clean_value(value: str | None) -> str:
    return str(value or '').strip()


def _normalized_match_value(value: str | None) -> str:
    return _clean_value(value).casefold()


def _age_drop_reasons(rules: list[Rule], actor_ages) -> list[str]:
    ages = [a for a in (actor_ages or {}).values() if isinstance(a, int)]
    if not ages:
        return []
    out: list[str] = []
    for rule in rules:
        if rule.dimension != 'age':
            continue
        try:
            bound = int(str(rule.value).strip())
        except (ValueError, TypeError):
            continue
        if rule.mode == 'min_age' and any(a < bound for a in ages):
            out.append(f'actor younger than minimum age {bound}')
        elif rule.mode == 'max_age' and any(a > bound for a in ages):
            out.append(f'actor older than maximum age {bound}')
    return out


def _release_date_drop_reasons(rules: list[Rule], detail) -> list[str]:
    raw = _clean_value(getattr(detail, 'release_date', ''))
    try:
        actual = date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return []  # missing/unparseable release date never drops
    out: list[str] = []
    for rule in rules:
        if rule.dimension != 'release_date':
            continue
        try:
            bound = date.fromisoformat(_clean_value(rule.value)[:10])
        except (ValueError, TypeError):
            continue  # bad rule value is ignored (fail-open)
        if rule.mode == 'before' and actual >= bound:
            out.append(f'release date not before {bound.isoformat()}')
        elif rule.mode == 'after' and actual <= bound:
            out.append(f'release date not after {bound.isoformat()}')
    return out
