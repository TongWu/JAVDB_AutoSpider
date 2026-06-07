from tests.harness.fixture_http import FixtureHTTP


def test_get_page_returns_fixture_by_url():
    http = FixtureHTTP({"https://javdb.com/?page=1": "<html>index</html>"})
    assert http.get_page("https://javdb.com/?page=1") == "<html>index</html>"


def test_get_page_trailing_slash_insensitive():
    http = FixtureHTTP({"https://javdb.com/v/abc": "<html>detail</html>"})
    assert http.get_page("https://javdb.com/v/abc/") == "<html>detail</html>"


def test_miss_returns_none_and_records_request():
    http = FixtureHTTP({})
    assert http.get_page("https://javdb.com/v/missing") is None
    assert "https://javdb.com/v/missing" in http.misses


def test_matches_real_get_page_kwargs():
    # must accept the full RequestHandler.get_page signature without error
    http = FixtureHTTP({"u": "ok"})
    assert http.get_page("u", session=None, use_cookie=False, use_proxy=True,
                         module_name="spider", max_retries=3, use_cf_bypass=False) == "ok"
