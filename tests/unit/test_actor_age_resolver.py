# tests/unit/test_actor_age_resolver.py
from dataclasses import dataclass, field
from datetime import date

from javdb.spider.services.actor_age import ActorAgeResolver
from javdb.spider.services.actor_age_sources import ResolvedAge, SourceUnavailable


@dataclass
class _Actor:
    name: str = ""
    href: str = ""
    gender: str = ""


@dataclass
class _Detail:
    actors: list = field(default_factory=list)
    release_date: str = "2026-06-01"


class _FakeCache:
    def __init__(self):
        self.rows = {}
        self.put_calls = 0

    def get(self, href):
        return self.rows.get(href)

    def put(self, href, name, resolved):
        self.put_calls += 1
        self.rows[href] = {
            "birthdate": resolved.birthdate if resolved else None,
            "resolved": 1,
        }


class _FakeSource:
    name = "fake"

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = 0

    def lookup(self, actor_name):
        self.calls += 1
        bd = self.mapping.get(actor_name)
        return ResolvedAge(bd, self.name, "u") if bd else None


_TODAY = date(2026, 6, 4)


def test_resolves_and_computes_age_at_release():
    cache = _FakeCache()
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 36}  # 1990-05-20 -> 2026-06-01
    assert cache.put_calls == 1


def test_cache_hit_uses_release_date_reference():
    cache = _FakeCache()
    cache.rows["/actors/h"] = {"birthdate": "2000-06-04", "resolved": 1}
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 25}  # birthday 06-04 not yet reached by 06-01
    assert src.calls == 0  # cache short-circuits the chain


def test_missing_release_date_falls_back_to_today():
    cache = _FakeCache()
    cache.rows["/actors/h"] = {"birthdate": "2000-06-04", "resolved": 1}
    r = ActorAgeResolver(sources=[], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="H", href="/actors/h")], release_date=""))
    assert ages == {"/actors/h": 26}  # fallback today 2026-06-04 -> birthday reached


def test_negative_cache_skips_sources():
    cache = _FakeCache()
    cache.rows["/actors/h"] = {"birthdate": None, "resolved": 1}
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {}
    assert src.calls == 0


def test_source_chain_second_source_wins():
    cache = _FakeCache()
    s1 = _FakeSource({})                       # miss
    s2 = _FakeSource({"Hanako": "1985-03-03"})  # hit
    r = ActorAgeResolver(sources=[s1, s2], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 41}
    assert s1.calls == 1 and s2.calls == 1


def test_unresolved_actor_excluded_and_negatively_cached():
    cache = _FakeCache()
    src = _FakeSource({})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="Ghost", href="/actors/g")]))
    assert ages == {}
    assert cache.rows["/actors/g"]["birthdate"] is None  # negative cached


def test_actor_without_href_or_name_skipped():
    cache = _FakeCache()
    src = _FakeSource({"Hanako": "1990-05-20"})
    r = ActorAgeResolver(sources=[src], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="", href=""), _Actor(name="Hanako", href="/actors/h")]))
    assert ages == {"/actors/h": 36}
    assert src.calls == 1


def test_transport_failure_is_not_negatively_cached():
    # A source that cannot reach its backend raises SourceUnavailable. The resolver
    # must NOT write a negative cache for it — a transient outage stays retryable.
    class _FailingSource:
        name = "failing"

        def lookup(self, actor_name):
            raise SourceUnavailable("backend down")

    cache = _FakeCache()
    r = ActorAgeResolver(sources=[_FailingSource()], today=_TODAY, cache=cache)
    ages = r.ages_for(_Detail(actors=[_Actor(name="X", href="/actors/x")]))
    assert ages == {}
    assert cache.put_calls == 0
    assert "/actors/x" not in cache.rows
