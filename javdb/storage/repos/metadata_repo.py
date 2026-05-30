"""Repository for MovieMetadata table (ADR-022)."""

from __future__ import annotations

import json
import re
from typing import Optional

from javdb.storage.db import get_db, HISTORY_DB_PATH


class MetadataRepo:
    """Thin typed wrapper over MovieMetadata in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or HISTORY_DB_PATH

    def upsert(self, href: str, detail: dict) -> None:
        """UPSERT a MovieDetail dict into MovieMetadata.

        ``detail`` is MovieDetail.__dict__ or an equivalent mapping.
        Keys match MovieDetail field names (snake_case).
        """
        def _link(obj) -> Optional[str]:
            if obj is None:
                return None
            if hasattr(obj, 'name') and hasattr(obj, 'href'):
                return json.dumps({'name': obj.name, 'href': obj.href})
            return json.dumps(obj)

        def _links(lst) -> Optional[str]:
            if not lst:
                return None
            return json.dumps([{'name': x.name, 'href': x.href} for x in lst])

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
        """Return the MovieMetadata row for *href*, or None."""
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM MovieMetadata WHERE href = ?", (href,)
            ).fetchone()
        return dict(row) if row is not None else None
