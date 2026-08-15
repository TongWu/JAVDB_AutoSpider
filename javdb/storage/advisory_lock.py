"""Lease-based advisory lock built on the ``system_state`` KV table.

Serialises a destructive job that more than one workflow can launch. First
user: the rclone dedup executor (:mod:`javdb.integrations.rclone.manager.service`)
— ``WeeklyDedup.yml`` and ``RcloneManager.yml`` share a GitHub ``concurrency``
group, but ``DailyIngestion.yml`` and ``AdHocIngestion.yml`` invoke the same
CLI *outside* that group (ad-hoc dispatches are parallel by design), so two
runners can otherwise drain — and purge — the same pending ``DedupRecords``
set at once.

Row shape — one ``system_state`` row per lock, in the operations DB::

    key   = 'lock:<name>'
    value = '<expires_at>|<owner>'    e.g. '2026-08-12T18:04:07Z|gha-42-1'

``expires_at`` is a fixed-width (20-char) ISO-8601 UTC timestamp, so a lease
deadline can be compared *inside* SQL with ``substr(value, 1, 20)`` — ASCII
order and chronological order coincide. Acquire is therefore a single
conditional upsert and release a single conditional delete: one statement each,
atomic on both backends (sqlite3 applies a statement atomically; D1
auto-commits per statement).

Why acquire verifies by read-back
---------------------------------
The conditional upsert is the atomic gate on each backend, but its affected row
count is *not* a cross-backend verdict. Under ``STORAGE_BACKEND=dual`` the
count a caller sees is the **local SQLite** leg's (``DualCursor.rowcount``,
``dual_connection.py:384-386``) and every GitHub runner carries its own mirror
file — so two dual-mode runners can each read ``rowcount == 1`` while only one
of them actually won on D1, and both would then drain the same pending
``DedupRecords`` set. Reads, in contrast, are routed to D1 alone
(``DualConnection.execute``, ``dual_connection.py:621-622``). Acquire therefore
re-reads the lock row and claims the lease **only** when the stored value
equals this attempt's exact token, which makes D1 — the canonical side — the
referee on every backend. It also drops the dependency on a backend reporting
``meta.changes`` at all; the row count is kept for logging only. Anything other
than our own token reads as "not acquired", which is the safe direction.

How far the lock actually reaches
--------------------------------
The lease is exactly as shared as the operations DB behind it, so
``STORAGE_BACKEND`` decides its authority:

* ``d1`` — **shared and authoritative.** One row on Cloudflare D1 referees
  every runner. This is the production setting, and the only one that delivers
  cross-runner mutual exclusion.
* ``dual`` — **shared, with one residual.** The deciding read-back is served by
  D1, but if D1 is unreachable a dual read falls back to the local SQLite
  mirror (``dual_connection.py:626-630``) and the verdict degrades to that
  mirror's — i.e. to the machine-local case below. Pre-existing exposure.
* ``sqlite`` — **machine-local only.** The lock row lives in this machine's own
  ``operations.db``, so it serialises concurrent runs on one host (still worth
  having for a single-machine self-hoster), but two GitHub runners would each
  acquire "the" lease in their own checkout and both proceed. Callers whose
  safety argument depends on the lease must say so out loud rather than imply a
  guarantee: the dedup executor logs a prominent warning under this backend
  (:mod:`javdb.integrations.rclone.manager.service`).

Release stays row-count based and is already safe cross-backend: the
conditional DELETE is keyed on the *exact* token, so a loser's token matches no
row on D1, and the only row it can ever delete is its own stale mirror entry.

Mirror drift is accepted rather than engineered around: a dual-mode loser
leaves a lock row in its local SQLite that D1 does not have. The row is
ephemeral (it expires on its own) and D1 stays canonical, so — per the
project's "drift always resolves toward D1" rule — nothing reconciles it.

This is an advisory lock, not a queue: a caller that loses the race is expected
to skip its work. Nothing here blocks, waits or retries. Storage errors are not
swallowed — they propagate so each caller can choose its own degrade policy.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# A lease only has to outlive the work it guards. The rclone dedup executor
# purges folder-by-folder over a remote drive, so a large backlog can keep it
# busy for hours — 6h leaves generous headroom. It is also how long a crashed
# runner's lease stays un-stealable, hence hours rather than days.
DEFAULT_LEASE_TTL_SECONDS = 6 * 60 * 60

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# len('2026-08-12T18:04:07Z') — fixed width so substr() can slice the deadline
# back out of ``value`` and compare it as a string.
_TS_WIDTH = 20
_SEPARATOR = "|"

# Conditional upsert: inserts when no lease row exists, overwrites when the
# stored lease has expired, and changes nothing while a live lease is held.
_ACQUIRE_SQL = f"""
INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)
ON CONFLICT(key) DO UPDATE SET
    value      = excluded.value,
    updated_at = excluded.updated_at
