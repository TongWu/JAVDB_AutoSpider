"""Session state management for JAVDB AutoSpider.

Manages the active session context (Session ID, Run ID, Write Mode) used by
the spider pipeline. These values are stored in module-global variables so
subprocess workers and the main pipeline share a single "current session"
once the spider sets it via set_active_session_id().

Session IDs are application-generated TEXT values in the format:
    YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS
where TTTT is a 4-digit hex process tag and SSSS is a 4-digit hex counter.
This format is human-readable, sortable, and round-trips losslessly through
JSON to Cloudflare D1 (avoiding IEEE-754 precision loss with large integers).
"""

import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# ── Active session context ───────────────────────────────────────────────

_active_session_id_lock = threading.Lock()
_active_session_id_value: Optional[str] = None
_active_run_id_value: Optional[str] = None
_active_run_attempt_value: Optional[int] = None
_active_write_mode_value: Optional[str] = None

# Allowed write modes
_ALLOWED_WRITE_MODES = {'audit', 'pending'}


# ── Session ID setters/getters ───────────────────────────────────────────


def set_active_session_id(session_id: Optional[str]) -> None:
    """Set the current pipeline ReportSessions.Id.

    Called by the spider once after creating the report session. All
    subsequent db_upsert_history / db_batch_update_last_visited /
    db_batch_update_movie_actors / etc. that don't pass an explicit
    session_id= will tag their writes with this value (and audit
    rows where applicable).

    Pass None to clear the context (e.g. between pipeline phases in
    long-lived processes / tests).

    Args:
        session_id: Session identifier (TEXT format) or None to clear
    """
    global _active_session_id_value
    with _active_session_id_lock:
        _active_session_id_value = session_id


def get_active_session_id() -> Optional[str]:
    """Return the currently-active ReportSessions.Id or None.

    Returns:
        Session ID string or None if not set
    """
    with _active_session_id_lock:
        return _active_session_id_value


# ── Session ID resolution ───────────────────────────────────────────────

_SESSION_ID_SENTINEL = object()


def _resolve_session_id(explicit=_SESSION_ID_SENTINEL) -> Optional[str]:
    """Pick the explicit override or fall back to the active context."""
    if explicit is _SESSION_ID_SENTINEL:
        return get_active_session_id()
    return explicit


# ── Run identity setters/getters ─────────────────────────────────────────


def set_active_run_identity(
    run_id: Optional[str],
    run_attempt: Optional[int],
) -> None:
    """Set the GitHub Actions workflow identity for subsequent audit rows.

    Called by the spider alongside set_active_session_id() so that
    every MovieHistoryAudit / TorrentHistoryAudit row written by
    this process is stamped with the run that produced it. Allows the
    rollback CLI to look up audit rows by (RunId, RunAttempt) —
    independent of any ReportSessions.Id drift between SQLite and D1.

    Args:
        run_id: GitHub Actions run_id (e.g., github.run_id)
        run_attempt: GitHub Actions run_attempt (e.g., github.run_attempt)
    """
    global _active_run_id_value, _active_run_attempt_value
    with _active_session_id_lock:
        _active_run_id_value = run_id
        _active_run_attempt_value = (
            int(run_attempt) if run_attempt is not None else None
        )


def get_active_run_identity() -> Tuple[Optional[str], Optional[int]]:
    """Return (RunId, RunAttempt) from the active session context.

    Returns:
        Tuple of (run_id, run_attempt), both may be None
    """
    with _active_session_id_lock:
        return _active_run_id_value, _active_run_attempt_value


# ── Write mode setters/getters ───────────────────────────────────────────


def set_active_write_mode(write_mode: Optional[str]) -> None:
    """Pin the active session's WriteMode for the current process.

    Set by the spider (and the rclone staging session) immediately after
    db_create_report_session() so the write-path helpers
    (save_parsed_movie_to_history, etc.) can branch to
    db_stage_history_write() without re-reading ReportSessions
    for every movie. Pass None to clear (e.g. between phases in
    long-lived processes / tests).

    Args:
        write_mode: 'audit' or 'pending', or None to clear

    Raises:
        ValueError: If write_mode is not 'audit' or 'pending'
    """
    global _active_write_mode_value
    if write_mode is not None:
        write_mode = _resolve_write_mode(write_mode)
    with _active_session_id_lock:
        _active_write_mode_value = write_mode


