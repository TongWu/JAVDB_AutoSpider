"""Plex adapter: X-Plex-Token → raw MediaItem (ADR-033 D7). Read-only."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import MediaItem

logger = logging.getLogger(__name__)
_TIMEOUT = 30


class _PlexHttp:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"X-Plex-Token": self._token, "Accept": "application/json"}
        resp = requests.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()


def _first_file_path(raw: dict) -> Optional[str]:
    for media in raw.get("Media", []) or []:
        for part in media.get("Part", []) or []:
            if part.get("file"):
                return part["file"]
    return None


class PlexAdapter:
    def __init__(self, config: MediaServerConfig, *, http: Any = None) -> None:
        self.config = config
        self._http = http or _PlexHttp(config.base_url, config.token)

    def _resolve_sections(self) -> list[dict]:
        data = self._http.get_json("/library/sections")
        dirs = ((data or {}).get("MediaContainer") or {}).get("Directory", [])
        wanted = set(self.config.libraries)
        if not wanted:
            return dirs
        return [d for d in dirs if d.get("title") in wanted]

    def list_items(self, since: Optional[str]) -> list[MediaItem]:
        # NOTE: `since` is currently ignored — every call does a full library
        # scan. Incremental pulls (Plex addedAt/updatedAt filters) are a future
        # optimization; the consumption pass tolerates a full scan today.
        out: list[MediaItem] = []
        for section in self._resolve_sections():
            key = str(section.get("key", ""))
            name = section.get("title")
            data = self._http.get_json(f"/library/sections/{key}/all", params={"type": "1"})
            for raw in ((data or {}).get("MediaContainer") or {}).get("Metadata", []):
                # TODO-VERIFY: confirm viewCount/viewOffset/duration/userRating/lastViewedAt
                # against live Plex JSON (pinned here by the injected-fake unit test shape).
                view_count = raw.get("viewCount")
                duration = raw.get("duration") or 0
                offset = raw.get("viewOffset") or 0
                if duration:
                    pct = int(offset * 100 / duration)
                else:
                    # duration=0 is missing metadata, not "complete" — don't force
                    # 100%; leave progress unknown and log for ops visibility.
                    if view_count:
                        logger.debug(
                            "Plex %s: item %s has viewCount but duration=0; progress unknown",
                            self.config.instance, raw.get("ratingKey"),
                        )
                    pct = None
                rating = raw.get("userRating")
                out.append(MediaItem(
                    instance=self.config.instance,
                    source_type="plex",
                    library_id=key,
                    library_name=name,
                    item_id=str(raw.get("ratingKey", "")),
                    file_path=_first_file_path(raw),
                    folder_name=None,
                    title=raw.get("title"),
                    watched=bool(view_count) if view_count is not None else None,
                    progress_pct=pct,
                    play_count=view_count,
                    rating=float(rating) if rating is not None else None,
                    watched_at=str(raw["lastViewedAt"]) if raw.get("lastViewedAt") else None,
                ))
        logger.info("Plex %s: collected %d items", self.config.instance, len(out))
        return out