WHERE substr(system_state.value, 1, {_TS_WIDTH}) <= ?
"""

# Conditional delete: only the exact lease we wrote is removed, so a lease that
# expired and was stolen meanwhile is left alone.
_RELEASE_SQL = "DELETE FROM system_state WHERE key = ? AND value = ?"

_READ_SQL = "SELECT value FROM system_state WHERE key = ?"


@dataclass(frozen=True)
class Lease:
    """A lease this process currently believes it holds."""

    key: str
    owner: str
    expires_at: str

    @property
    def token(self) -> str:
        """The exact ``value`` written for this lease (owner + deadline)."""
        return f"{self.expires_at}{_SEPARATOR}{self.owner}"


class LockAttempt(NamedTuple):
    """Outcome of :func:`try_acquire`.

    ``lease`` is ``None`` when a live lease is held elsewhere; ``holder`` then
    describes the current holder so the caller can name it while skipping.
    """

    lease: Optional[Lease]
    holder: Optional[str]


def default_owner() -> str:
    """Identify this process: the GitHub run when available, else host + pid."""
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        return f"gha-{run_id}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    return f"{socket.gethostname()}-{os.getpid()}"


def try_acquire(
    key: str,
    *,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    owner: Optional[str] = None,
    now: Optional[datetime] = None,
) -> LockAttempt:
    """Take the lease for *key* if it is free or expired. Never blocks.

    Success is decided by reading the lock row back and requiring it to equal
    this attempt's exact token — not by the upsert's row count (see the module
    docstring for the dual-backend reason). Attempts are distinguished by the
    whole token, deadline *and* owner, so two of them could only be confused by
    sharing an owner string and landing on the same second, i.e. two processes
    inside one GitHub run — and each guarded workflow invokes its locked step
    once per run.

    *now* is injectable so tests can cross an expiry without sleeping.
    Raises whatever the storage layer raises (missing table, D1 unavailable);
    callers decide whether that means fail-open or fail-closed.
    """
    owner = owner or default_owner()
    started_at = now or datetime.now(timezone.utc)
    now_ts = _format(started_at)
    lease = Lease(
        key=key,
        owner=owner,
        expires_at=_format(started_at + timedelta(seconds=ttl_seconds)),
    )

    with _operations_db() as conn:
        # Read first, for logging only — the read-back after the upsert decides.
        prior = _read_value(conn, key)
        cursor = conn.execute(_ACQUIRE_SQL, (key, lease.token, now_ts, now_ts))
        # Kept for the log line only: under dual this count is the local SQLite
        # mirror's (dual_connection.py:384-386), so a "1" here proves nothing
        # about D1. The read-back below is served by D1
        # (dual_connection.py:621-622) and is what decides.
        rowcount = getattr(cursor, "rowcount", None)
        stored = _read_value(conn, key)
        if stored == lease.token:
            if prior:
                logger.warning(
                    "Stole expired advisory lease %s from %s (lease expired at %s)",
                    key, _owner_of(prior), _deadline_of(prior),
                )
            logger.info(
                "Acquired advisory lease %s for %s (expires %s)",
                key, owner, lease.expires_at,
            )
            return LockAttempt(lease=lease, holder=None)

        holder = _describe(stored or prior)
        logger.warning(
            "Advisory lease %s is held by %s — %s did not acquire it "
            "(conditional upsert reported rowcount=%s)",
            key, holder, owner, rowcount,
        )
        return LockAttempt(lease=None, holder=holder)


def release(lease: Lease) -> bool:
    """Release *lease*; returns False when it was no longer ours.

    Row-count based on purpose: the DELETE is keyed on the exact token, so it
    can only ever remove a row this process wrote. Under dual that makes the
    SQLite-sourced count harmless — a token that never won on D1 matches
    nothing there, and clearing the loser's own stale mirror row is exactly the
    cleanup we want.
    """
    with _operations_db() as conn:
        cursor = conn.execute(_RELEASE_SQL, (lease.key, lease.token))
        released = cursor.rowcount == 1
    if released:
        logger.info("Released advisory lease %s held by %s", lease.key, lease.owner)
        return True
    logger.warning(
        "Advisory lease %s was no longer held by %s at release time "
        "(expired and taken over?) — nothing deleted",
        lease.key, lease.owner,
    )
    return False


# ── internals ────────────────────────────────────────────────────────────


def _operations_db():
    """Open the operations DB (imported lazily: tests repoint the path)."""
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db

    return get_db(OPERATIONS_DB_PATH)


def _read_value(conn, key: str) -> Optional[str]:
    row = conn.execute(_READ_SQL, (key,)).fetchone()
    if row is None:
        return None
    # D1 returns dicts; sqlite3.Row supports both name and index access.
    try:
        return row["value"]
    except (KeyError, TypeError, IndexError):
        return row[0]


def _format(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("advisory lock timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime(_TS_FORMAT)


def _deadline_of(value: str) -> str:
    return value[:_TS_WIDTH]


def _owner_of(value: str) -> str:
    return value[_TS_WIDTH + len(_SEPARATOR):] or "<unknown>"


def _describe(value: Optional[str]) -> str:
    if not value:
        return "<unknown holder>"
    return f"{_owner_of(value)} (lease expires {_deadline_of(value)})"
