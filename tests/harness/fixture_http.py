"""Replay javdb responses from a cassette; optionally record live on a miss
(ADR-037 D3).

Drop-in for ``RequestHandler.get_page``: same ``(url, **kwargs) -> str | None``
shape. Replay is the default and the only behaviour CI ever uses. Record mode
is dev-only and triple-gated: the scenario must pass ``record_miss=True``, a
``live_fetch`` callback must be supplied, AND the ``JAVDB_HARNESS_RECORD`` env
var must be truthy. On a miss under those conditions the live body is fetched,
remembered in ``recorded`` (for ``save_cassette``), and served."""

from __future__ import annotations

import os
from typing import Callable, Optional

_RECORD_ENV = "JAVDB_HARNESS_RECORD"


def record_enabled() -> bool:
    """True when dev-only record mode is armed via env (read at call time)."""
    raw = os.environ.get(_RECORD_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _norm(url: str) -> str:
    return url.rstrip("/")


class FixtureHTTP:
    def __init__(
        self,
        pages: dict,
        *,
        record_miss: bool = False,
        live_fetch: Optional[Callable[..., Optional[str]]] = None,
    ) -> None:
        """Replay ``pages`` (URL -> HTML) at the get_page seam.

        ``record_miss`` + ``live_fetch`` + ``record_enabled()`` together arm
        record mode (ADR-037 D3); otherwise misses are merely tracked and
        ``None`` is returned (replay-only, the CI default)."""
        self._pages = {_norm(k): v for k, v in pages.items()}
        self.record_miss = record_miss
        self.live_fetch = live_fetch
        self.misses: list[str] = []
        self.requests: list[str] = []
        self.recorded: dict[str, str] = {}

    def get_page(self, url: str, *args, **kwargs) -> Optional[str]:
        self.requests.append(url)
        key = _norm(url)
        hit = self._pages.get(key)
        if hit is not None:
            return hit
        if self.record_miss and self.live_fetch is not None and record_enabled():
            body = self.live_fetch(url, *args, **kwargs)
            if body is not None:
                self._pages[key] = body
                self.recorded[key] = body
                return body
            # Live fetch itself failed — fall through to a tracked miss.
        self.misses.append(url)
        return None