def get_active_write_mode() -> str:
    """Return the resolved active WriteMode ('audit' or 'pending').

    Resolution order:
      1. Process-local override set by set_active_write_mode().
      2. Env var JAVDB_HISTORY_WRITE_MODE.
      3. Default 'audit'.

    Returns:
        'audit' or 'pending'
    """
    with _active_session_id_lock:
        cached = _active_write_mode_value
    if cached:
        return cached
    return _resolve_write_mode(None)


def _resolve_write_mode(explicit: Optional[str]) -> str:
    """Return a validated WriteMode ('audit' or 'pending').

    Resolution order:
      1. Explicit argument (when set).
      2. JAVDB_HISTORY_WRITE_MODE env var.
      3. Default 'audit' so the historic X3 path stays in effect for
         every workflow that has not opted in.

    Args:
        explicit: Explicit write mode override

    Returns:
        'audit' or 'pending'

    Raises:
        ValueError: If write mode is not 'audit' or 'pending'
    """
    candidate = explicit
    if candidate is None:
        candidate = os.environ.get("JAVDB_HISTORY_WRITE_MODE")
    if not candidate:
        return "audit"
    candidate = candidate.strip().lower()
    if candidate not in _ALLOWED_WRITE_MODES:
        raise ValueError(
            f"Unknown WriteMode {candidate!r}; "
            f"expected one of {_ALLOWED_WRITE_MODES}"
        )
    return candidate


# ── Session ID generation ────────────────────────────────────────────────

# Why TEXT session IDs instead of INTEGER AUTOINCREMENT?
#
# Under STORAGE_BACKEND=dual the SQLite-side and D1-side AUTOINCREMENT
# counters are independent, and any past asymmetric INSERT (one side
# committed, the other failed) leaves them permanently out of sync.
# cur.lastrowid returns whichever backend the cursor wraps; trusting it
# as SessionId for downstream tables caused the 2026-05-08 incident where
# the local id 332 collided with a stale 332 on D1 from a prior run.
#
# Why not an INTEGER snowflake? Cloudflare D1's HTTP /query endpoint parses
# JSON parameters and serializes result rows through a JS layer whose Number
# type is IEEE-754 double. Any integer with |x| > 2**53 - 1 silently loses
# precision in transit. A 63-bit snowflake (today's IDs are ~7e18) overruns
# that ceiling by ~780×, so the local SQLite value and the D1-stored value
# diverge — breaking every downstream join keyed on SessionId (2026-05-12).
#
# Solution: store ReportSessions.Id as TEXT in a human-readable, sortable
# format that round-trips losslessly through JSON.
#
# Layout: YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS (33 chars, fixed width),
# where TTTT is 4 lowercase hex digits of per-process random tag (16
# bits, ~256-concurrent-process birthday bound) and SSSS is 4 hex digits
# of in-process monotonic counter that resets every microsecond. Fixed
# width and zero-padded throughout, so lexicographic sort equals
# chronological sort.

_SESSION_ID_LOCK = threading.Lock()
_SESSION_ID_LAST: str = ""
_SESSION_ID_LAST_US: int = -1
_SESSION_ID_COUNTER: int = 0
_SESSION_ID_PROCESS_TAG_BITS = 16
_SESSION_ID_PROCESS_TAG = secrets.randbits(_SESSION_ID_PROCESS_TAG_BITS)
_SESSION_ID_TAG_HEX = f"{_SESSION_ID_PROCESS_TAG:04x}"

# Regex matching the canonical session-id shape. Useful for tests and
# defensive validation in callers (e.g. rollback CLI that takes an id from
# operator input). Old-format decimal-string ids minted before the
# 2026-05-13 migration won't match — that's intentional.
SESSION_ID_PATTERN = re.compile(
    r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{4}-[0-9a-f]{4}$"
)


