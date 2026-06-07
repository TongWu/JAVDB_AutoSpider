"""Repository for MovieMetadata table (ADR-022)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Optional

from javdb.infra.config import cfg
from javdb.parsing.common import javdb_absolute_url
from javdb.storage.db import get_db, HISTORY_DB_PATH


# Fields read by ``upsert`` below. Used to coerce a MovieDetail object (Python
# dataclass OR the Rust ``RustMovieDetail`` PyO3 object, which has no
# ``__dict__``) into a plain mapping via getattr. Nested link fields
# (maker/publisher/series/directors/tags) are kept as their original objects so
# ``_link`` / ``_links`` can absolutize their hrefs — do NOT use MovieDetail's
# ``to_dict()`` here, which would flatten them into plain dicts and skip
# absolutization.
_UPSERT_FIELDS = (
    'title', 'video_code', 'release_date', 'duration',
    'rate', 'comment_count', 'review_count', 'want_count', 'watched_count',
    'maker', 'publisher', 'series', 'directors', 'tags',
    'poster_url', 'fanart_urls', 'trailer_url',
)


class MetadataRepo:
    """Thin typed wrapper over MovieMetadata in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or HISTORY_DB_PATH

    def upsert(self, href: str, detail: Any) -> None:
        """UPSERT a MovieDetail into MovieMetadata.

        ``detail`` may be either a mapping (keys = MovieDetail field names,
        snake_case) or a MovieDetail-like object exposing those fields as
        attributes — the pure-Python ``MovieDetail`` dataclass or the Rust
        ``RustMovieDetail`` PyO3 object. The Rust object has no ``__dict__``,
        so callers must pass the object itself (not ``detail.__dict__``); this
        method coerces it via getattr.
        """
        if not isinstance(detail, Mapping):
            detail = {f: getattr(detail, f, None) for f in _UPSERT_FIELDS}
        # Parser emits site-relative hrefs (``/makers/x``, ``/actors/y``).
        # Normalize the movie href key and every embedded link to absolute
        # BASE_URL form so MovieMetadata.href matches MovieHistory.Href (the
        # backfill join key) and link payloads are stored consistently. (BFR-010)
        base_url = cfg('BASE_URL', 'https://javdb.com')
        href = javdb_absolute_url(href, base_url) or href

        def _link(obj) -> Optional[str]:
            if obj is None:
                return None
            if hasattr(obj, 'name') and hasattr(obj, 'href'):
                return json.dumps(
                    {'name': obj.name, 'href': javdb_absolute_url(obj.href, base_url)}
                )
            return json.dumps(obj)

        def _links(lst) -> Optional[str]:
            if not lst:
                return None
            return json.dumps(
                [{'name': x.name, 'href': javdb_absolute_url(x.href, base_url)} for x in lst]
            )

        def _urls(lst) -> Optional[str]:
            if not lst:
                return None
            return json.dumps(list(lst))

        def _duration(s: Optional[str]) -> Optional[int]:
            if not s:
                return None
            m = re.search(r'(\d+)', str(s))
            return int(m.group(1)) if m else None

        def _float(s) -> Optional[float]:
            if s is None:
                return None
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        def _int(s) -> Optional[int]:
            if s is None:
                return None
            try:
                return int(s)
            except (ValueError, TypeError):
                return None

        sql = """
            INSERT INTO MovieMetadata (
                href, title, video_code, release_date, duration_minutes,
                rate, comment_count, review_count, want_count, watched_count,
                maker, publisher, series, directors, categories,
                poster_url, fanart_urls, trailer_url,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                strftime('%Y-%m-%dT%H:%M:%fZ','now')
            )
            ON CONFLICT(href) DO UPDATE SET
                title             = excluded.title,
                video_code        = excluded.video_code,
                release_date      = excluded.release_date,
                duration_minutes  = excluded.duration_minutes,
                rate              = excluded.rate,
                comment_count     = excluded.comment_count,
                review_count      = excluded.review_count,
                want_count        = excluded.want_count,
                watched_count     = excluded.watched_count,
                maker             = excluded.maker,
                publisher         = excluded.publisher,
                series            = excluded.series,
                directors         = excluded.directors,
                categories        = excluded.categories,
                poster_url        = excluded.poster_url,
                fanart_urls       = excluded.fanart_urls,
                trailer_url       = excluded.trailer_url,
                updated_at        = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """
        params = (
            href,
            detail.get('title'),
            detail.get('video_code'),
            detail.get('release_date'),
            _duration(detail.get('duration')),
            _float(detail.get('rate')),
            _int(detail.get('comment_count')),
            _int(detail.get('review_count')),
            _int(detail.get('want_count')),
            _int(detail.get('watched_count')),
            _link(detail.get('maker')),
            _link(detail.get('publisher')),
            _link(detail.get('series')),
            _links(detail.get('directors')),
            _links(detail.get('tags')),      # MovieDetail.tags = categories
            detail.get('poster_url'),
            _urls(detail.get('fanart_urls')),
            detail.get('trailer_url'),
        )
        with get_db(self._db_path) as conn:
            conn.execute(sql, params)

    def get(self, href: str) -> Optional[dict]:
        """Return the MovieMetadata row for *href*, or None.

        The lookup key is absolutized to match how :meth:`upsert` stores it,
        so callers may pass either a relative ``/v/..`` path or an absolute
        ``https://javdb.com/v/..`` URL. (BFR-010)
        """
        base_url = cfg('BASE_URL', 'https://javdb.com')
        href = javdb_absolute_url(href, base_url) or href
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM MovieMetadata WHERE href = ?", (href,)
            ).fetchone()
        return dict(row) if row is not None else None
