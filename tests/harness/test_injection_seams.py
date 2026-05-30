from javdb.infra.request import RequestHandler
import javdb.integrations.qb.uploader.service as up_service
from tests.harness.fake_qb import FakeQB
from tests.harness.fixture_http import FixtureHTTP


def test_get_page_seam_routes_to_fixture(monkeypatch):
    http = FixtureHTTP({"https://javdb.com/x": "<html>ok</html>"})
    monkeypatch.setattr(RequestHandler, "get_page",
                        lambda self, url, *a, **k: http.get_page(url, *a, **k))
    # Any RequestHandler instance now replays the cassette.
    h = RequestHandler.__new__(RequestHandler)
    assert h.get_page("https://javdb.com/x") == "<html>ok</html>"


def test_wrap_session_seam_returns_fake_qb(monkeypatch):
    fake = FakeQB()
    monkeypatch.setattr(up_service, "_wrap_session_as_client",
                        lambda session, use_proxy=False: fake)
    assert up_service._wrap_session_as_client(object()) is fake
