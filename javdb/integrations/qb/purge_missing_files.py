"""Purge ``missingFiles`` torrents from every configured qB instance.

A torrent enters ``missingFiles`` when qB can no longer find its files on disk
— for this pipeline that is the normal end state after the big content file is
uploaded to cloud (PikPak / rclone) and removed locally, leaving at most small
residue (samples / spam that QBFileFilter had already deselected).

qB exposes no live on-disk folder size, and reports ``progress=0`` for every
``missingFiles`` torrent, so we cannot tell "files genuinely gone" from "disk
was unmounted" by reading state alone. Instead, per torrent we:

  1. stop it (so a recheck can't auto-resume seeding a torrent whose files turn
     out to still be present),
  2. force a recheck (qB re-verifies against on-disk data),
  3. read back per-file ``progress`` to learn what is actually on disk, and
  4. delete the entry *with files* only when the content has shrunk past
     ``_SHRINK_RATIO`` of the original size AND every still-present file is at
     most the ``QB_FILE_FILTER_MIN_SIZE_MB`` threshold. Otherwise the torrent is
     left alone (stopped, files intact) — a present big file is never deleted.

Unlike the reconcile acquisition pass this is not scoped to tracked
AcquisitionOutcome rows or specific categories: a ``missingFiles`` torrent is
swept regardless of how it was added, across all categories of both qBs.
"""

from __future__ import annotations

import logging
import time

from javdb.infra.config import cfg
from javdb.infra.masking import mask_ip_address
from javdb.integrations.qb.client import QBittorrentClient
from javdb.integrations.qb.config import qb_base_url_candidates

logger = logging.getLogger(__name__)

_MISSING_STATE = "missingFiles"
_CHECKING_STATES = frozenset(
    {"checkingResumeData", "checkingUP", "checkingDL", "queuedForChecking", "checking"}
)
_SHRINK_RATIO = 0.5  # content must be below half the original size to delete files


def _threshold_bytes() -> float:
    """Residue threshold — reuse the QBFileFilter small-file cutoff (default 100MB)."""
    return float(cfg("QB_FILE_FILTER_MIN_SIZE_MB", 100)) * 1024 * 1024


def decide_delete_files(files: list, total_size: float, threshold_bytes: float) -> tuple[bool, float]:
    """Decide whether a torrent's files may be deleted along with its entry.

    Only *selected* files (``priority != 0``) count — QBFileFilter deselects
    small files so they are never written to disk. Returns
    ``(delete_files, present_bytes)``.
    """
    present = 0.0
    big_present = False
    for f in files:
        if (f.get("priority") or 0) == 0:
            continue  # deselected — never downloaded, so never on disk
        size = f.get("size") or 0
        progress = f.get("progress") or 0.0
        present += size * progress
        if progress > 0 and size > threshold_bytes:
            big_present = True  # a sizeable file is still (partly) on disk
    shrank = total_size > 0 and present < _SHRINK_RATIO * total_size
    return (shrank and not big_present), present


def _completed_before(torrent: dict, *, now: float, min_age_seconds: float) -> bool:
    """True when the torrent completed at least ``min_age_seconds`` ago.

    A torrent with no valid ``completion_on`` (never completed) is excluded, so
    only settled torrents are swept.
    """
    completed_at = torrent.get("completion_on") or 0
    return completed_at > 0 and (now - completed_at) >= min_age_seconds


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _wait_checks_done(client, hashes, *, settle, poll_interval, poll_timeout) -> None:
    """Block until none of ``hashes`` is in a checking state (or timeout).

    The initial ``settle`` wait lets qB enter the checking state before the
    first poll, so a present (slow-to-hash) file is never mistaken for done.
    """
    time.sleep(settle)
    waited = settle
    while waited < poll_timeout:
        rows = client.get_torrents_by_hashes(hashes)
        if not any((r.get("state") or "") in _CHECKING_STATES for r in rows):
            return
        time.sleep(poll_interval)
        waited += poll_interval


