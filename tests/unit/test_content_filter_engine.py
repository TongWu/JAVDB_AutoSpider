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


def test_blacklist_actor_matches_href():
    detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='actor', mode='exclude', value='/actors/lead', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['excluded by actor rule: /actors/lead']


def test_actor_blacklist_matching_is_case_insensitive_and_stable():
    detail = make_detail(
        actors=[ActorCredit(name='Lead Actor', href='/Actors/Lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/Tags/Drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='actor', mode='exclude', value='lead actor', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['excluded by actor rule: lead actor']


def test_tag_include_and_exclude_matching_is_case_insensitive_and_stable():
    detail = make_detail(
        actors=[ActorCredit(name='Lead Actor', href='/Actors/Lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/Tags/Drama')],
    )

    include_decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='tag', mode='include', value='drama', enabled=True),
        ],
    )
    exclude_decision = evaluate(
        detail,
        [
            Rule(id=2, dimension='tag', mode='exclude', value='/tags/drama', enabled=True),
        ],
    )

    assert include_decision == FilterDecision(keep=True, reasons=[])
    assert exclude_decision.keep is False
    assert exclude_decision.reasons == ['excluded by tag rule: /tags/drama']


def test_blank_actor_and_tag_rules_do_not_match_empty_fields():
    detail = make_detail(
        actors=[ActorCredit(name='', href='', gender='female')],
        tags=[MovieLink(name='', href='')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='actor', mode='exclude', value=' ', enabled=True),
            Rule(id=2, dimension='tag', mode='include', value='', enabled=True),
            Rule(id=3, dimension='tag', mode='exclude', value=' ', enabled=True),
        ],
    )

    assert decision == FilterDecision(keep=True, reasons=[])


def test_tag_exclude_drops_matching_tag():
    detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    decision = evaluate(
        detail,
        [
            Rule(id=1, dimension='tag', mode='exclude', value='Drama', enabled=True),
        ],
    )

    assert decision.keep is False
    assert decision.reasons == ['excluded by tag rule: Drama']


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


def test_tag_include_has_passing_and_failing_cases():
    passing_detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )
    failing_detail = make_detail(
        actors=[ActorCredit(name='Lead', href='/actors/lead', gender='female')],
        tags=[MovieLink(name='Comedy', href='/tags/comedy')],
    )

    rules = [Rule(id=1, dimension='tag', mode='include', value='Drama', enabled=True)]

    assert evaluate(passing_detail, rules) == FilterDecision(keep=True, reasons=[])
    assert evaluate(failing_detail, rules) == FilterDecision(
        keep=False,
        reasons=['missing required tag include: Drama'],
    )


def test_gender_require_lead_has_passing_and_failing_cases():
    passing_detail = make_detail(
        actors=[
            ActorCredit(name='Lead', href='/actors/lead', gender='female'),
            ActorCredit(name='Support', href='/actors/support', gender='male'),
        ],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )
    failing_detail = make_detail(
        actors=[
            ActorCredit(name='Lead', href='/actors/lead', gender='male'),
            ActorCredit(name='Support', href='/actors/support', gender='female'),
        ],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    rules = [
        Rule(id=1, dimension='gender', mode='require_lead', value='female', enabled=True),
    ]

    assert evaluate(passing_detail, rules) == FilterDecision(keep=True, reasons=[])
    assert evaluate(failing_detail, rules) == FilterDecision(
        keep=False,
        reasons=['lead actor gender mismatch: expected female, got male'],
    )


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


def test_gender_exclude_all_male_has_dropping_and_keeping_cases():
    dropping_detail = make_detail(
        actors=[
            ActorCredit(name='Lead', href='/actors/lead', gender='male'),
            ActorCredit(name='Support', href='/actors/support', gender='male'),
        ],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )
    keeping_detail = make_detail(
        actors=[
            ActorCredit(name='Lead', href='/actors/lead', gender='male'),
            ActorCredit(name='Support', href='/actors/support', gender='female'),
        ],
        tags=[MovieLink(name='Drama', href='/tags/drama')],
    )

    rules = [
        Rule(id=1, dimension='gender', mode='exclude_all_male', value='', enabled=True),
    ]

    assert evaluate(dropping_detail, rules) == FilterDecision(
        keep=False,
        reasons=['all actors are male'],
    )
    assert evaluate(keeping_detail, rules) == FilterDecision(keep=True, reasons=[])


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
