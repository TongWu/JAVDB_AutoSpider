"""
Compatibility exports for parser models.

The canonical parser dataclasses live in ``javdb.parsing.models``. This module
keeps the historical ``apps.api.models`` import path working for API parser
adapters and downstream callers.
"""

from javdb.parsing.models import (
    ActorCredit,
    CategoryPageResult,
    IndexPageResult,
    MagnetInfo,
    MovieDetail,
    MovieIndexEntry,
    MovieLink,
    NO_ACTOR_LISTING_ACTOR_GENDER,
    NO_ACTOR_LISTING_ACTOR_NAME,
    TagCategory,
    TagOption,
    TagPageResult,
    TopPageResult,
)

__all__ = [
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
    'NO_ACTOR_LISTING_ACTOR_NAME',
    'NO_ACTOR_LISTING_ACTOR_GENDER',
]
