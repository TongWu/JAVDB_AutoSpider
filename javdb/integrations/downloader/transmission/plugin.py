"""Transmission downloader plugin (ADR-039 Phase 2)."""

from __future__ import annotations

from javdb.infra.config import cfg
from javdb.integrations.downloader.plugin import DownloadResult
from javdb.integrations.downloader.transmission.client import TransmissionRpcClient
from javdb.integrations.plugins.registry import REGISTRY


class TransmissionDownloaderPlugin:
    name = "transmission"

    def is_configured(self) -> bool:
        host = cfg("TRANSMISSION_HOST", "")
        port = cfg("TRANSMISSION_PORT", "")
        return bool(host) and bool(port)

    def add_torrent(
        self,
        magnet: str,
        category: str,
        name: str | None = None,
    ) -> DownloadResult:
        if name:
            # torrent-rename-path is unreliable for magnet adds (metadata not
            # yet fetched), so fail fast instead of silently dropping the name.
            return DownloadResult(
                plugin=self.name,
                ok=False,
                detail="--name rename is not supported by the transmission backend",
            )
        host = cfg("TRANSMISSION_HOST", "localhost")
        port = cfg("TRANSMISSION_PORT", "9091")
        username = cfg("TRANSMISSION_USERNAME", "")
        password = cfg("TRANSMISSION_PASSWORD", "")
        download_dir = cfg("TRANSMISSION_DOWNLOAD_DIR", "/downloads")
        base_url = f"http://{host}:{port}"
        try:
            client = TransmissionRpcClient(
                base_url=base_url,
                username=username,
                password=password,
            )
            ok, detail = client.torrent_add(
                magnet=magnet,
                download_dir=download_dir,
                labels=[category] if category else [],
            )
            return DownloadResult(plugin=self.name, ok=ok, detail=detail)
        except Exception as exc:
            return DownloadResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("downloader", TransmissionDownloaderPlugin())