def purge_instance(
    client: QBittorrentClient,
    *,
    label: str,
    dry_run: bool,
    min_age_hours: float = 22.0,
    chunk_size: int = 20,
    settle: float = 2.0,
    poll_interval: float = 1.0,
    poll_timeout: float = 180.0,
) -> dict:
    """Recheck every ``missingFiles`` torrent and delete the genuinely-gone ones.

    Only torrents that completed at least ``min_age_hours`` ago are considered,
    so a torrent whose files are briefly missing right after completion is given
    time to settle before being swept.
    """
    torrents = client.get_torrents(category=None, torrent_filter="all")
    candidates = [t for t in torrents if t.get("state") == _MISSING_STATE and t.get("hash")]
    now = time.time()
    min_age_seconds = min_age_hours * 3600
    missing = [t for t in candidates if _completed_before(t, now=now, min_age_seconds=min_age_seconds)]
    skipped_recent = len(candidates) - len(missing)
    threshold = _threshold_bytes()

    to_delete: list[str] = []   # content gone → delete entry WITH files
    left: list[str] = []        # content still present → leave alone
    unverified: list[str] = []  # recheck timed out → leave alone (safe)

    logger.info(
        "[%s] %s: %d missingFiles completed >%.0fh ago (%d skipped as recent) of %d torrents%s",
        label, mask_ip_address(client.base_url), len(missing), min_age_hours,
        skipped_recent, len(torrents), " (dry-run)" if dry_run else "",
    )

    for chunk in _chunks(missing, chunk_size):
        hashes = [t["hash"] for t in chunk]
        client.stop_torrents(hashes)
        client.recheck_torrents(hashes)
        _wait_checks_done(client, hashes, settle=settle, poll_interval=poll_interval, poll_timeout=poll_timeout)

        info = {r.get("hash"): r for r in client.get_torrents_by_hashes(hashes)}
        for t in chunk:
            h = t["hash"]
            row = info.get(h)
            if row is None or (row.get("state") or "") in _CHECKING_STATES:
                # No info row (read failed) or still checking → unverifiable; never delete.
                unverified.append(h)
                logger.info("[%s]   ~ KEEP (recheck unverified) %s %s", label, h, (t.get("name") or "")[:60])
                continue
            files = client.get_torrent_files(h)
            if files is None:
                # File-list read failed — None means "unknown", NOT "no files".
                # Treating it as empty would wrongly mark on-disk content deletable.
                unverified.append(h)
                logger.info("[%s]   ~ KEEP (file list unavailable) %s %s", label, h, (t.get("name") or "")[:60])
                continue
            total_size = t.get("total_size") or t.get("size") or 0
            delete_files, present = decide_delete_files(files, total_size, threshold)
            pct = (present / total_size * 100) if total_size else 0
            if delete_files:
                to_delete.append(h)
                logger.info("[%s]   - DELETE+files (%.0f%% left) %s %s", label, pct, h, (t.get("name") or "")[:60])
            else:
                left.append(h)
                logger.info("[%s]   ~ KEEP (%.0f%% left) %s %s", label, pct, h, (t.get("name") or "")[:60])

    deleted = 0
    if to_delete and not dry_run:
        if client.delete_torrents(to_delete, delete_files=True):
            deleted = len(to_delete)
        else:
            logger.warning("[%s] delete_torrents returned falsy; treating as failed", label)

    return {
        "label": label,
        "scanned": len(torrents),
        "missing": len(missing),
        "skipped_recent": skipped_recent,
        "to_delete": len(to_delete),
        "left_present": len(left),
        "unverified": len(unverified),
        "deleted": deleted,
    }


def _build_client(base_urls, username, password) -> QBittorrentClient:
    # qB is reached directly from the runner (like the reconcile pass); no proxy.
    return QBittorrentClient(base_urls, username, password)


def run_purge_missing_files(*, dry_run: bool = False, min_age_hours: float = 22.0) -> list[dict]:
    """Purge missingFiles torrents from the primary and adhoc qB instances."""
    allow_insecure = bool(cfg("QB_ALLOW_INSECURE_HTTP", False))
    results: list[dict] = []

    # Primary qB — a failure here is fatal (the caller exits non-zero).
    primary = _build_client(
        qb_base_url_candidates(allow_insecure_http=allow_insecure),
        cfg("QB_USERNAME", ""),
        cfg("QB_PASSWORD", ""),
    )
    results.append(purge_instance(primary, label="Primary", dry_run=dry_run, min_age_hours=min_age_hours))

    # Adhoc qB — optional; an unreachable adhoc instance must not fail the run.
    adhoc_url = cfg("QB_URL_ADHOC", "")
    if adhoc_url:
        try:
            adhoc = _build_client(
                qb_base_url_candidates(adhoc_url, allow_insecure_http=allow_insecure),
                cfg("QB_USERNAME_ADHOC", "") or cfg("QB_USERNAME", ""),
                cfg("QB_PASSWORD_ADHOC", "") or cfg("QB_PASSWORD", ""),
            )
            results.append(purge_instance(adhoc, label="Adhoc", dry_run=dry_run, min_age_hours=min_age_hours))
        except Exception as exc:  # noqa: BLE001 - adhoc is best-effort
            logger.warning("Adhoc qB purge skipped (connect/scan failed): %s", exc)
            results.append({"label": "Adhoc", "error": str(exc), "scanned": 0, "missing": 0, "deleted": 0})

    return results
