"""ADR-024 IMP-10: turn Top-K runner-up magnets into bounded probe-queue rows.

Pure and injectable: ``repo`` is any object with ``enqueue(ProbeCandidate, *,
enqueued_at)``. Capture is additive and never affects production selection.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Dict, List

from javdb.parsing.magnet_categorize import collect_runner_ups
from javdb.storage.repos.torrent_probe_repo import ProbeCandidate

_BTIH = re.compile(r"xt=urn:btih:([0-9a-zA-Z]+)", re.IGNORECASE)


def _info_hash_from_magnet(magnet_uri: str) -> str:
    """Extract a normalized v1 info_hash (40-char lowercase hex) from a magnet.

    Returns "" for anything that is not a valid BTIH v1 — a bare existence check
    would let junk like ``btih:abc`` through and pollute the probe queue with
    hashes qB can never match. A 40-char hex BTIH is validated and lowercased; a
    32-char base32 BTIH is decoded and re-encoded as 40-char hex (the form qB
    reports in ``torrents/info``), so the probe's ``get_torrent_files`` lookups
    line up regardless of the magnet's encoding.
    """
    m = _BTIH.search(magnet_uri or "")
    if not m:
        return ""
    raw = m.group(1)
    if len(raw) == 40:  # v1 hex
        try:
            int(raw, 16)
        except ValueError:
            return ""
        return raw.lower()
    if len(raw) == 32:  # v1 base32 -> normalize to hex
        try:
            return base64.b32decode(raw.upper()).hex()
        except (binascii.Error, ValueError):
            return ""
    return ""


def enqueue_runner_ups(
    runner_ups: Dict[str, List[dict]],
    *,
    repo: Any,
    context: dict,
    global_cap: int,
    enqueued_at: str,
) -> int:
    """Enqueue runner-up candidates (bounded by ``global_cap``). Returns count.

    Skips candidates whose magnet has no btih (can't be tracked/deleted) and
    skips entirely when ``movie_href`` is empty (assist scoring needs the movie
    context, and the queue PK requires a movie_href).
    """
    movie_href = (context.get("movie_href") or "").strip()
    if not movie_href:
        return 0

    enqueued = 0
    for category, magnets in runner_ups.items():
        for magnet in magnets:
            if enqueued >= global_cap:
                return enqueued
            magnet_uri = magnet.get("href", "")
            info_hash = _info_hash_from_magnet(magnet_uri)
            if not info_hash:
                continue
            repo.enqueue(
                ProbeCandidate(
                    info_hash=info_hash,
                    movie_href=movie_href,
                    magnet_uri=magnet_uri,
                    video_code=context.get("video_code"),
                    javdb_category=category,
                    magnet_name=magnet.get("name"),
                    javdb_tags=list(magnet.get("tags", []) or []),
                    javdb_size_text=magnet.get("size"),
                ),
                enqueued_at=enqueued_at,
            )
            enqueued += 1
    return enqueued


def maybe_capture_runner_ups(
    magnets,
    *,
    context: dict,
    repo: Any,
    enabled: bool,
    k: int,
    global_cap: int,
    enqueued_at: str,
) -> int:
    """Gated, additive Top-K capture: extract runner-ups then enqueue them.

    No-op (returns 0) when ``enabled`` is False. Reads the raw ``magnets`` list
    only — never the production selection result — so selection stays identical.
    """
    if not enabled:
        return 0
    runner_ups = collect_runner_ups(magnets, k=k)
    return enqueue_runner_ups(
        runner_ups, repo=repo, context=context,
        global_cap=global_cap, enqueued_at=enqueued_at,
    )
