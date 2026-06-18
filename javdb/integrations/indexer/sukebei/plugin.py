"""Sukebei (sukebei.nyaa.si) indexer plugin (ADR-054 WS3)."""

from __future__ import annotations

from typing import List
from urllib.parse import quote

from bs4 import BeautifulSoup

from javdb.infra.config import cfg
from javdb.infra.runtime_config import get_runtime_config
from javdb.integrations.indexer.fetch import fetch_source_html
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.plugins.registry import REGISTRY
from javdb.integrations.qb.client import extract_hash_from_magnet

_DEFAULT_BASE = "https://sukebei.nyaa.si"


def parse(html: str) -> List[IndexerMagnet]:
    soup = BeautifulSoup(html, "html.parser")
    magnets: List[IndexerMagnet] = []
    for row in soup.select("table.torrent-list tbody tr, table tbody tr"):
        anchor = row.select_one('a[href^="magnet:"]')
        if anchor is None:
            continue
        uri = (anchor.get("href") or "").strip()
        if not uri:
            continue
        title_link = row.select_one('a[href^="/view/"], a[title]')
        name = (title_link.get_text(" ", strip=True) if title_link else "") or uri
        size = ""
        for td in row.find_all("td"):
            text = td.get_text(" ", strip=True)
            if any(unit in text for unit in ("GiB", "MiB", "GB", "MB", "KiB")):
                size = text
                break
        magnets.append(
            IndexerMagnet(
                magnet_uri=uri,
                name=name,
                source="sukebei",
                info_hash=extract_hash_from_magnet(uri),
                size=size,
            )
        )
    return magnets


class SukebeiIndexerPlugin:
    name = "sukebei"

    def _base_url(self) -> str:
        return str(cfg("SUKEBEI_BASE_URL", _DEFAULT_BASE) or _DEFAULT_BASE).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._base_url())

    def search(self, video_code: str) -> IndexerResult:
        base = self._base_url()
        config = _runtime_config()
        html = fetch_source_html(
            f"{base}/?q={quote(video_code, safe='')}",
            config,
            use_proxy=bool(config.get("MAGNET_SOURCES_USE_PROXY", True)),
        )
        if not html:
            return IndexerResult(source=self.name, ok=False, detail="empty response")
        return IndexerResult(source=self.name, ok=True, magnets=parse(html))


def _runtime_config() -> dict:
    """Load merged runtime config (PROXY_POOL etc.) for the source fetch.

    Reads through the canonical ``javdb.infra`` accessor (issue #228) instead of
    importing the API layer directly. Still fails closed: a missing provider or
    a config-load error propagates so the dispatcher marks this source failed
    rather than scraping direct and leaking the operator IP (issue #226).
    """
    return get_runtime_config()


REGISTRY.register("indexer", SukebeiIndexerPlugin())
