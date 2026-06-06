"""Emby adapter: REST + X-Emby-Token → raw MediaItem (ADR-033 D7).

Read-only. Never writes the DB. The service resolves video_codes from the
raw items this returns."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from javdb.ops.reconcile.media_config import MediaServerConfig
from javdb.ops.reconcile.models import MediaItem

logger = logging.getLogger(__name__)
_TIMEOUT = 30


class _EmbyHttp:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"X-Emby-Token": self._token, "Accept": "application/json"}
        resp = requests.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()


class EmbyAdapter:
    def __init__(self, config: MediaServerConfig, *, http: Any = None) -> None:
        self.config = config
        self._http = http or _EmbyHttp(config.base_url, config.token)

    def _resolve_libraries(self) -> list[dict]:
        # TODO-VERIFY: confirm /Library/MediaFolders vs /Library/VirtualFolders
        # (vs /Users/{id}/Views) for library discovery on the operator's Emby build.
        views = self._http.get_json("/Library/MediaFolders")
        items = (views or {}).get("Items", [])
        wanted = set(self.config.libraries)
        if not wanted:
            return items
        return [v for v in items if v.get("Name") in wanted]

    def list_items(self, since: Optional[str]) -> list[MediaItem]:
        out: list[MediaItem] = []
        for lib in self._resolve_libraries():
            lib_id = str(lib.get("Id", ""))
            lib_name = lib.get("Name")
            params = {
                "ParentId": lib_id,
                "Recursive": "true",
                "IncludeItemTypes": "Movie",
                "Fields": "Path,UserData",
            }
            try:
                data = self._http.get_json("/Items", params=params)
            except Exception:
                logger.warning(
                    "Emby %s: failed to list library %s, skipping",
                    self.config.instance, lib_id, exc_info=True,
                )
                continue
            for raw in (data or {}).get("Items", []):
                try:
                    ud = raw.get("UserData") or {}
                    pct = ud.get("PlayedPercentage")
                    out.append(MediaItem(
                        instance=self.config.instance,
                        source_type="emby",
                        library_id=lib_id,
                        library_name=lib_name,
                        item_id=str(raw.get("Id", "")),
                        file_path=raw.get("Path"),
                        folder_name=None,
                        title=raw.get("Name"),
                        watched=bool(ud.get("Played")) if "Played" in ud else None,
                        progress_pct=int(pct) if pct is not None else None,
                        play_count=ud.get("PlayCount"),
                        rating=None,
                        watched_at=ud.get("LastPlayedDate"),
                    ))
                except Exception:
                    logger.warning(
                        "Emby %s: failed to parse an item in library %s, skipping",
                        self.config.instance, lib_id, exc_info=True,
                    )
                    continue
        logger.info("Emby %s: collected %d items", self.config.instance, len(out))
        return out
