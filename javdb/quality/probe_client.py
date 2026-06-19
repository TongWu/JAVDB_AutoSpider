"""ADR-024 IMP-10: build the remote quality_probe qBittorrent client from config.

Returns None (fail-closed) when no probe endpoint is configured or login fails,
so callers skip probing cleanly on a fresh deploy.
"""

from __future__ import annotations

import logging
from typing import Optional

from javdb.infra.config import cfg
from javdb.integrations.qb.client import QBittorrentClient

logger = logging.getLogger(__name__)

PROBE_CATEGORY = "JavDB Quality Shadow"


def build_probe_client(use_proxy=None, proxies_getter=None) -> Optional[QBittorrentClient]:
    """Build the remote probe client. ``use_proxy`` is tri-state (True/False/None
    auto) and is passed through to the client / proxies_getter unchanged — do not
    collapse None to False, or auto-mode would be forced off."""
    raw = cfg("QUALITY_PROBE_QB_URL", "")
    url = raw.strip() if isinstance(raw, str) else ""
    if not url:
        logger.info("quality_probe endpoint not configured; skipping probe")
        return None
    username = cfg("QUALITY_PROBE_QB_USERNAME", "") or cfg("QB_USERNAME", "")
    password = cfg("QUALITY_PROBE_QB_PASSWORD", "") or cfg("QB_PASSWORD", "")
    try:
        return QBittorrentClient(
            url, username, password,
            use_proxy=use_proxy, proxies_getter=proxies_getter,
            request_timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001 - login/network failure must fail closed
        logger.warning("quality_probe login failed (%s); skipping probe", exc)
        return None
