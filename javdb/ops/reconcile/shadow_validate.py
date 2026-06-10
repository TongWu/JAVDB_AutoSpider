"""Cross-validation of AcquisitionOutcomeShadow vs AcquisitionOutcome (ADR-036 P2).

compare_shadow_to_authoritative() compares the event-driven shadow projection
against the authoritative table and returns a ShadowValidateResult.  Never
raises — comparison errors are collected in the result.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# States that the shadow CAN track; comparison is only meaningful here.
_COMPARABLE_STATES = frozenset({"queued", "completed"})


@dataclass
class ShadowValidateResult:
    shadow_total: int = 0
    auth_total: int = 0
    missing_from_shadow: list[str] = field(default_factory=list)
    missing_from_auth: list[str] = field(default_factory=list)
    state_mismatches: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            not self.missing_from_shadow
            and not self.missing_from_auth
            and not self.state_mismatches
            and not self.errors
        )


def compare_shadow_to_authoritative(
    *,
    shadow_conn=None,
    auth_conn=None,
) -> ShadowValidateResult:
    """Compare shadow projection to authoritative AcquisitionOutcome.

    Accepts optional injected connections for testability.  When not provided,
    opens the reports DB (shadow) and operations DB (auth) via get_db.

    State-mapping rule: only flag state mismatches when BOTH shadow and
    authoritative have a state in {queued, completed}.  Authoritative states
    like in_library, downloading, stalled, failed are not comparable to the
    shadow's coarse view and are silently skipped.
    """
    result = ShadowValidateResult()
    try:
        shadow_rows, auth_rows = _load_rows(shadow_conn, auth_conn)
        result.shadow_total = len(shadow_rows)
        result.auth_total = len(auth_rows)

        shadow_map = {r["qb_hash"]: r["state"] for r in shadow_rows}
        # Only compare auth rows that have a comparable (queued/completed) state
        auth_map_comparable = {
            r["qb_hash"]: r["state"]
            for r in auth_rows
            if r["state"] in _COMPARABLE_STATES
        }
        auth_all_hashes = {r["qb_hash"] for r in auth_rows}

        # Missing from shadow: in auth (comparable states) but not in shadow
        result.missing_from_shadow = sorted(
            h for h in auth_map_comparable if h not in shadow_map
        )

        # Missing from auth: in shadow but not in auth at all
        result.missing_from_auth = sorted(
            h for h in shadow_map if h not in auth_all_hashes
        )

        # State mismatches: in both, both states comparable, but differ
        for qb_hash in shadow_map:
            if qb_hash not in auth_map_comparable:
                continue  # auth has a non-comparable state or not present
            shadow_state = shadow_map[qb_hash]
            auth_state = auth_map_comparable[qb_hash]
            if shadow_state != auth_state:
                result.state_mismatches.append({
                    "qb_hash": qb_hash,
                    "shadow_state": shadow_state,
                    "auth_state": auth_state,
                })
    except Exception as exc:
        logger.exception("compare_shadow_to_authoritative failed")
        result.errors.append(str(exc))
    return result


def _load_rows(shadow_conn, auth_conn) -> tuple[list, list]:
    """Load all rows from both connections.

    Injected connections (tests) are used as-is; otherwise the DBs are opened
    via the backend-aware ``get_db`` so under ``STORAGE_BACKEND=d1``/``dual`` the
    cross-validation reads the canonical D1 backend, not a stale local mirror.
    """
    with contextlib.ExitStack() as stack:
        if shadow_conn is not None:
            sc = shadow_conn
        else:
            from javdb.storage.db import REPORTS_DB_PATH, get_db
            sc = stack.enter_context(get_db(REPORTS_DB_PATH))
        if auth_conn is not None:
            ac = auth_conn
        else:
            from javdb.storage.db import OPERATIONS_DB_PATH, get_db
            ac = stack.enter_context(get_db(OPERATIONS_DB_PATH))
        shadow_rows = sc.execute(
            "SELECT qb_hash, state FROM AcquisitionOutcomeShadow"
        ).fetchall()
        auth_rows = ac.execute(
            "SELECT qb_hash, state FROM AcquisitionOutcome"
        ).fetchall()
    return shadow_rows, auth_rows
