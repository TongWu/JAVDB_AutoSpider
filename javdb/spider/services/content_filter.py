"""Pure content-filter engine for parsed movie details."""

from __future__ import annotations

from dataclasses import dataclass
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


def evaluate(detail, rules: Iterable[Rule]) -> FilterDecision:
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
    ]
    if include_tag_rules and not any(
        _matches_link(rule.value, tag)
        for rule in include_tag_rules
        for tag in detail.tags
    ):
        expected = ', '.join(_clean_value(rule.value) for rule in include_tag_rules)
        reasons.append(f'missing required tag include: {expected}')

    for rule in enabled_rules:
        if rule.dimension != 'gender':
            continue
        reason = _gender_drop_reason(detail, rule)
        if reason:
            reasons.append(reason)

    if reasons:
        return FilterDecision(keep=False, reasons=reasons)
    return FilterDecision(keep=True, reasons=[])


def _matches_exclude_rule(detail, rule: Rule) -> bool:
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
    if rule.mode == 'exclude_all_male' and detail.actors:
        if all(actor.gender.lower() == 'male' for actor in detail.actors):
            return 'all actors are male'
    return ''


def _matches_link(value: str, item) -> bool:
    expected = _clean_value(value)
    return expected in {
        _clean_value(getattr(item, 'name', '')),
        _clean_value(getattr(item, 'href', '')),
    }


def _clean_value(value: str | None) -> str:
    return str(value or '').strip()
