"""Parser-related optional Rust adapter functions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Tuple

from api.parsers import (
    parse_index_page,
    parse_detail_page,
    parse_category_page,
    parse_top_page,
    parse_tag_page,
    detect_page_type,
)

try:
    from javdb_rust_core import is_login_page as _rust_is_login_page
    from javdb_rust_core import validate_index_html as _rust_validate_index_html

    RUST_PARSER_EXTRAS_AVAILABLE = True
except ImportError:
    RUST_PARSER_EXTRAS_AVAILABLE = False
    _rust_is_login_page = None
    _rust_validate_index_html = None


def result_to_dict(result: Any) -> dict:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return asdict(result)


def is_login_page(html: str) -> bool:
    if not html:
        return False
    if RUST_PARSER_EXTRAS_AVAILABLE:
        try:
            return bool(_rust_is_login_page(html))
        except Exception:
            pass
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.get_text().strip().lower()
        if "登入" in title_text or "login" in title_text:
            return True
    return False


def validate_index_html(html: str) -> Tuple[bool, bool]:
    if RUST_PARSER_EXTRAS_AVAILABLE:
        try:
            return _rust_validate_index_html(html)
        except Exception:
            pass
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    movie_list = soup.find("div", class_=lambda x: x and "movie-list" in x)
    if movie_list:
        movie_items = movie_list.find_all("div", class_="item")
        if len(movie_items) > 0:
            return True, False
        return False, True

    page_text = soup.get_text()
    empty_message_div = soup.find("div", class_="empty-message")
    age_modal = soup.find("div", class_="modal is-active over18-modal")
    has_no_content_msg = (
        "No content yet" in page_text
        or "No result" in page_text
        or "暫無內容" in page_text
        or "暂无内容" in page_text
        or empty_message_div is not None
    )
    if empty_message_div is not None or (not age_modal and has_no_content_msg) or (not age_modal and len(html) > 20000):
        return False, True
    return False, False

