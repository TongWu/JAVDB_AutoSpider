import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.parsers import tag_parser  # noqa: E402


def test_extract_page_url_from_saved_from_comment():
    html = "<!-- saved from url=(0038)https://javdb.com/tags?c1=23 -->"

    assert tag_parser._extract_page_url(html) == "https://javdb.com/tags?c1=23"


def test_extract_page_url_from_canonical_link():
    html = (
        '<html><head><link rel="canonical" '
        'href="https://javdb.com/tags?c7=28"></head></html>'
    )

    assert tag_parser._extract_page_url(html) == "https://javdb.com/tags?c7=28"
