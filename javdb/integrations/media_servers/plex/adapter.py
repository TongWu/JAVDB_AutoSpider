"""Plex adapter: X-Plex-Token → raw MediaItem (ADR-033 D7). Read-only."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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


def _watched_at_iso(value: Any) -> Optional[str]:
    """Normalize Plex ``lastViewedAt`` to the UTC ISO shape ConsumptionSignal expects.

    Plex returns a numeric Unix epoch (seconds); Emby's ``LastPlayedDate`` is
    already an ISO string. ConsumptionSignal consumers compare ``watched_at``
    against a ``YYYY-MM-DD`` cutoff and group with ``substr(watched_at, 1, 10)``,
    so a raw epoch is either excluded from the trend or lands under an invalid
    day key. Convert the epoch; pass any non-numeric value through unchanged so a
    server that already sends ISO keeps working.
    """
    # Falsy covers absent, empty, and a numeric 0.
    if not value:
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return str(value)
    # The falsy check above misses the *string* forms — '0' and '0.0' are truthy
    # strings — and a negative epoch is equally meaningless here. Both mean
    # "never viewed"; without this they became 1970-01-01 / a 1969 date and
    # polluted the consumption trend with a real-looking day.
    if epoch <= 0:
        return None
    try:
        return (
            datetime.fromtimestamp(epoch, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        logger.warning("Plex: uninterpretable lastViewedAt %r; dropping", value)
        return None


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
                    watched_at=_watched_at_iso(raw.get("lastViewedAt")),
                ))
        logger.info("Plex %s: collected %d items", self.config.instance, len(out))
        return out