def generate_session_id() -> str:
    """Return a TEXT session id suitable for ReportSessions.Id.

    Format: YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS (UTC, microsecond
    precision, per-process random 16-bit tag, in-process monotonic 16-bit
    counter that resets every microsecond). Strictly increasing within a
    process under lexicographic ordering; round-trips losslessly through
    JSON to Cloudflare D1.

    Returns:
        Session ID string (33 characters, fixed width)

    Example:
        >>> generate_session_id()
        '20260515T143022.123456Z-a3f2-0001'
    """
    global _SESSION_ID_LAST, _SESSION_ID_LAST_US, _SESSION_ID_COUNTER
    with _SESSION_ID_LOCK:
        us = time.time_ns() // 1_000
        if us == _SESSION_ID_LAST_US:
            _SESSION_ID_COUNTER += 1
        else:
            _SESSION_ID_LAST_US = us
            _SESSION_ID_COUNTER = 0
        # Wrap-around guard: 16-bit counter can only represent 65 536 ids
        # within a single microsecond; bump to next µs if exhausted (an
        # absurd burst rate, but better to spend a µs of skew than mint a
        # duplicate id).
        if _SESSION_ID_COUNTER > 0xFFFF:
            _SESSION_ID_LAST_US += 1
            _SESSION_ID_COUNTER = 0
            us = _SESSION_ID_LAST_US
        dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
        ts = dt.strftime("%Y%m%dT%H%M%S") + f".{us % 1_000_000:06d}Z"
        candidate = f"{ts}-{_SESSION_ID_TAG_HEX}-{_SESSION_ID_COUNTER:04x}"
        if candidate <= _SESSION_ID_LAST:
            # Clock went backwards (NTP step, VM resume). Force monotonicity
            # by appending an extra counter increment beyond the last seen
            # id; tag stays stable, so we extend via the µs portion.
            _SESSION_ID_LAST_US += 1
            _SESSION_ID_COUNTER = 0
            us = _SESSION_ID_LAST_US
            dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
            ts = dt.strftime("%Y%m%dT%H%M%S") + f".{us % 1_000_000:06d}Z"
            candidate = f"{ts}-{_SESSION_ID_TAG_HEX}-0000"
        _SESSION_ID_LAST = candidate
        return candidate


def is_valid_session_id(session_id: str) -> bool:
    """Check if a session ID matches the canonical format.

    Args:
        session_id: Session ID string to validate

    Returns:
        True if session_id matches the canonical format, False otherwise

    Example:
        >>> is_valid_session_id('20260515T143022.123456Z-a3f2-0001')
        True
        >>> is_valid_session_id('12345')  # Old decimal format
        False
    """
    return SESSION_ID_PATTERN.match(session_id) is not None


# ── Integer ID generation (for INTEGER PRIMARY KEY tables) ───────────────

# MovieHistory.Id and TorrentHistory.Id are declared INTEGER, so they cannot
# use the TEXT session-id format above. To keep dual-write consistent we
# need to supply explicit ids that are identical on both SQLite and D1.
#
# Constraints:
#   • Must stay within D1's JSON-safe range: |x| < 2**53
#   • Must be strictly increasing within a process so two concurrent
#     inserts cannot collide
#   • Must be unlikely to collide across processes (dual-mode multi-runner)
#
# Layout (52 bits, little-headroom below 2**53):
#   relative_ms (40 bits) — ms since 2026-01-01T00:00:00Z; overflows year 2060
#   process_tag  (6 bits) — secrets.randbits(6) per process (64 slots)
#   counter      (6 bits) — monotonic per-ms in-process counter (64 per ms)

_INT_ID_EPOCH_BASE_MS: int = 1_735_689_600_000  # 2026-01-01T00:00:00Z
_INT_ID_PROCESS_TAG: int = secrets.randbits(6)
_INT_ID_LOCK = threading.Lock()
_INT_ID_LAST_MS: int = -1
_INT_ID_COUNTER: int = 0


def generate_integer_id() -> int:
    """Return a 52-bit integer PK for INTEGER PRIMARY KEY tables.

    Safe for Cloudflare D1 JSON transport (all values < 2**53).
    Strictly increasing within a process; monotonicity forced on clock skew.

    Returns:
        Integer ID (52 bits, < 2**53)

    Example:
        >>> generate_integer_id()
        123456789012345
    """
    global _INT_ID_LAST_MS, _INT_ID_COUNTER
    with _INT_ID_LOCK:
        ms = int(time.time() * 1000) - _INT_ID_EPOCH_BASE_MS
        if ms > _INT_ID_LAST_MS:
            _INT_ID_LAST_MS = ms
            _INT_ID_COUNTER = 0
        else:
            # Same ms or clock went backwards — stay monotonic on _INT_ID_LAST_MS.
            _INT_ID_COUNTER += 1
            if _INT_ID_COUNTER >= 64:
                # Counter exhausted within this ms; bump to next ms.
                _INT_ID_LAST_MS += 1
                _INT_ID_COUNTER = 0
                ms = _INT_ID_LAST_MS
        # Pack: 40 bits ms + 6 bits tag + 6 bits counter
        return (ms << 12) | (_INT_ID_PROCESS_TAG << 6) | _INT_ID_COUNTER
