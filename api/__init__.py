"""
JAVDB AutoSpider – API Layer.

This package provides structured parsing of JavDB HTML pages and a thin
FastAPI REST interface for use by front-end applications.

Quick start (Python)::

    from api.parsers import parse_index_page, parse_detail_page
    from api.models import MovieIndexEntry, MovieDetail

Quick start (REST)::

    uvicorn api.server:app --reload
"""

from compat import extend_package_path

extend_package_path(__path__, "apps", "api")

from api.models import (
    ActorCredit,
    MovieLink,
    MagnetInfo,
    MovieIndexEntry,
    MovieDetail,
    IndexPageResult,
    CategoryPageResult,
    TopPageResult,
    TagOption,
    TagCategory,
    TagPageResult,
)
from api.parsers import (
    parse_index_page,
    parse_detail_page,
    parse_category_page,
    parse_top_page,
    parse_tag_page,
    detect_page_type,
)

__all__ = [
    # Models
    'ActorCredit',
    'MovieLink',
    'MagnetInfo',
    'MovieIndexEntry',
    'MovieDetail',
    'IndexPageResult',
    'CategoryPageResult',
    'TopPageResult',
    'TagOption',
    'TagCategory',
    'TagPageResult',
    # Parsers
    'parse_index_page',
    'parse_detail_page',
    'parse_category_page',
    'parse_top_page',
    'parse_tag_page',
    'detect_page_type',
]
