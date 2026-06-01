"""Read-only qBittorrent helpers shared by qB workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_METADATA_WAIT_SECONDS = 90
DEFAULT_METADATA_POLL_INTERVAL_SECONDS = 10
DEFAULT_RECENT_METADATA_WINDOW_SECONDS = 15 * 60


def filter_recent_torrents(
    torrents: Iterable[dict[str, Any]],
    *,
    days: int = 2,
    categories: Iterable[str] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return torrents added within the included day window and categories."""
    if now is None:
        now = time.time()

    category_filter = set(categories) if categories else None
    cutoff_date = datetime.fromtimestamp(now) - timedelta(days=days - 1)
    cutoff_timestamp = int(
        cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )

    recent_torrents = []
    for torrent in torrents:
        added_on = torrent.get("added_on", 0)
        torrent_category = torrent.get("category", "")

        if added_on < cutoff_timestamp:
            continue

        if category_filter and torrent_category not in category_filter:
            continue

        recent_torrents.append(torrent)

    return recent_torrents


def get_torrent_files(
    session: Any,
    base_url: str,
    torrent_hash: str,
    *,
    proxies: dict[str, str] | None = None,
    verify: bool = True,
    timeout: int | float = DEFAULT_REQUEST_TIMEOUT,
) -> list[dict[str, Any]] | None:
    """Return torrent files, or None when the qB API request fails."""
    files_url = f"{base_url.rstrip('/')}/api/v2/torrents/files"

    try:
        response = session.get(
            files_url,
            params={"hash": torrent_hash},
            timeout=timeout,
            proxies=proxies,
            verify=verify,
        )

        if response.status_code == 200:
            return response.json()

        logger.warning(
            "Failed to get files for torrent %s: %s",
            torrent_hash,
            response.status_code,
        )
        return None

    except requests.RequestException as exc:
        logger.error("Error getting files for torrent %s: %s", torrent_hash, exc)
        return None


def recent_metadata_candidates(
    torrents: Iterable[dict[str, Any]],
    *,
    now: float | None = None,
    window_seconds: int | float | None = None,
) -> list[dict[str, Any]]:
    """Return recently added torrents worth waiting on for metadata."""
    if now is None:
        now = time.time()
    if window_seconds is None:
        window_seconds = 15 * 60

    cutoff = now - max(0, window_seconds)
    candidates = []
    for torrent in torrents:
        if not torrent.get("hash"):
            continue
        try:
            added_on = int(float(torrent.get("added_on") or 0))
        except (TypeError, ValueError):
            continue
        if added_on >= cutoff:
            candidates.append(torrent)
    return candidates


def wait_for_metadata_readiness(
    torrents: Iterable[dict[str, Any]],
    *,
    fetch_files: Callable[[str], list[dict[str, Any]] | None],
    max_wait_seconds: int | float = DEFAULT_METADATA_WAIT_SECONDS,
    poll_interval_seconds: int | float = DEFAULT_METADATA_POLL_INTERVAL_SECONDS,
    recent_window_seconds: int | float = DEFAULT_RECENT_METADATA_WINDOW_SECONDS,
    now: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Poll until most newly added torrents expose file metadata."""
    candidates = recent_metadata_candidates(
        torrents,
        now=now,
        window_seconds=recent_window_seconds,
    )
    if not candidates or max_wait_seconds <= 0:
        return {
            "checked": len(candidates),
            "ready": 0,
            "pending": 0,
            "api_failures": 0,
            "waited_seconds": 0,
        }

    ready_needed = (len(candidates) // 2) + 1
    deadline = time.monotonic() + max_wait_seconds
    waited_seconds = 0.0

    while True:
        ready = 0
        pending = 0
        api_failures = 0
        for torrent in candidates:
            files = fetch_files(torrent.get("hash", ""))
            if files is None:
                api_failures += 1
            elif len(files) == 0:
                pending += 1
            else:
                ready += 1

        logger.info(
            "Metadata readiness: ready=%d pending=%d api_failures=%d "
            "target=%d/%d",
            ready,
            pending,
            api_failures,
            ready_needed,
            len(candidates),
        )
        if pending == 0 or ready >= ready_needed:
            return {
                "checked": len(candidates),
                "ready": ready,
                "pending": pending,
                "api_failures": api_failures,
                "waited_seconds": int(waited_seconds),
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "checked": len(candidates),
                "ready": ready,
                "pending": pending,
                "api_failures": api_failures,
                "waited_seconds": int(waited_seconds),
            }

        sleep_for = min(max(1, poll_interval_seconds), remaining)
        logger.info(
            "Waiting %.0fs for qBittorrent metadata (%d/%d ready)",
            sleep_for,
            ready,
            len(candidates),
        )
        sleep(sleep_for)
        waited_seconds += sleep_for
