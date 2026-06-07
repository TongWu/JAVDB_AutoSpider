"""External actor-age source + pure parser (ADR-040 Phase 2).

MinnanoAvSource searches minnano-av by actor name and parses a birthdate.
Confirmed against live markup (Task-2 spike, 2026-06-04):
  - search: GET search_result.php?search_scope=actress&search_word=<name>
  - a single confident match 30x-redirects straight to the actress profile
    page (actress<ID>.html), so the fetched HTML IS the profile;
  - multiple matches stay on a results list whose profile links are
    <a href="actress<ID>.html">name…</a> (NOT actress.php?…, which is video
    pagination, and NOT ranking_actress.php);
  - the profile shows 生年月日 YYYY年MM月DD日.
Parsers are pure (HTML in, ISO date / hits out). I/O is via an injected
``fetch`` callable so the resolver/tests stay deterministic. The class is a
pluggable adapter — a second source can be added later behind ``lookup``."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Optional[str]]


class SourceUnavailable(Exception):
    """An age source could not reach its backend (timeout / proxy / site failure).

    Distinct from an authoritative miss (the site responded but has no match): on
    ``SourceUnavailable`` the resolver must NOT write a negative cache, so a
    transient outage does not permanently mark an actor's age as unknown."""


# minnano-av profile links look like "actress123287.html" (optionally with a
# "?<name>" suffix). Exclude "ranking_actress.php" and "actress.php?…" (video
# pagination, not a profile).
_PROFILE_HREF_RE = re.compile(r"(?:^|/)actress\d+\.html(?:\?|$)")
_BIRTHDATE_RE = re.compile(r"生年月日[^\d]*(\d{4})年(\d{1,2})月(\d{1,2})日")


@dataclass(frozen=True)
class ResolvedAge:
    birthdate: str  # ISO YYYY-MM-DD
    source: str
    source_url: str


def _norm(s: str) -> str:
    return (s or "").strip().casefold().replace(" ", "")


def _strip_suffix(text: str) -> str:
    """Drop minnano list-entry suffixes like '【着エロ】' and trailing labels."""
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"(女優情報|情報)$", "", text)
    return text.strip()


def parse_minnano_search(html: str) -> list[tuple[str, str]]:
    """Profile (name, href) pairs from a minnano-av results LIST page.

    Matches actress<ID>.html links only; ignores ranking / video-pagination
    links. Empty list when the page has no such links (e.g. it is itself a
    single redirected-to profile, or a no-result page)."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _PROFILE_HREF_RE.search(href):
            name = a.get_text(strip=True)
            if name:
                out.append((name, href))
    return out


def parse_minnano_birthdate(html: str) -> Optional[str]:
    """ISO birthdate from a minnano-av profile page, or None.

    Anchored on the visible 生年月日 … YYYY年MM月DD日 row. ``get_text`` excludes
    tag attributes, so a <meta> description that merely lists '生年月日' as a
    field name (with no date) never produces a false match."""
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    m = _BIRTHDATE_RE.search(text)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d).date().isoformat()
    except ValueError:
        return None


class MinnanoAvSource:
    name = "minnano-av"
    _SEARCH = ("https://www.minnano-av.com/search_result.php"
               "?search_scope=actress&search_word={q}")
    _BASE = "https://www.minnano-av.com/"

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch

    def lookup(self, actor_name: str) -> Optional[ResolvedAge]:
        """Return a ``ResolvedAge`` (hit) or ``None`` (authoritative miss — the site
        responded but has no matching profile / no birthdate). Raise
        ``SourceUnavailable`` if a fetch could not reach the site (the injected fetch
        returned falsy): the caller treats that as "unknown, retry later", NOT as a
        negative cache."""
        search_url = self._SEARCH.format(q=quote(actor_name))
        html = self._fetch(search_url)
        if not html:
            raise SourceUnavailable(
                f"minnano-av search fetch failed for {actor_name!r}"
            )
        # Case 1: a confident single match redirected straight to the profile,
        # so the fetched HTML already carries the birthdate. Trust minnano's
        # match — its name index is alias-aware (a javdb stage-name alias
        # resolves to the canonical profile of the same performer).
        bd = parse_minnano_birthdate(html)
        if bd:
            return ResolvedAge(bd, self.name, search_url)
        # Case 2: a results list with several candidates — pick the profile
        # whose (suffix-stripped) name matches the query exactly, then fetch it.
        for text, href in parse_minnano_search(html):
            if _norm(_strip_suffix(text)) == _norm(actor_name):
                url = urljoin(self._BASE, href)
                profile = self._fetch(url)
                if not profile:
                    raise SourceUnavailable(
                        f"minnano-av profile fetch failed for {actor_name!r}"
                    )
                bd = parse_minnano_birthdate(profile)
                if bd:
                    return ResolvedAge(bd, self.name, url)
                # this candidate has no birthdate; keep trying other same-name matches
        return None
