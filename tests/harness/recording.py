"""Dev-only cassette recording for the pipeline harness (ADR-037 D3, Phase 2).

NEVER runs in CI. Arm with ``JAVDB_HARNESS_RECORD=1``. ``record_pages`` fetches
each URL through a real ``RequestHandler`` (the full proxy / CF-bypass / retry
machinery), so the recorded bodies match what production would scrape. Pair with
``save_cassette`` to refresh a cassette when javdb's HTML changes.

Example (dev shell, with config.py present)::

    JAVDB_HARNESS_RECORD=1 python3 -c "
    from tests.harness.recording import record_pages
    from tests.harness.cassette import save_cassette
    urls = ['https://javdb.com?page=1', 'https://javdb.com/v/<code>']
    save_cassette('tests/harness/scenarios/cassettes/daily', record_pages(urls))
    "
"""

from __future__ import annotations

from tests.harness.fixture_http import record_enabled


def record_pages(urls, *, use_proxy: bool = False, use_cookie: bool = False) -> dict:
    """Fetch each URL live via a real RequestHandler. Dev-only.

    Raises ``RuntimeError`` if record mode is not armed, so this can never
    accidentally dial javdb from a normal test run."""
    if not record_enabled():
        raise RuntimeError(
            "record_pages requires JAVDB_HARNESS_RECORD=1 (dev-only live fetch)."
        )
    from javdb.infra.request import RequestHandler

    handler = RequestHandler()
    pages: dict[str, str] = {}
    for url in urls:
        body = handler.get_page(
            url, use_proxy=use_proxy, use_cookie=use_cookie, module_name="spider"
        )
        if body is not None:
            pages[url] = body
    return pages
