"""Append-only recovery outbox helpers for D1 write failures.

ADR-010 introduces an inert recovery model: callers can append durable recovery
events and operators can inspect or compact the outbox, but replay remains out
of scope for Phase 1.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

PathLike = Union[str, Path]

RECOVERY_STATES = frozenset(
    {"queued", "attempting", "replayed", "dead_lettered", "abandoned"}
)
PENDING_STATES = frozenset({"queued", "attempting"})
BLOCKING_STATES = frozenset({"dead_lettered"})
COMPACTED_STATES = frozenset({"replayed", "abandoned"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_recovery_allowed(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError("recovery_allowed must be a boolean")


@dataclass(frozen=True)
class RecoveryPolicy:
    """Policy metadata attached to every recovery outbox event."""

    logical_db: str
    operation_type: str
    idempotency_key: str
    ordering_key: str
    recovery_allowed: bool
    max_attempts: int


@dataclass(frozen=True)
class RecoveryEvent:
    """One JSONL event in the D1 recovery outbox."""

    logical_db: str
    operation_type: str
    idempotency_key: str
    ordering_key: str
    recovery_allowed: bool
    max_attempts: int
    state: str
    attempt: int = 0
    sql: Optional[str] = None
    params: Optional[List[Any]] = None
    error: Optional[str] = None
    ts: Optional[str] = None

    @classmethod
    def _from_policy(
        cls,
        policy: RecoveryPolicy,
        *,
        state: str,
        attempt: int = 0,
        sql: Optional[str] = None,
        params: Optional[Iterable[Any]] = None,
        error: Optional[str] = None,
    ) -> "RecoveryEvent":
        if state not in RECOVERY_STATES:
            raise ValueError(f"unknown recovery state: {state}")
        return cls(
            logical_db=policy.logical_db,
            operation_type=policy.operation_type,
            idempotency_key=policy.idempotency_key,
            ordering_key=policy.ordering_key,
            recovery_allowed=policy.recovery_allowed,
            max_attempts=policy.max_attempts,
            state=state,
            attempt=int(attempt),
            sql=sql,
            params=list(params) if params is not None else None,
            error=error,
            ts=_utc_now_iso(),
        )

    @classmethod
    def queued(
        cls,
        policy: RecoveryPolicy,
        sql: str,
        params: Iterable[Any],
        error: str,
    ) -> "RecoveryEvent":
        return cls._from_policy(
            policy,
            state="queued",
            attempt=0,
            sql=sql,
            params=params,
            error=error,
        )

    @classmethod
    def attempting(cls, policy: RecoveryPolicy, *, attempt: int) -> "RecoveryEvent":
        return cls._from_policy(policy, state="attempting", attempt=attempt)

    @classmethod
    def replayed(cls, policy: RecoveryPolicy, *, attempt: int) -> "RecoveryEvent":
        return cls._from_policy(policy, state="replayed", attempt=attempt)

    @classmethod
    def dead_lettered(
        cls,
        policy: RecoveryPolicy,
        *,
        attempt: int,
        error: Optional[str] = None,
    ) -> "RecoveryEvent":
        return cls._from_policy(
            policy,
            state="dead_lettered",
            attempt=attempt,
            error=error,
        )

    @classmethod
    def abandoned(
        cls,
        policy: RecoveryPolicy,
        *,
        attempt: int = 0,
        error: Optional[str] = None,
    ) -> "RecoveryEvent":
        return cls._from_policy(
            policy,
            state="abandoned",
            attempt=attempt,
            error=error,
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "ts": self.ts or _utc_now_iso(),
            "logical_db": self.logical_db,
            "operation_type": self.operation_type,
            "idempotency_key": self.idempotency_key,
            "ordering_key": self.ordering_key,
            "recovery_allowed": bool(self.recovery_allowed),
            "max_attempts": int(self.max_attempts),
            "state": self.state,
            "attempt": int(self.attempt),
        }
        if self.sql is not None:
            data["sql"] = self.sql
        if self.params is not None:
            data["params"] = self.params
        if self.error is not None:
            data["error"] = self.error
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RecoveryEvent":
        state = str(raw["state"])
        if state not in RECOVERY_STATES:
            raise ValueError(f"unknown recovery state: {state}")
        params = raw.get("params")
        if params is not None and not isinstance(params, list):
            raise ValueError("params must be a JSON array when present")
        return cls(
            logical_db=str(raw["logical_db"]),
            operation_type=str(raw["operation_type"]),
            idempotency_key=str(raw["idempotency_key"]),
            ordering_key=str(raw["ordering_key"]),
            recovery_allowed=_parse_recovery_allowed(raw["recovery_allowed"]),
            max_attempts=int(raw["max_attempts"]),
            state=state,
            attempt=int(raw.get("attempt", 0)),
            sql=str(raw["sql"]) if raw.get("sql") is not None else None,
            params=params,
            error=str(raw["error"]) if raw.get("error") is not None else None,
            ts=str(raw["ts"]) if raw.get("ts") is not None else None,
        )

    def with_payload_from(self, other: "RecoveryEvent") -> "RecoveryEvent":
        """Return this event with SQL payload copied from an earlier event."""
        return replace(
            self,
            sql=self.sql if self.sql is not None else other.sql,
            params=self.params if self.params is not None else other.params,
            error=self.error if self.error is not None else other.error,
        )


def append_event(path: PathLike, event: RecoveryEvent) -> None:
    """Append *event* to the JSONL recovery outbox, creating parents."""
    outbox = Path(path)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
        fh.write("\n")


def _iter_events(path: PathLike) -> Iterable[RecoveryEvent]:
    outbox = Path(path)
    if not outbox.exists():
        return []

    events: List[RecoveryEvent] = []
    with outbox.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                if isinstance(raw, Mapping):
                    events.append(RecoveryEvent.from_dict(raw))
            except Exception:
                continue
    return events


def _read_raw_event_lines(path: PathLike) -> List[Tuple[str, Optional[RecoveryEvent]]]:
    outbox = Path(path)
    if not outbox.exists():
        return []

    records: List[Tuple[str, Optional[RecoveryEvent]]] = []
    with outbox.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            event: Optional[RecoveryEvent] = None
            try:
                raw = json.loads(line)
                if isinstance(raw, Mapping):
                    event = RecoveryEvent.from_dict(raw)
            except Exception:
                event = None
            normalized_line = raw_line if raw_line.endswith("\n") else f"{raw_line}\n"
            records.append((normalized_line, event))
    return records


def _histories_from_events(
    events: Iterable[RecoveryEvent],
) -> "OrderedDict[str, List[RecoveryEvent]]":
    histories: "OrderedDict[str, List[RecoveryEvent]]" = OrderedDict()
    for event in events:
        histories.setdefault(event.idempotency_key, []).append(event)
    return histories


def _latest_events_with_payload(
    histories: "OrderedDict[str, List[RecoveryEvent]]",
    states: frozenset[str],
) -> "OrderedDict[str, List[RecoveryEvent]]":
    grouped: "OrderedDict[str, List[RecoveryEvent]]" = OrderedDict()
    for history in histories.values():
        latest = history[-1]
        if latest.state not in states:
            continue
        queued_payload = next(
            (event for event in history if event.sql is not None),
            latest,
        )
        latest_with_payload = latest.with_payload_from(queued_payload)
        grouped.setdefault(latest.ordering_key, []).append(latest_with_payload)
    return grouped


def load_latest_events(path: PathLike) -> Dict[str, RecoveryEvent]:
    """Return the latest recovery event for each idempotency key."""
    latest: Dict[str, RecoveryEvent] = {}
    for event in _iter_events(path):
        latest[event.idempotency_key] = event
    return latest


def pending_by_ordering_key(path: PathLike) -> Dict[str, List[RecoveryEvent]]:
    """Group active queued/attempting events by ordering key in FIFO order."""
    return dict(
        _latest_events_with_payload(
            _histories_from_events(_iter_events(path)),
            PENDING_STATES,
        )
    )


def outbox_status(path: PathLike) -> Dict[str, Any]:
    """Summarise latest active and malformed state for an outbox path."""
    records = _read_raw_event_lines(path)
    malformed_count = sum(1 for _, event in records if event is None)
    events = [event for _, event in records if event is not None]
    histories = _histories_from_events(events)
    pending_groups = _latest_events_with_payload(histories, PENDING_STATES)
    dead_lettered_groups = _latest_events_with_payload(histories, BLOCKING_STATES)

    latest_state_counts = {
        state: 0 for state in sorted(RECOVERY_STATES)
    }
    for history in histories.values():
        latest_state_counts[history[-1].state] += 1

    return {
        "pending_count": sum(len(events) for events in pending_groups.values()),
        "dead_lettered_count": sum(
            len(events) for events in dead_lettered_groups.values()
        ),
        "malformed_count": malformed_count,
        "ordering_key_count": len(
            set(pending_groups.keys()) | set(dead_lettered_groups.keys())
        ),
        "pending_groups": dict(pending_groups),
        "dead_lettered_groups": dict(dead_lettered_groups),
        "latest_state_counts": latest_state_counts,
    }


def compact_replayed(active: PathLike, processed: PathLike) -> Dict[str, int]:
    """Move replayed/abandoned event histories from active JSONL to processed."""
    active_path = Path(active)
    processed_path = Path(processed)
    records = _read_raw_event_lines(active_path)
    if not records:
        return {"active": 0, "processed": 0}

    latest: Dict[str, RecoveryEvent] = {}
    for _, event in records:
        if event is not None:
            latest[event.idempotency_key] = event

    compacted_keys = {
        key for key, event in latest.items() if event.state in COMPACTED_STATES
    }
    active_lines: List[str] = []
    processed_lines: List[str] = []
    for raw_line, event in records:
        if event is not None and event.idempotency_key in compacted_keys:
            processed_lines.append(raw_line)
        else:
            active_lines.append(raw_line)

    active_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = active_path.with_name(f"{active_path.name}.tmp")
    temp_path.write_text("".join(active_lines), encoding="utf-8")

    if processed_lines:
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        with processed_path.open("a", encoding="utf-8") as fh:
            fh.writelines(processed_lines)

    temp_path.replace(active_path)

    return {"active": len(active_lines), "processed": len(processed_lines)}
