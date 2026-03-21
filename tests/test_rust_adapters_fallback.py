import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.rust_adapters import parser_adapter


def test_parser_adapter_is_login_page_python_fallback(monkeypatch):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", False)
    html = "<html><head><title>JavDB Login</title></head><body></body></html>"
    assert parser_adapter.is_login_page(html) is True


def test_parser_adapter_validate_index_html_python_fallback(monkeypatch):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", False)
    html = "<html><body><div class='movie-list'><div class='item'></div></div></body></html>"
    has_movies, is_empty = parser_adapter.validate_index_html(html)
    assert has_movies is True
    assert is_empty is False

