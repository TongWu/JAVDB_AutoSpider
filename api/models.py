"""
Data models for the JAVDB scraping API layer.

All models use dataclasses for lightweight internal usage and easy
serialisation to dicts / JSON (for the FastAPI REST layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


# ---------------------------------------------------------------------------
# Primitive link model – reused for actors, directors, makers, tags, etc.
# ---------------------------------------------------------------------------

@dataclass
class MovieLink:
    """A named hyperlink extracted from the page (actor, director, maker …)."""
    name: str
    href: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Magnet link
# ---------------------------------------------------------------------------

@dataclass
class MagnetInfo:
    """A single magnet/torrent entry from a detail page."""
    href: str
    name: str
    tags: List[str] = field(default_factory=list)
    size: str = ''
    timestamp: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Index-page movie entry (used for normal index, category, and top pages)
# ---------------------------------------------------------------------------

@dataclass
class MovieIndexEntry:
    """One movie card as it appears on any listing / index page."""
    href: str
    video_code: str
    title: str = ''
    rate: str = ''
    comment_count: str = ''
    release_date: str = ''
    tags: List[str] = field(default_factory=list)
    cover_url: str = ''
    page: int = 1
    ranking: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    # Convenience: convert to the legacy dict format used by spider.py
    def to_legacy_dict(self) -> dict:
        """Return a dict compatible with the old ``parse_index()`` output."""
        return {
            'href': self.href,
            'video_code': self.video_code,
            'page': self.page,
            'actor': '',  # filled later from the detail page
            'rate': self.rate,
            'comment_number': self.comment_count,
        }


# ---------------------------------------------------------------------------
# Detail-page full movie info
# ---------------------------------------------------------------------------

@dataclass
class MovieDetail:
    """All metadata extracted from a single movie's detail page."""
    title: str = ''
    video_code: str = ''
    code_prefix_link: str = ''
    duration: str = ''
    release_date: str = ''
    publisher: Optional[MovieLink] = None
    maker: Optional[MovieLink] = None
    series: Optional[MovieLink] = None
    directors: List[MovieLink] = field(default_factory=list)
    tags: List[MovieLink] = field(default_factory=list)
    rate: str = ''
    comment_count: str = ''
    poster_url: str = ''
    fanart_urls: List[str] = field(default_factory=list)
    trailer_url: Optional[str] = None
    actors: List[MovieLink] = field(default_factory=list)
    magnets: List[MagnetInfo] = field(default_factory=list)
    review_count: int = 0
    want_count: int = 0
    watched_count: int = 0
    parse_success: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    # Convenience helpers for backward compatibility with spider.py ----------

    def get_first_actor_name(self) -> str:
        """Return the name of the first actor, or empty string."""
        return self.actors[0].name if self.actors else ''

    def get_magnets_as_legacy(self) -> list:
        """Return magnets in the legacy list-of-dicts format."""
        return [m.to_dict() for m in self.magnets]


# ---------------------------------------------------------------------------
# Page-level result containers
# ---------------------------------------------------------------------------

@dataclass
class IndexPageResult:
    """Result of parsing any movie-list index page."""
    has_movie_list: bool = False
    movies: List[MovieIndexEntry] = field(default_factory=list)
    page_title: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CategoryPageResult(IndexPageResult):
    """Result of parsing a category page (maker, publisher, series …)."""
    category_type: str = ''
    category_name: str = ''


@dataclass
class TopPageResult(IndexPageResult):
    """Result of parsing a top/ranking page."""
    top_type: str = ''
    period: Optional[str] = None


# ---------------------------------------------------------------------------
# Tag filter page models
# ---------------------------------------------------------------------------

@dataclass
class TagOption:
    """A single selectable tag within a filter category.

    Attributes:
        name: Human-readable tag label (e.g. "熟女", "巨乳").
        tag_id: The numeric (or string) ID used in URL params
                (e.g. ``"15"`` for 熟女 → ``c4=15``).
                Empty string when the ID could not be extracted from
                the current page's HTML.
        selected: Whether this tag is currently active/selected.
    """
    name: str
    tag_id: str = ''
    selected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TagCategory:
    """One filter category on the tag page (e.g. 主題, 角色, 體型 …).

    Attributes:
        category_id: The ``data-cid`` value (e.g. ``"1"``, ``"4"``).
                     Corresponds to the ``c{N}`` URL parameter.
        name: Human-readable category name (e.g. "主題", "體型").
        options: All tag options within this category.
    """
    category_id: str
    name: str
    options: List[TagOption] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def get_id_to_name_map(self) -> dict:
        """Return ``{tag_id: name}`` for options whose ID is known."""
        return {o.tag_id: o.name for o in self.options if o.tag_id}

    def get_name_to_id_map(self) -> dict:
        """Return ``{name: tag_id}`` for options whose ID is known."""
        return {o.name: o.tag_id for o in self.options if o.tag_id}

    def get_selected(self) -> List[TagOption]:
        """Return only the currently selected options."""
        return [o for o in self.options if o.selected]


@dataclass
class TagPageResult(IndexPageResult):
    """Result of parsing a JavDB tag filter page.

    Contains both the movie listings AND the full tag filter panel
    with all categories, options, and their ID mappings.
    """
    categories: List[TagCategory] = field(default_factory=list)
    current_selections: dict = field(default_factory=dict)
    """``{category_id: [tag_id, ...]}`` of currently active filters."""

    def get_full_id_to_name_map(self) -> dict:
        """Return a flat ``{(category_id, tag_id): name}`` mapping across
        all categories for every option whose ID is known."""
        result = {}
        for cat in self.categories:
            for opt in cat.options:
                if opt.tag_id:
                    result[(cat.category_id, opt.tag_id)] = opt.name
        return result

    def get_category_by_id(self, cid: str) -> Optional[TagCategory]:
        """Look up a category by its ``data-cid``."""
        for cat in self.categories:
            if cat.category_id == cid:
                return cat
        return None

    def get_category_by_name(self, name: str) -> Optional[TagCategory]:
        """Look up a category by its display name."""
        for cat in self.categories:
            if cat.name == name:
                return cat
        return None
