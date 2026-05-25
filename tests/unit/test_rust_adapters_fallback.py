import os
import sys
import logging

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.spider import parser as parser_adapter


def test_parser_adapter_is_login_page_python_fallback(monkeypatch):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", False)
    html = "<html><head><title>JavDB Login</title></head><body></body></html>"
    assert parser_adapter.is_login_page(html) is True


def test_parser_adapter_is_login_page_logs_rust_fallback(monkeypatch, caplog):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", True)
    caplog.set_level(logging.DEBUG, logger="javdb.spider._parser_support")

    def fail_rust_login(_html):
        raise RuntimeError("rust login failed")

    monkeypatch.setattr(parser_adapter, "_rust_is_login_page", fail_rust_login)
    html = "<html><head><title>JavDB Login</title></head><body></body></html>"

    assert parser_adapter.is_login_page(html) is True
    assert "Rust login-page detection failed" in caplog.text


def test_parser_adapter_copyright_restriction_is_login_page_before_rust(monkeypatch):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", True)
    monkeypatch.setattr(parser_adapter, "_rust_is_login_page", lambda _html: False)
    html = (
        "<html><body>Due to copyright restrictions, "
        "this page is not available in your country.</body></html>"
    )

    assert parser_adapter.is_login_page(html) is True


def test_parser_adapter_validate_index_html_python_fallback(monkeypatch):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", False)
    html = "<html><body><div class='movie-list'><div class='item'></div></div></body></html>"
    has_movies, is_empty = parser_adapter.validate_index_html(html)
    assert has_movies is True
    assert is_empty is False


def test_parser_adapter_validate_index_html_logs_rust_fallback(monkeypatch, caplog):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", True)
    caplog.set_level(logging.DEBUG, logger="javdb.spider._parser_support")

    def fail_rust_validation(_html):
        raise RuntimeError("rust validation failed")

    monkeypatch.setattr(parser_adapter, "_rust_validate_index_html", fail_rust_validation)
    html = "<html><body><div class='movie-list'><div class='item'></div></div></body></html>"

    assert parser_adapter.validate_index_html(html) == (True, False)
    assert "Rust index HTML validation failed" in caplog.text


def test_parser_adapter_validate_index_html_over18_modal_class_order(monkeypatch):
    monkeypatch.setattr(parser_adapter, "RUST_PARSER_EXTRAS_AVAILABLE", False)
    html = (
        "<html><body>"
        "<div class='over18-modal modal is-active'></div>"
        "No content yet"
        "</body></html>"
    )

    assert parser_adapter.validate_index_html(html) == (False, False)
