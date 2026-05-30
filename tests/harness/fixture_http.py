"""Replay javdb responses from an in-memory cassette (ADR-037 D3).

Drop-in for ``RequestHandler.get_page``: same ``(url, **kwargs) -> str | None``
shape. Record mode is a Phase-2 stub here (misses are merely tracked)."""

from __future__ import annotations

from typing import Optional


def _norm(url: str) -> str:
    return url.rstrip("/")


class FixtureHTTP:
    def __init__(self, pages: dict, *, record_miss: bool = False) -> None:
        """Replay ``pages`` (URL -> HTML) at the get_page seam.

        ``record_miss`` is a **Phase-2 reserved stub** and currently has no
        effect: on a cassette miss the URL is only tracked in ``misses`` and
        ``None`` is returned. Phase 2 will wire it to perform a real request and
        save the response to refresh the cassette (ADR-037 D3).
        """
        self._pages = {_norm(k): v for k, v in pages.items()}
        self.record_miss = record_miss
        self.misses: list[str] = []
        self.requests: list[str] = []

    def get_page(self, url: str, *args, **kwargs) -> Optional[str]:
        self.requests.append(url)
        hit = self._pages.get(_norm(url))
        if hit is None:
            self.misses.append(url)
        return hit
