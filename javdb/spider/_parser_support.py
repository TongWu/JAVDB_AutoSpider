"""Internal support helpers for ``javdb.spider.parser``."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Tuple

from bs4 import BeautifulSoup

from javdb.infra.logging import get_logger


logger = get_logger(__name__)


_LOGIN_REQUIRED_TEXT_MARKERS = (
    "due to copyright restrictions",
    "not available in your country",
)

_MAINTENANCE_MARKERS = (
    "系統維護中",
    "系统维护中",
    "system maintenance",
    "service unavailable",
    "temporarily unavailable",
    "暫時無法使用",
)


def _has_login_required_text(html: str) -> bool:
    lower_html = html.lower()
    return all(marker in lower_html for marker in _LOGIN_REQUIRED_TEXT_MARKERS)


def is_maintenance_page(html: str) -> bool:
    """Detect javdb maintenance or service-unavailable pages."""
    if not html:
        return False
    lower_html = html.lower()
    if any(marker.lower() in lower_html for marker in _MAINTENANCE_MARKERS):
        return True
    if len(html) < 2000 and "<html" in lower_html:
        if "movie-list" not in lower_html and "video-detail" not in lower_html:
            if "503" in html or "502" in html or "maintenance" in lower_html:
                return True
    return False


def result_to_dict(result: Any) -> dict:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return asdict(result)


def is_login_page(
    html: str,
    *,
    rust_parser_extras_available: bool,
    rust_is_login_page,
) -> bool:
    if not html:
        return False
    if _has_login_required_text(html):
        return True
    if rust_parser_extras_available:
        try:
            return bool(rust_is_login_page(html))
        except Exception as exc:
            logger.debug(
                "Rust login-page detection failed; falling back to Python parser: %s",
                exc,
                exc_info=True,
            )
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.get_text().strip().lower()
        if "登入" in title_text or "login" in title_text:
            return True
    return False


def validate_index_html(
    html: str,
    *,
    rust_parser_extras_available: bool,
    rust_validate_index_html,
) -> Tuple[bool, bool]:
    if rust_parser_extras_available:
        try:
            return rust_validate_index_html(html)
        except Exception as exc:
            logger.debug(
                "Rust index HTML validation failed; falling back to Python parser: %s",
                exc,
                exc_info=True,
            )
    soup = BeautifulSoup(html, "html.parser")
    movie_list = soup.find("div", class_=lambda x: x and "movie-list" in x)
    if movie_list:
        movie_items = movie_list.find_all("div", class_="item")
        if len(movie_items) > 0:
            return True, False
        return False, True

    page_text = soup.get_text()
    empty_message_div = soup.find("div", class_="empty-message")
    age_modal = soup.select_one("div.modal.is-active.over18-modal")
    has_no_content_msg = (
        "No content yet" in page_text
        or "No result" in page_text
        or "暫無內容" in page_text
        or "暂无内容" in page_text
        or empty_message_div is not None
    )
    if (
        empty_message_div is not None
        or (not age_modal and has_no_content_msg)
        or (not age_modal and len(html) > 20000)
    ):
        return False, True
    return False, False
