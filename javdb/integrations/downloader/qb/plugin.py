"""qBittorrent downloader plugin — thin adapter over QBittorrentClient (ADR-039 Phase 2).

Does NOT touch javdb/integrations/qb/uploader/service.py — see IMP Out of Scope."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.downloader.plugin import DownloadResult
from javdb.integrations.plugins.registry import REGISTRY
from javdb.integrations.qb.client import QBittorrentClient
from javdb.integrations.qb.config import qb_base_url_candidates


class QbDownloaderPlugin:
    name = "qb"

    def is_configured(self) -> bool:
        host = cfg("QB_URL", "") or cfg("QB_HOST", "")
        username = cfg("QB_USERNAME", "")
        return bool(host) and bool(username)

    def add_torrent(
        self,
        magnet: str,
        category: str,
        name: str | None = None,
    ) -> DownloadResult:
        try:
            client = QBittorrentClient(
                base_urls=qb_base_url_candidates(),
                username=cfg("QB_USERNAME", ""),
                password=cfg("QB_PASSWORD", ""),
                request_timeout=cfg("REQUEST_TIMEOUT", 30),
            )
            ok = client.add_torrent(
                magnet_link=magnet,
                name=name,
                category=category,
                save_path=cfg("TORRENT_SAVE_PATH", ""),
                skip_checking=cfg("SKIP_CHECKING", False),
                paused=not cfg("AUTO_START", True),
            )
            if ok:
                return DownloadResult(plugin=self.name, ok=True)
            return DownloadResult(plugin=self.name, ok=False, detail="add_torrent returned False")
        except Exception as exc:
            return DownloadResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("downloader", QbDownloaderPlugin())
