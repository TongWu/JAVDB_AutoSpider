import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.services import system_service  # noqa: E402


class DummyGateway:
    def fetch_and_parse(self, url, page_num):
        raise RuntimeError("sensitive upstream failure")

    def crawl_pages(self, *args, **kwargs):
        raise RuntimeError("internal crawl failure")


def test_parse_index_payload_hides_internal_exception(monkeypatch):
    def raise_parse_error(html, page_num):
        raise ValueError("secret parser failure")

    monkeypatch.setattr(system_service, "parse_index_page", raise_parse_error)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            system_service.parse_index_payload(
                SimpleNamespace(html="<html/>", page_num=1)
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to parse index page"


def test_crawl_index_payload_hides_internal_exception(monkeypatch):
    monkeypatch.setattr(system_service, "_validate_target_url", lambda url: None)
    monkeypatch.setattr(
        system_service,
        "_runtime_facade",
        lambda: SimpleNamespace(
            create_gateway=lambda **kwargs: DummyGateway(),
        ),
    )

    payload = SimpleNamespace(
        url="https://javdb.com",
        use_proxy=False,
        use_cf_bypass=False,
        use_cookie=True,
        start_page=1,
        end_page=1,
        crawl_all=False,
        max_consecutive_empty=1,
        page_delay=0,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(system_service.crawl_index_payload(payload))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to crawl index pages"
