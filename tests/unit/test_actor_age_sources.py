# tests/unit/test_actor_age_sources.py
import pytest

from javdb.spider.services.actor_age_sources import (
    MinnanoAvSource,
    SourceUnavailable,
    parse_minnano_birthdate,
    parse_minnano_search,
)

# A real minnano-av profile is reached by REDIRECT from the search when there
# is a single confident match — so the search fetch returns profile HTML. It
# carries a <meta> description that also literally says "生年月日" (no date, plus
# a "現在NN歳" age); get_text() excludes attributes so it must not mislead.
_PROFILE = """
<html><head>
  <meta name="description" content="プロフィール情報（生年月日、サイズ）を掲載。現在36歳。">
</head><body><table>
  <tr><th>生年月日</th><td>1990年5月20日</td></tr>
  <tr><th>血液型</th><td>A型</td></tr>
</table></body></html>
"""

# A multi-match results LIST page: profile links are actress<ID>.html (some with
# a "?<name>" suffix and bracketed labels in the text). ranking_actress.php and
# actress.php pagination links must be ignored.
_LIST = """
<html><body>
  <a href="ranking_actress.php">ランキング</a>
  <a href="actress111.html">Hanako Test【専属】女優情報</a>
  <a href="actress222.html?Other">Other Person</a>
  <a href="actress.php?actress_id=111&page=2">2</a>
</body></html>
"""


def test_parse_minnano_search_finds_profile_links_only():
    hits = parse_minnano_search(_LIST)
    hrefs = [h for _, h in hits]
    assert "actress111.html" in hrefs
    assert "actress222.html?Other" in hrefs
    assert all("ranking_actress.php" not in h for h in hrefs)
    assert all(not h.startswith("actress.php") for h in hrefs)


def test_parse_minnano_birthdate():
    assert parse_minnano_birthdate(_PROFILE) == "1990-05-20"


def test_parse_minnano_birthdate_ignores_meta_label_and_age():
    meta_only = ('<html><head><meta name="description" '
                 'content="生年月日、サイズ。現在36歳。"></head><body>no table</body></html>')
    assert parse_minnano_birthdate(meta_only) is None


def test_parse_minnano_birthdate_absent():
    assert parse_minnano_birthdate("<html><body>no dob</body></html>") is None


def test_source_single_match_redirect_to_profile():
    search_url = ("https://www.minnano-av.com/search_result.php"
                  "?search_scope=actress&search_word=Hanako%20Test")
    src = MinnanoAvSource(lambda url: _PROFILE if url == search_url else None)
    hit = src.lookup("Hanako Test")
    assert hit is not None
    assert hit.birthdate == "1990-05-20"
    assert hit.source == "minnano-av"


def test_source_multi_match_list_then_profile():
    search_url = ("https://www.minnano-av.com/search_result.php"
                  "?search_scope=actress&search_word=Hanako%20Test")
    profile_url = "https://www.minnano-av.com/actress111.html"
    pages = {search_url: _LIST, profile_url: _PROFILE}
    src = MinnanoAvSource(lambda url: pages.get(url))
    hit = src.lookup("Hanako Test")
    assert hit is not None
    assert hit.birthdate == "1990-05-20"
    assert hit.source_url == profile_url


def test_source_no_match_returns_none():
    src = MinnanoAvSource(lambda url: _LIST)
    assert src.lookup("Nonexistent Actor") is None


def test_source_search_fetch_failure_raises_unavailable():
    # A failed transport (fetch returns falsy) must NOT look like an authoritative
    # miss — it raises so the resolver leaves the actor uncached (retry next run).
    with pytest.raises(SourceUnavailable):
        MinnanoAvSource(lambda url: None).lookup("Anyone")


def test_source_profile_fetch_failure_raises_unavailable():
    # search succeeds (matching entry) but the profile fetch fails -> unavailable.
    def fetch(url):
        return _LIST if "search_result" in url else None

    with pytest.raises(SourceUnavailable):
        MinnanoAvSource(fetch).lookup("Hanako Test")


_NO_DOB_PROFILE = "<html><body>no dob</body></html>"

_DUP_LIST = """
<html><body>
  <a href="actress301.html">Dup Name</a>
  <a href="actress302.html">Dup Name</a>
</body></html>
"""


def test_source_first_candidate_no_dob_falls_through_to_second():
    """First same-name candidate has no birthdate; lookup should try the second."""
    search_url = ("https://www.minnano-av.com/search_result.php"
                  "?search_scope=actress&search_word=Dup%20Name")
    first_url = "https://www.minnano-av.com/actress301.html"
    second_url = "https://www.minnano-av.com/actress302.html"
    pages = {
        search_url: _DUP_LIST,
        first_url: _NO_DOB_PROFILE,
        second_url: _PROFILE,
    }
    src = MinnanoAvSource(lambda url: pages.get(url))
    hit = src.lookup("Dup Name")
    assert hit is not None
    assert hit.birthdate == "1990-05-20"
    assert hit.source_url == second_url
