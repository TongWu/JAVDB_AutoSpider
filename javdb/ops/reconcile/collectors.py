"""Read-only source collectors for reconciliation (ADR-033 D4).

A collector NEVER writes the DB. It transforms a source read into normalized
Observations; the service is the only writer.
"""

from __future__ import annotations

import logging
from typing import Iterable, Protocol

from javdb.ops.reconcile.models import Observation, OwnershipObservation, utc_now_iso

logger = logging.getLogger(__name__)

# qB torrent states that mean "finished downloading" even if progress<1 rounding.
# missingFiles: files were deleted from disk after download completed; treat as
# completed so the outcome is promoted and the stale qB entry can be removed.
_QB_COMPLETED_STATES = frozenset(
    {
        "uploading",
        "seeding",
        "stalledUP",
        "pausedUP",
        "queuedUP",
        "forcedUP",
        "checkingUP",
        "missingFiles",
    }
)


class SourceCollector(Protocol):
    source: str

    def collect(self, torrents: Iterable[dict]) -> list[Observation]: ...


class QbCollector:
    source = "qb"

    def collect(self, torrents: Iterable[dict]) -> list[Observation]:
        now = utc_now_iso()
        out: list[Observation] = []
        for t in torrents:
            qb_hash = t.get("hash")
            if not qb_hash:
                continue
            progress = t.get("progress") or 0.0
            qb_state = t.get("state") or ""
            completed = progress == 1.0 or qb_state in _QB_COMPLETED_STATES
            out.append(
                Observation(
                    source=self.source,
                    qb_hash=qb_hash,
                    state="completed" if completed else "downloading",
                    observed_at=now,
                )
            )
        return out


# ---------------------------------------------------------------------------
# ADR-033 Phase 2: Ownership collectors (read-only seam, D4)
# ---------------------------------------------------------------------------


class GdriveOwnershipCollector:
    """Project RcloneInventory into gdrive OwnershipObservations.

    Input is the load_rclone_inventory() dict (Dict[code -> List[RcloneEntry]]).
    Collapse rule (D-P2-3): multiple rows mapping to the same
    (video_code, '<sensor>|<subtitle>') keep the row with the MAX folder_size.
    """

    source = "gdrive"

    def collect(self, inventory) -> list[OwnershipObservation]:
        best: dict[tuple[str, str], OwnershipObservation] = {}
        for code, entries in (inventory or {}).items():
            for e in entries:
                category = f"{e.sensor_category}|{e.subtitle_category}"
                key = (e.video_code or code, category)
                size = int(e.folder_size or 0)
                current = best.get(key)
                if current is None or size > (current.size or 0):
                    best[key] = OwnershipObservation(
                        source=self.source,
                        video_code=e.video_code or code,
                        category=category,
                        path=e.folder_path or None,
                        size=size,
                    )
        return list(best.values())


class QbOwnershipCollector:
    """Bridge AcquisitionOutcome rows into qb OwnershipObservations.

    The only qB-hash -> video_code bridge is AcquisitionOutcome itself, so this
    consumes outcome rows (dicts or records) rather than re-reading qB.
    """

    source = "qb"

    def collect(self, outcomes) -> list[OwnershipObservation]:
        out: list[OwnershipObservation] = []
        for o in outcomes or []:
            video_code = o.get("video_code") if isinstance(o, dict) else getattr(o, "video_code", None)
            if not video_code:
                continue
            category = (o.get("category") if isinstance(o, dict) else getattr(o, "category", None)) or ""
            out.append(OwnershipObservation(
                source=self.source, video_code=video_code, category=category,
            ))
        return out


class PikpakOwnershipCollector:
    """Best-effort presence from PikpakHistory TransferStatus='success'.

    pikpakapi has no file-listing API, so this records transfer OUTCOMES, not
    live presence; category is '' and presence is monotonic (Task 6 never
    sweeps pikpak).
    """

    source = "pikpak"

    def collect(self, rows) -> list[OwnershipObservation]:
        out: list[OwnershipObservation] = []
        for r in rows or []:
            status = r.get("TransferStatus", r.get("transfer_status"))
            if status != "success":
                continue
            video_code = r.get("video_code") or r.get("VideoCode") or r.get("TorrentName")
            if not video_code:
                continue
            out.append(OwnershipObservation(source=self.source, video_code=video_code, category=""))
        return out


class NasOwnershipCollector:
    """Explicit stub (D-P2-4): NAS ownership is not collected yet.

    Returns [] and logs once so the gap is visible (no silent caps). The 'nas'
    source value stays valid in the CHECK constraint for forward-compat.
    """

    source = "nas"

    def collect(self) -> list[OwnershipObservation]:
        logger.info("nas ownership not collected (no RCLONE_NAS_REMOTE configured)")
        return []


# ---------------------------------------------------------------------------
# ADR-033 Phase 3: Media-server collector (read-only seam, D-P3-5)
# ---------------------------------------------------------------------------


class MediaServerCollector:
    """Read-only wrapper over a MediaServerAdapter. Never writes the DB."""

    def __init__(self, adapter) -> None:
        self._adapter = adapter

    @property
    def config(self):
        return self._adapter.config

    def collect(self, since):
        return list(self._adapter.list_items(since))
