import asyncio
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.services import explore_service  # noqa: E402


def test_sanitize_proxied_html_removes_active_content():
    html = """
    <html><body onload="alert(1)">
      <script>alert(1)</script>
      <iframe src="https://evil.example/embed"></iframe>
      <a href="javascript:alert(2)" onclick="evil()">Click</a>
    </body></html>
    """

    result = explore_service._sanitize_proxied_html(html)
    lowered = result.lower()

    assert "alert(1)" not in lowered
    assert "<iframe" not in lowered
    assert "onclick=" not in lowered
    assert "javascript:alert(2)" not in lowered


def test_proxy_page_payload_sanitizes_html_and_sets_strict_csp(monkeypatch):
    monkeypatch.setattr(explore_service, "_validate_javdb_url_or_422", lambda url: None)
    monkeypatch.setattr(
        explore_service,
        "_fetch_javdb_html",
        lambda *args, **kwargs: (
            '<html><body><a href="javascript:alert(2)" onclick="evil()">x</a>'
            '<script>alert(1)</script></body></html>'
        ),
    )

    response = asyncio.run(
        explore_service.proxy_page_payload("https://javdb.com/v/abc123", "tester")
    )
    body = response.body.decode("utf-8")

    assert "javascript:alert(2)" not in body.lower()
    assert "onclick=" not in body.lower()
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "form-action 'none'" in response.headers["Content-Security-Policy"]
