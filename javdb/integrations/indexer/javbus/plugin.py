"""JAVBUS indexer plugin (ADR-054 WS3)."""

from __future__ import annotations

from typing import List
from urllib.parse import quote

from bs4 import BeautifulSoup

from javdb.infra.config import cfg
from javdb.integrations.indexer.fetch import fetch_source_html
from javdb.integrations.indexer.plugin import IndexerMagnet, IndexerResult
from javdb.integrations.plugins.registry import REGISTRY
from javdb.integrations.qb.client import extract_hash_from_magnet

_DEFAULT_BASE = "https://www.javbus.com"


def parse(html: str) -> List[IndexerMagnet]:
    soup = BeautifulSoup(html, "html.parser")
    magnets: List[IndexerMagnet] = []
    for anchor in soup.select('a[href^="magnet:"]'):
        uri = (anchor.get("href") or "").strip()
        if not uri:
            continue
        row = anchor.find_parent("tr")
        cells = row.find_all("td") if row is not None else []
        name = anchor.get_text(" ", strip=True) or uri
        size = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
        tags = [t.get_text(" ", strip=True) for t in row.select("span.label, .tag")] if row else []
        magnets.append(
            IndexerMagnet(
                magnet_uri=uri,
                name=name,
                source="javbus",
                info_hash=extract_hash_from_magnet(uri),
                size=size,
                tags=[t for t in tags if t],
            )
        )
    return magnets


class JavbusIndexerPlugin:
    name = "javbus"

    def _base_url(self) -> str:
        return str(cfg("JAVBUS_BASE_URL", _DEFAULT_BASE) or _DEFAULT_BASE).rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._base_url())

    def search(self, video_code: str) -> IndexerResult:
        base = self._base_url()
        config = _runtime_config()
        html = fetch_source_html(
            f"{base}/{quote(video_code.strip(), safe='')}",
            config,
            use_proxy=bool(config.get("MAGNET_SOURCES_USE_PROXY", True)),
        )
        if not html:
            return IndexerResult(source=self.name, ok=False, detail="empty response")
        return IndexerResult(source=self.name, ok=True, magnets=parse(html))


def _runtime_config() -> dict:
    """Load runtime config (PROXY_POOL etc.) for the source fetch.

    Fails closed: a config-load error must NOT degrade to an empty config. An
    empty config carries no ``PROXY_POOL``, so the fetch would go out direct and
    leak the operator's IP to the upstream source (privacy regression).
    Propagating the error makes the dispatcher mark this source failed instead
    of scraping direct (see ``dispatch.aggregate`` per-source isolation).
    """
    from apps.api.services import config_service

    return config_service.load_runtime_config()


REGISTRY.register("indexer", JavbusIndexerPlugin())
