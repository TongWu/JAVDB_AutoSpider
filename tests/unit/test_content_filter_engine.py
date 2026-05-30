"""Tests for the pure content-filter engine."""

from javdb.parsing.models import ActorCredit, MovieDetail, MovieLink
from javdb.spider.services.content_filter import FilterDecision, Rule, evaluate


def make_detail(*, actors=None, tags=None):
    return MovieDetail(
        actors=actors or [],
        tags=tags or [],
    )


def test_returns_keep_when_no_rules():
    detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(detail, [])

    assert decision == FilterDecision(keep=True, reasons=[])


def test_blacklist_actor_excludes_immediately():
    detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='actor', mode='exclude', value='Lead', enabled=True),
            Rule(id=2, dimension='tag', mode='include', value='Drama', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['excluded by actor rule: Lead']


def test_tag_include_requires_a_matching_tag():
    detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='tag', mode='include', value='Comedy', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['missing required tag include: Comedy']


def test_gender_require_lead_uses_first_actor_only():
    detail = make_detail(
        actors=[
            ActorCredit(name='Lead', href='/actors/lead', gender='male'),
            ActorCredit(name='Support', href='/actors/support', gender='female'),
        ],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='gender', mode='require_lead', value='female', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['lead actor gender mismatch: expected female, got male']


def test_gender_exclude_all_male_drops_when_every_actor_is_male():
    detail = make_detail(
        actors=[
            ActorCredit(name='Lead', href='/actors/lead', gender='male'),
            ActorCredit(name='Support', href='/actors/support', gender='male'),
        ],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='gender', mode='exclude_all_male', value='', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['all actors are male']


def test_disabled_rules_are_ignored():
    detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='actor', mode='exclude', value='Lead', enabled=False),
        ],
    )

    assert decision == FilterDecision(keep=True, reasons=[])
