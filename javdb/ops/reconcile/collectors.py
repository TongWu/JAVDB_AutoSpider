"""Read-only source collectors for reconciliation (ADR-033 D4).

A collector NEVER writes the DB. It transforms a source read into normalized
Observations; the service is the only writer.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from javdb.ops.reconcile.models import Observation, utc_now_iso

# qB torrent states that mean "finished downloading" even if progress<1 rounding.
_QB_COMPLETED_STATES = frozenset(
    {
        "uploading",
        "seeding",
        "stalledUP",
        "pausedUP",
        "queuedUP",
        "forcedUP",
        "checkingUP",
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
