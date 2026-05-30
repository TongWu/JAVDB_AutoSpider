# javdb/ops/sentinel/field_health.py
"""Per-run field-fill aggregation for the drift sentinel (read-only piggyback).

Observes parsed records at the parse boundary and counts non-empty values per
contract field. Never writes the DB; persistence is the service's job."""

from __future__ import annotations

import logging
from typing import Any, Optional

from javdb.ops.sentinel.models import FieldFill
from javdb.spider.parse_contract import fields_for

logger = logging.getLogger(__name__)


def _value(record: Any, name: str) -> Optional[Any]:
    if hasattr(record, name):
        return getattr(record, name)
    if isinstance(record, dict):
        return record.get(name)
    return None


def _is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True  # numbers/bools count as present


class FieldHealthAccumulator:
    def __init__(self) -> None:
        # (page_type, field) -> [filled_count, total_count]
        self._counts: dict[tuple[str, str], list[int]] = {}

    def observe(self, page_type: str, records) -> None:
        spec = fields_for(page_type)
        if not spec:
            return
        if not records:  # None / empty / non-truthy — telemetry must never raise
            return
        for record in records:
            for name in spec:
                slot = self._counts.setdefault((page_type, name), [0, 0])
                slot[1] += 1
                if _is_filled(_value(record, name)):
                    slot[0] += 1

    def fill_rates(self) -> list[FieldFill]:
        out: list[FieldFill] = []
        for (page_type, name), (filled, total) in self._counts.items():
            if total == 0:
                continue
            out.append(FieldFill(page_type, name, filled / total, total))
        return out


# --- process-global current-run accumulator (single spider process) ----------
_CURRENT: FieldHealthAccumulator | None = None


def start_run() -> FieldHealthAccumulator:
    global _CURRENT
    _CURRENT = FieldHealthAccumulator()
    return _CURRENT


def current() -> FieldHealthAccumulator | None:
    return _CURRENT


def persist_run(*, repo=None) -> int:
    """Persist the current run's fills via the service. No-op if no run started.
    Best-effort: logs and swallows on failure (must not break the spider)."""
    acc = _CURRENT
    if acc is None:
        return 0
    fills = acc.fill_rates()
    if not fills:
        return 0
    try:
        from javdb.ops.sentinel.service import persist_run as _svc_persist
        return _svc_persist(fills, repo=repo)
    except Exception:
        logger.warning("field_health.persist_run failed", exc_info=True)
        return 0
