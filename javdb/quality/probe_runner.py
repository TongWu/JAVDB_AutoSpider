"""ADR-024 IMP-10: remote quality_probe lifecycle (shadow-only, fail-closed).

For each pending runner-up candidate: confirm metadata-only capability once,
add the magnet to the dedicated shadow category with stopCondition, poll for the
file list (bounded), extract features, UPSERT a ``quality_probe`` evidence row,
then remove the torrent with ``deleteFiles=false``. Timeouts and failures are
recorded and cleaned up — nothing here ever raises into ingestion.

Per-candidate isolation: each candidate runs in its own ``try/except/finally`` so
one bad candidate never aborts the batch, and the probe torrent is ALWAYS removed
(``deleteFiles=false``) even when an intermediate step raises — otherwise a
half-probed torrent would leak on the remote shadow endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from javdb.infra.logging import log_summary_block
from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features
from javdb.quality.models import EvidenceRecord
from javdb.quality.probe_client import PROBE_CATEGORY

logger = logging.getLogger(__name__)

PROBE_TARGET_ROLE = "quality_probe"


def _empty_summary() -> dict:
    return {
        "scanned": 0,
        "probed": 0,
        "timeout": 0,
        "capability_unsupported": 0,
        "errors": 0,
    }


def _evidence(info_hash: str, status: str, *, feats: Optional[dict] = None) -> EvidenceRecord:
    """Build an evidence row for the probe (status-only or full feature row)."""
    if feats is None:
        return EvidenceRecord(
            info_hash=info_hash,
            probe_schema_version=PROBE_SCHEMA_VERSION,
            target_role=PROBE_TARGET_ROLE,
            probe_target_name="quality_probe",
            metadata_status=status,
            reasons=[status],
        )
    return EvidenceRecord(
        info_hash=info_hash,
        probe_schema_version=PROBE_SCHEMA_VERSION,
        target_role=PROBE_TARGET_ROLE,
        probe_target_name="quality_probe",
        metadata_status=status,
        total_size_bytes=feats["total_size_bytes"],
        main_video_size_bytes=feats["main_video_size_bytes"],
        main_video_ratio=feats["main_video_ratio"],
        video_file_count=feats["video_file_count"],
        subtitle_file_count=feats["subtitle_file_count"],
        non_video_file_count=feats["non_video_file_count"],
        junk_size_bytes=feats["junk_size_bytes"],
        junk_size_ratio=feats["junk_size_ratio"],
        suspicious_file_count=feats["suspicious_file_count"],
        features={"main_video_name": feats["main_video_name"]},
    )


def probe_candidates(
    *,
    client: Any,
    queue_repo: Any,
    evidence_repo: Any,
    now: str,
    poll: Callable[[], None],
    max_polls: int = 30,
    limit: Optional[int] = None,
) -> dict:
    """Probe pending candidates. ``poll`` is the inter-poll sleep (injected)."""
    summary = _empty_summary()

    pending = queue_repo.list_pending(limit=limit)
    if not pending:
        return summary

    if not client.supports_metadata_only_probe():
        for cand in pending:
            summary["scanned"] += 1
            summary["capability_unsupported"] += 1
            evidence_repo.upsert_evidence(
                _evidence(cand.info_hash, "probe_capability_unsupported")
            )
            queue_repo.mark_status(cand.info_hash, cand.movie_href, "failed", probed_at=now)
        return summary

    for cand in pending:
        summary["scanned"] += 1
        added = False
        try:
            # D7: must be ACTIVE (paused=False) so qB actually fetches metadata;
            # stopCondition=MetadataReceived auto-stops it the moment metadata
            # arrives. A paused torrent never fetches metadata, so the stop
            # condition would never fire — defeating the metadata-only probe.
            added = bool(client.add_torrent(
                cand.magnet_uri, category=PROBE_CATEGORY,
                paused=False, stop_condition="MetadataReceived",
            ))
            if not added:
                evidence_repo.upsert_evidence(_evidence(cand.info_hash, "pending_timeout"))
                queue_repo.mark_status(cand.info_hash, cand.movie_href, "failed", probed_at=now)
                summary["timeout"] += 1
                continue

            files = None
            for _ in range(max_polls):
                files = client.get_torrent_files(cand.info_hash)
                if files:
                    break
                poll()

            if not files:
                evidence_repo.upsert_evidence(_evidence(cand.info_hash, "pending_timeout"))
                queue_repo.mark_status(cand.info_hash, cand.movie_href, "failed", probed_at=now)
                summary["timeout"] += 1
            else:
                feats = extract_file_features(files)
                evidence_repo.upsert_evidence(
                    _evidence(cand.info_hash, "metadata_received", feats=feats)
                )
                queue_repo.mark_status(cand.info_hash, cand.movie_href, "probed", probed_at=now)
                summary["probed"] += 1
        except Exception:  # noqa: BLE001 - one candidate must not abort the batch
            logger.warning(
                "quality_probe: candidate %s failed; skipping", cand.info_hash,
                exc_info=True,
            )
            summary["errors"] += 1
            try:
                queue_repo.mark_status(cand.info_hash, cand.movie_href, "failed", probed_at=now)
            except Exception:  # noqa: BLE001 - best-effort status write
                logger.warning("quality_probe: status write failed for %s", cand.info_hash)
        finally:
            # D6: short-lived — always remove the probe torrent (keep no files),
            # even on error, so a half-probed torrent never leaks on the endpoint.
            # (Deleting a hash that was never added is a harmless no-op on qB.)
            try:
                client.delete_torrents([cand.info_hash], delete_files=False)
            except Exception:  # noqa: BLE001 - cleanup is best-effort
                logger.warning(
                    "quality_probe: cleanup delete failed for %s", cand.info_hash,
                    exc_info=True,
                )

    log_summary_block(logger, "Quality Probe Summary", summary)
    return summary
