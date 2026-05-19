import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.spider.parser import is_maintenance_page


def test_empty_string_returns_false():
    assert is_maintenance_page("") is False


def test_none_like_returns_false():
    assert is_maintenance_page("") is False
    assert is_maintenance_page(0) is False
    assert is_maintenance_page(None) is False


def test_chinese_traditional_maintenance_marker():
    assert is_maintenance_page("<html><body>系統維護中</body></html>") is True


def test_chinese_simplified_maintenance_marker():
    assert is_maintenance_page("<html><body>系统维护中</body></html>") is True


def test_english_system_maintenance_marker():
    assert is_maintenance_page("<html><body>system maintenance in progress</body></html>") is True


def test_service_unavailable_marker():
    assert is_maintenance_page("<html><body>service unavailable</body></html>") is True


def test_temporarily_unavailable_marker():
    assert is_maintenance_page("<html><body>暫時無法使用</body></html>") is True


def test_marker_case_insensitive():
    assert is_maintenance_page("<html><body>SYSTEM MAINTENANCE</body></html>") is True
    assert is_maintenance_page("<html><body>Service Unavailable</body></html>") is True


def test_normal_page_with_movie_list_returns_false():
    html = '<html><body><div class="movie-list">Normal content here</div></body></html>'
    assert is_maintenance_page(html) is False


def test_short_503_page_without_content_markers():
    html = "<html><head><title>503</title></head><body>Error</body></html>"
    assert is_maintenance_page(html) is True


def test_short_502_page_without_content_markers():
    html = "<html><head><title>502</title></head><body>Bad Gateway</body></html>"
    assert is_maintenance_page(html) is True


def test_short_503_page_with_movie_list_returns_false():
    html = '<html><body><div class="movie-list">503 items found</div></body></html>'
    assert is_maintenance_page(html) is False


def test_long_page_with_503_returns_false():
    html = "<html><body>" + ("x" * 2000) + " 503 some text</body></html>"
    assert is_maintenance_page(html) is False
