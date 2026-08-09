# javdb/spider/services/actor_age.py
"""Best-effort actor-age enrichment + resolver (ADR-040 Phase 2 / IMP-ADR040-02).

javdb actor pages carry no birthdate, so ages are resolved from minnano-av by
actor name, cached in ActorMetadata, and computed at the movie's release date.
Unknown ages never cause a drop. The runner builds the resolver only when an
'age' rule is active."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, Sequence

from javdb.parsing.common import normalize_javdb_href_path
from javdb.spider.services.actor_age_sources import (
    MinnanoAvSource,
    ResolvedAge,
    SourceUnavailable,
)
from javdb.storage.db import HISTORY_DB_PATH, get_db
from javdb.storage.repos.actor_metadata_repo import ActorMetadataRepo

logger = logging.getLogger(__name__)


def compute_age(birthdate: str, reference: date) -> Optional[int]:
    """Whole years from ISO ``birthdate`` to ``reference``; None if unparseable/future."""
    try:
        born = date.fromisoformat((birthdate or "").strip())
    except (ValueError, TypeError):
        return None
    years = reference.year - born.year
    if (reference.month, reference.day) < (born.month, born.day):
        years -= 1
    return years if years >= 0 else None


class _DbActorAgeCache:
    """Default cache backed by ActorMetadata (history DB), one short conn per op."""

    def __init__(self, db_path: str = HISTORY_DB_PATH) -> None:
        self._db_path = db_path

    def get(self, actor_href: str) -> Optional[dict]:
        try:
            with get_db(self._db_path) as conn:
                return ActorMetadataRepo(conn).get(actor_href)
        except Exception:
            logger.debug("ActorMetadata get failed for %s", actor_href, exc_info=True)
            return None

    def put(self, actor_href: str, actor_name: str,
            resolved: Optional[ResolvedAge]) -> None:
        try:
            with get_db(self._db_path) as conn:
                ActorMetadataRepo(conn).upsert(
                    actor_href, actor_name,
                    resolved.birthdate if resolved else None,
                    resolved.source if resolved else "",
                    resolved.source_url if resolved else "",
                )
        except Exception:
            logger.debug("ActorMetadata upsert failed for %s", actor_href, exc_info=True)


class _ThrottledGatewayFetch:
    """Production fetch: one proxied gateway (CF-bypass off) + a min-interval throttle.

    Reuses the spider's request stack (proxy pool honored via PROXY_MODULES for the
    'spider' module) but disables CF-bypass for external (non-javdb) hosts. A
    politeness delay keeps the daily cron from hammering / getting banned by the
    external source."""

    def __init__(self, *, min_interval: float = 1.5) -> None:
        self._min_interval = min_interval
        self._last = 0.0
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from javdb.spider.spider_gateway import create_gateway
            try:
                from javdb.spider.runtime.state import should_use_proxy_for_module
                use_proxy = bool(should_use_proxy_for_module("spider", None))
            except Exception:
                use_proxy = False
            self._gateway = create_gateway(
                use_proxy=use_proxy, use_cf_bypass=False, use_cookie=False,
            )
        return self._gateway

    def __call__(self, url: str) -> Optional[str]:
        import time
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            return self._get_gateway().fetch_html(url)
        except Exception:
            logger.debug("gateway fetch failed: %s", url, exc_info=True)
            return None


class ActorAgeResolver:
    """Turn a parsed MovieDetail into {normalized_actor_href: age}, best-effort."""

    def __init__(self, *, sources: "Sequence", today: date, cache=None) -> None:
        self._sources = list(sources)
        self._today = today  # fallback reference when release_date is unparseable
        self._cache = cache if cache is not None else _DbActorAgeCache()

    def ages_for(self, detail) -> dict[str, int]:
        reference = self._reference_for(detail)
        ages: dict[str, int] = {}
        for actor in getattr(detail, "actors", []) or []:
            href = normalize_javdb_href_path(getattr(actor, "href", "") or "")
            name = (getattr(actor, "name", "") or "").strip()
            if not href or not name or href in ages:
                continue
            birthdate = self._birthdate_for(href, name)
            if not birthdate:
                continue
            age = compute_age(birthdate, reference)
            if age is not None:
                ages[href] = age
        return ages

    def _reference_for(self, detail) -> date:
        raw = (getattr(detail, "release_date", "") or "").strip()
        try:
            return date.fromisoformat(raw[:10])
        except (ValueError, TypeError):
            return self._today

    def _birthdate_for(self, href: str, name: str) -> Optional[str]:
        cached = self._cache.get(href)
        if cached is not None and cached.get("resolved"):
            return cached.get("birthdate")  # may be None (negative cache)
        resolved, reached = self._lookup_chain(name)
        # Persist only when a source authoritatively answered (a hit, or a definite
        # miss). A transport failure (reached is False) is left uncached so the next
        # run retries — a transient outage must not permanently negative-cache the actor.
        if reached:
            self._cache.put(href, name, resolved)
        return resolved.birthdate if resolved else None

    def _lookup_chain(self, name: str) -> "tuple[Optional[ResolvedAge], bool]":
        """Return ``(hit, reached)``. ``reached`` is True iff at least one source gave
        an authoritative answer (a hit or a definite miss); False if every source was
        unreachable (raised ``SourceUnavailable``) or errored — in which case the
        caller must not write a negative cache."""
        reached = False
        for source in self._sources:
            try:
                hit = source.lookup(name)
            except SourceUnavailable:
                logger.debug("age source %s unavailable for %s",
                             getattr(source, "name", "?"), name, exc_info=True)
                continue
            except Exception:
                logger.debug("age source %s errored for %s",
                             getattr(source, "name", "?"), name, exc_info=True)
                continue
            reached = True
            if hit and hit.birthdate:
                return hit, True
        return None, reached


def build_default_resolver(today: Optional[date] = None) -> ActorAgeResolver:
    """Wire the real cache + minnano-av source (proxied, throttled). Used by the runner."""
    return ActorAgeResolver(
        sources=[MinnanoAvSource(_ThrottledGatewayFetch())],
        today=today or date.today(),
        cache=_DbActorAgeCache(),
    )
