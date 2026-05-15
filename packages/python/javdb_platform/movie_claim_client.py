"""HTTP client for the Cloudflare Worker + Durable Object movie claim coordinator.

Sister to :mod:`packages.python.javdb_platform.login_state_client` and
:mod:`packages.python.javdb_platform.proxy_coordinator_client`; targets the
``MovieClaimState`` per-day-sharded Durable Object.

P1-B mutex: when two GH Actions runners would otherwise race to fetch the
same JavDB ``/v/<id>`` detail page, the runner that "wins" the
:meth:`MovieClaimClient.claim` call gets exclusive access until it completes
or releases.  Other runners observe ``acquired=False`` and either back off
(``already_completed=False``) or skip + record local history
(``already_completed=True``).

Design constraints (mirror those of :mod:`proxy_coordinator_client` /
:mod:`login_state_client`):

- One HTTP round-trip per call; **no retries** on failure.  Callers MUST
  treat :class:`MovieClaimUnavailable` as a fail-open signal and fall
  back to the existing per-process dedup / direct fetch.
- A short timeout (default 5 s) bounds the worst-case impact of a slow
  Worker; the spider's hot path is never blocked for more than that.
- The client is thread-safe: each call constructs its own request and the
  underlying ``requests.Session`` only stores connection-pool metadata.

Wire format and field semantics live in
``JAVDB_AutoSpider_Proxycoordinator/src/types.ts`` (the ``MovieClaim*``
type cluster).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import requests

from packages.python.javdb_platform import config_helper
from packages.python.javdb_platform.do_client_base import (
    BaseDOClient,
    DOClientUnavailable,
)
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_USER_AGENT = "javdb-spider-movie-claim-client/1.0"

# ── three-state ``MOVIE_CLAIM_ENABLED`` semantics ─────────────────────────
#
# Operations historically had to keep ``MOVIE_CLAIM_ENABLED`` in sync with
# the actual number of live runners by hand: enable when ≥2 runners are
# scheduled, disable for single-runner deploys.  The auto-toggle replaces
# that with a registry-driven signal, so the env-var grows from "boolean
# enable" to a tri-state mode selector:
#
#   ``auto``  (new default) — driven by ``RunnerRegistry.movie_claim_recommended``
#                              on every register / heartbeat.
#   ``true`` / ``1`` / ``yes`` — force-on (legacy P1-B behaviour).  Runner
#                                 mounts the global client unconditionally
#                                 and ignores the registry signal.  This
#                                 is the operator's escape hatch for the
#                                 mixed old-Worker / new-client window.
#   ``false`` / ``0`` / ``no`` / empty — force-off.  Runner pays zero claim
#                                         overhead, identical to the
#                                         pre-auto world.
#
# Comparison is case-insensitive after ``str.strip()``.  Unknown values
# fall back to ``auto`` so a typo doesn't silently disable the mutex on
# a multi-runner deploy.
_MOVIE_CLAIM_FORCE_ON_VALUES = frozenset({"1", "true", "yes"})
_MOVIE_CLAIM_OFF_VALUES = frozenset({"0", "false", "no", ""})
_MOVIE_CLAIM_AUTO_VALUES = frozenset({"auto"})

#: Mode constants returned by :func:`parse_movie_claim_mode`.
MOVIE_CLAIM_MODE_OFF = "off"
MOVIE_CLAIM_MODE_AUTO = "auto"
MOVIE_CLAIM_MODE_FORCE_ON = "force_on"


def parse_movie_claim_mode(raw: Optional[str]) -> str:
    """Translate a ``MOVIE_CLAIM_ENABLED`` value to one of three modes.

    Returns one of :data:`MOVIE_CLAIM_MODE_OFF` /
    :data:`MOVIE_CLAIM_MODE_AUTO` / :data:`MOVIE_CLAIM_MODE_FORCE_ON`.
    Unknown values resolve to ``auto`` so a typo on a multi-runner
    deploy errs on the side of "let the registry decide" instead of
    silently disabling the mutex.

    Args:
        raw: Env-var value as captured from ``os.environ`` /
            ``config.MOVIE_CLAIM_ENABLED``.  ``None`` and empty strings
            are treated as "explicitly off" — this matches the ergonomic
            expectation that ``MOVIE_CLAIM_ENABLED=`` (empty) disables
            the feature, regardless of the new ``auto`` default applied
            when the variable is *unset altogether*.  The "unset"
            distinction is enforced by the caller via the ``defaulted``
            wrapper in :func:`create_movie_claim_client_with_mode_from_env`.
    """
    if raw is None:
        return MOVIE_CLAIM_MODE_OFF
    cleaned = raw.strip().lower()
    if cleaned in _MOVIE_CLAIM_FORCE_ON_VALUES:
        return MOVIE_CLAIM_MODE_FORCE_ON
    if cleaned in _MOVIE_CLAIM_OFF_VALUES:
        return MOVIE_CLAIM_MODE_OFF
    if cleaned in _MOVIE_CLAIM_AUTO_VALUES:
        return MOVIE_CLAIM_MODE_AUTO
    # Unknown value (e.g. typo "ato" or "trure") — fall back to ``auto``.
    # Logging here would spam at every spider call; callers that care
    # about the literal value (factories) log once per process at
    # construction time.
    return MOVIE_CLAIM_MODE_AUTO

# Mirror of ``DEFAULT_MOVIE_CLAIM_TTL_MS`` /
# ``MOVIE_CLAIM_TTL_{MIN,MAX}_MS`` on the Worker side.  Exposed here so
# callers can stay within range without an extra round-trip; the Worker
# still clamps defensively if a caller goes over.
DEFAULT_CLAIM_TTL_MS = 30 * 60 * 1000          # 30 min
CLAIM_TTL_MIN_MS = 60_000                       # 1 min
CLAIM_TTL_MAX_MS = 2 * 60 * 60 * 1000           # 2 h


# Operational time zone used for per-day sharding.  Asia/Singapore is
# UTC+08:00 and DST-free, so a fixed-offset tz is exact and matches the
# Worker's ``currentSingaporeDate`` helper without an IANA tz-data
# dependency in either runtime.
_OPS_TZ = timezone(timedelta(hours=8))


def current_shard_date() -> str:
    """Return the per-day shard identifier for "right now" in operations TZ.

    Format mirrors the Worker's ``YYYY-MM-DD``.  Callers that need to claim
    the same href across the day boundary (e.g. an ingestion that spans
    midnight) MUST pin a single shard date at *task dispatch time* and pass
    it explicitly to every claim/release/complete call so the same movie
    always maps to the same shard — calling :func:`current_shard_date` at
    each step would otherwise re-fragment the claim across two shards.
    """
    return datetime.now(_OPS_TZ).strftime("%Y-%m-%d")


class MovieClaimUnavailable(DOClientUnavailable):
    """Raised when the movie claim Worker cannot be reached or returns an error.

    This is a *signal*, not a panic.  Every callsite in the spider treats
    it as "fall back to per-process dedup" — the local in-memory
    ``_completed_entries`` / ``_in_flight`` sets continue to function
    exactly as before, and the worst-case outcome is two runners
    independently fetching the same detail page (the legacy behaviour).
    """


@dataclass(frozen=True)
class ClaimResult:
    """Reply from ``POST /claim_movie``.

    Four exhaustive cases — pattern-match on the booleans + cooldown:

    1. ``acquired=True`` → caller now owns the claim; proceed with fetch.
       Caller MUST eventually call :meth:`complete` (success) or
       :meth:`release` (abort / retry handoff) to free the slot.
       (P2-A: ``fail_count`` may be > 0 in the rare case of a renewal
       mid-failure-window — the previous failures are surfaced for ops
       visibility but do NOT block the renewal.)
    2. ``acquired=False, already_completed=True`` → another runner has
       already finished this href in the same per-day shard; skip + mark
       local history.
    3. ``acquired=False, cooldown_until > server_time_ms`` (P2-A) →
       href is in cooldown after repeated failures.  The caller MUST
       NOT retry before ``cooldown_until``; the back-off is encoded
       server-side (``MOVIE_CLAIM_COOLDOWN_LADDER_MS``).
    4. ``acquired=False, already_completed=False, cooldown_until=0`` →
       another runner is *currently* working on the href; back off and
       retry later (the plan recommends 60–120 s).

    P2-A fields default to ``0`` / empty string when the Worker is on
    a pre-P2-A deploy that omits them — the caller transparently treats
    such a response as "no cooldown info" (= legacy P1-B semantics).
    """

    acquired: bool
    current_holder_id: str
    expires_at: int
    already_completed: bool
    server_time_ms: int
    cooldown_until: int = 0
    last_error_kind: str = ""
    fail_count: int = 0
    #: Phase-1 — ``session_id`` of the staged_complete entry blocking this
    #: href, if any.  Empty string when no staged entry exists.  When the
    #: request supplied a matching ``session_id`` the DO sets
    #: ``already_completed=True`` AND echoes this field so the client can
    #: confirm the same-session idempotent path.  When the staged entry
    #: belongs to a *different* session ``already_completed`` is ``False``
    #: and the claim proceeds normally — the field is informational.
    staged_session_id: str = ""


@dataclass(frozen=True)
class ReleaseResult:
    """Reply from ``POST /release_movie``.

    ``released`` is ``False`` when the caller is not the current holder
    (e.g. its claim already expired and was reclaimed by another runner,
    or another runner already completed the href).  The caller can safely
    treat this as a no-op — the slot is gone either way.
    """

    released: bool
    server_time_ms: int


@dataclass(frozen=True)
class CompleteResult:
    """Reply from ``POST /complete_movie``.

    ``completed`` is ``True`` when the href is now in the shard's
    ``completed_committed[]`` list — either freshly added by this caller
    or already present from a previous successful complete (idempotent).
    ``False`` means a stale-holder complete (the active claim belongs to
    someone else); the caller should typically log + retry from a fresh
    claim.

    Note: this endpoint is the legacy P1-B path that commits the href
    immediately; Phase-1 callers should prefer :meth:`MovieClaimClient.stage_complete`
    so a downstream rollback can erase the runner's footprint.
    """

    completed: bool
    href: str
    server_time_ms: int


@dataclass(frozen=True)
class StageCompleteResult:
    """Reply from ``POST /stage_complete_movie`` (Phase-1).

    ``staged`` is ``True`` when the href now has a staged completion in
    the shard's ``staged_complete{}`` map — either fresh, or an
    idempotent re-stage by the same ``session_id``.  ``False`` means
    one of three rejection paths:

    * The reporting holder is not the active claim holder
      (stale-holder);
    * Another session already staged this href; ``session_id`` echoes
      the *winner's* session so the caller can decide whether to wait
      for that session's commit / rollback before retrying;
    * The href is already in ``completed_committed[]`` (the call still
      returns ``staged=True`` here — the work is durable, no further
      action needed).

    A staged entry survives until the spider's session-end CLI calls
    :meth:`commit_completed_movies` (promotes to durable) or
    :meth:`rollback_staged_movies` (drops it without affecting peers).
    """

    staged: bool
    href: str
    session_id: str
    server_time_ms: int


@dataclass(frozen=True)
class CommitCompletedMoviesResult:
    """Reply from ``POST /commit_completed_movies`` (Phase-1)."""

    promoted: int
    session_id: str
    server_time_ms: int


@dataclass(frozen=True)
class RollbackStagedMoviesResult:
    """Reply from ``POST /rollback_staged_movies`` (Phase-1)."""

    removed: int
    session_id: str
    server_time_ms: int


@dataclass(frozen=True)
class SweepOrphanStagesResult:
    """Reply from ``GET /sweep_orphan_stages`` (Phase-1, cron-only)."""

    removed: int
    cutoff_ms: int
    server_time_ms: int


@dataclass(frozen=True)
class StatusResult:
    """Reply from ``GET /movie_status?href=...&date=YYYY-MM-DD``.

    Used for ops debugging only; the spider's hot path never calls this.
    P2-A fields default to ``0`` / empty string for backward-compat
    with pre-P2-A Worker deploys.
    """

    current_holder_id: Optional[str]
    expires_at: int
    already_completed: bool
    server_time_ms: int
    cooldown_until: int = 0
    last_error_kind: str = ""
    fail_count: int = 0
    #: Phase-1 — session_id owning the current ``staged_complete`` entry,
    #: or empty string when none.  Surfaced so ops can correlate
    #: staged-but-not-committed state via /movie_status.
    staged_session_id: str = ""
    #: Phase-1 — wall-clock ms epoch when the current staged_complete
    #: entry was recorded.  ``0`` when no staged entry exists.
    staged_at: int = 0


@dataclass(frozen=True)
class ReportFailureResult:
    """Reply from ``POST /report_failure`` (P2-A).

    ``dead_lettered`` is ``True`` once ``fail_count`` crosses the
    server-side dead-letter threshold (default 8); the caller can use
    this to short-circuit further retries for the rest of the shard's
    lifetime.
    """

    fail_count: int
    cooldown_until: int
    dead_lettered: bool
    server_time_ms: int


def _extract_server_time_ms(data: dict) -> int:
    """Read the server-side timestamp from a response.

    Module-level alias of :meth:`BaseDOClient._extract_server_time_ms`
    so existing tests / call-sites keep importing it from this module
    unchanged.
    """
    return BaseDOClient._extract_server_time_ms(data)


class MovieClaimClient(BaseDOClient):
    """HTTP client for the MovieClaimState DO.

    Construct once per process and pass into the runtime's detail-fetch
    pipeline.  All four methods are blocking and short-lived; the spider's
    hot path is bounded by ``timeout`` (5 s default) on every call.

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``
            (same Worker that hosts the per-proxy throttle + login-state endpoints).
        token: Bearer token (must match the secret set via
            ``wrangler secret put PROXY_COORDINATOR_TOKEN``).
        timeout: Per-request HTTP timeout in seconds.
        user_agent: Optional override for the ``User-Agent`` header.
    """

    _unavailable_exc = MovieClaimUnavailable

    # -- public API ---------------------------------------------------------

    def claim(
        self,
        href: str,
        holder_id: str,
        *,
        ttl_ms: int = DEFAULT_CLAIM_TTL_MS,
        date: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ClaimResult:
        """Try to acquire (or renew) the claim on *href* for the current shard.

        ``date`` defaults to "today in Asia/Singapore" via
        :func:`current_shard_date`.  Long-running ingestions MUST pin a
        date at task dispatch time and pass it explicitly; otherwise the
        same href could land in two shards across midnight and lose
        cross-runner exclusivity.

        ``session_id`` (Phase-1) ties the claim to the spider's
        ``ReportSessions.Id`` so a peer session's staged completion does
        not block this caller, while a same-session staged completion is
        treated as ``already_completed=True`` (idempotent skip).  Pass
        ``None`` to reproduce legacy P1-B semantics where any staged
        completion is ignored on the read path.

        Returns immediately with a :class:`ClaimResult`; raises
        :class:`MovieClaimUnavailable` on any failure (timeout, non-2xx,
        connection error, malformed response).
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "ttl_ms": int(ttl_ms),
            "date": date or current_shard_date(),
        }
        if session_id is not None and session_id != "":
            body["session_id"] = str(session_id)
        resp = self._do_request("POST", "/claim_movie", body)
        try:
            return ClaimResult(
                acquired=bool(resp["acquired"]),
                current_holder_id=str(resp.get("current_holder_id", "") or ""),
                expires_at=int(resp.get("expires_at", 0) or 0),
                already_completed=bool(resp.get("already_completed", False)),
                server_time_ms=_extract_server_time_ms(resp),
                cooldown_until=int(resp.get("cooldown_until", 0) or 0),
                last_error_kind=str(resp.get("last_error_kind", "") or ""),
                fail_count=int(resp.get("fail_count", 0) or 0),
                staged_session_id=str(resp.get("staged_session_id", "") or ""),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed claim response: {resp!r} ({e})"
            ) from e

    def release(
        self,
        href: str,
        holder_id: str,
        *,
        date: Optional[str] = None,
    ) -> ReleaseResult:
        """Relinquish a held claim on *href* (e.g. after a fetch failure).

        Non-owner releases are silently ignored by the Worker
        (``released:false``); the caller can fire-and-forget without a
        prior ownership check — the post-condition "this runner no longer
        holds the claim" is true either way.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/release_movie", body)
        try:
            return ReleaseResult(
                released=bool(resp["released"]),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed release response: {resp!r} ({e})"
            ) from e

    def complete(
        self,
        href: str,
        holder_id: str,
        *,
        date: Optional[str] = None,
    ) -> CompleteResult:
        """Mark *href* as fully processed for this shard.

        Subsequent claims on the same href in the same shard return
        ``acquired=False, already_completed=True`` so peer runners can
        skip the fetch entirely.  Idempotent: repeated completes return
        ``completed=True`` even from non-holders.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/complete_movie", body)
        try:
            return CompleteResult(
                completed=bool(resp["completed"]),
                href=str(resp.get("href", href)),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed complete response: {resp!r} ({e})"
            ) from e

    def stage_complete(
        self,
        href: str,
        holder_id: str,
        session_id: str,
        *,
        date: Optional[str] = None,
    ) -> StageCompleteResult:
        """Stage *href* as completed for *session_id* (Phase-1).

        Replaces :meth:`complete` on the spider's success path: writes
        into the DO's ``staged_complete{}`` map instead of jumping
        straight to ``completed_committed[]``.  The session-end CLI
        then either calls :meth:`commit_completed_movies` (success) or
        :meth:`rollback_staged_movies` (failure) to resolve the stage.

        ``session_id`` MUST be the spider's ``ReportSessions.Id``.  An
        empty string is rejected by the Worker (would make the staged
        entry un-rollbackable).

        Same idempotency / stale-holder semantics as :meth:`complete`;
        see :class:`StageCompleteResult` for the three rejection paths.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        if not session_id:
            raise MovieClaimUnavailable("session_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "session_id": str(session_id),
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/stage_complete_movie", body)
        try:
            return StageCompleteResult(
                staged=bool(resp["staged"]),
                href=str(resp.get("href", href)),
                session_id=str(resp.get("session_id", session_id) or ""),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed stage_complete response: {resp!r} ({e})"
            ) from e

    def commit_completed_movies(
        self,
        session_id: str,
        *,
        date: Optional[str] = None,
    ) -> CommitCompletedMoviesResult:
        """Promote all stages for *session_id* to committed (Phase-1).

        Called by ``apps.cli.commit_session`` immediately after the
        ReportSessions row flips to ``committed``.  Idempotent: a re-run
        returns ``promoted=0``.  Failures must NOT block the DB commit
        (the staged entries will still match an orphan-sweep cutoff so
        the next StaleSessionCleanup cron tidies them up).
        """
        if not session_id:
            raise MovieClaimUnavailable("session_id must be a non-empty string")
        body = {
            "session_id": str(session_id),
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/commit_completed_movies", body)
        try:
            return CommitCompletedMoviesResult(
                promoted=int(resp["promoted"]),
                session_id=str(resp.get("session_id", session_id) or ""),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed commit_completed_movies response: {resp!r} ({e})"
            ) from e

    def rollback_staged_movies(
        self,
        session_id: str,
        *,
        date: Optional[str] = None,
    ) -> RollbackStagedMoviesResult:
        """Drop every stage for *session_id* (Phase-1).

        Called by ``apps.cli.rollback`` after (or alongside) the DB-side
        rollback completes.  Idempotent: a re-run returns ``removed=0``.
        Other sessions' stages are NEVER touched — the per-session scope
        is the whole point of the rollback-safety split.
        """
        if not session_id:
            raise MovieClaimUnavailable("session_id must be a non-empty string")
        body = {
            "session_id": str(session_id),
            "date": date or current_shard_date(),
        }
        resp = self._do_request("POST", "/rollback_staged_movies", body)
        try:
            return RollbackStagedMoviesResult(
                removed=int(resp["removed"]),
                session_id=str(resp.get("session_id", session_id) or ""),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed rollback_staged_movies response: {resp!r} ({e})"
            ) from e

    def sweep_orphan_stages(
        self,
        *,
        older_than_ms: Optional[int] = None,
        date: Optional[str] = None,
    ) -> SweepOrphanStagesResult:
        """Prune stages older than *older_than_ms* (Phase-1, cron-only).

        Defence-in-depth for the case where a runner crashed between
        :meth:`stage_complete` and the session-end CLI's commit /
        rollback.  ``older_than_ms`` defaults to the Worker's
        ``DEFAULT_SWEEP_ORPHAN_MS`` (48h) and is floored at 1h
        server-side so a buggy operator can't accidentally wipe live
        stages.

        Designed to be called from a cron CLI (StaleSessionCleanup
        workflow) — passing the per-day shard ``date`` lets the cron
        walk multiple historical shards in one job.
        """
        params: list[str] = []
        target_date = date or current_shard_date()
        params.append(f"date={target_date}")
        if older_than_ms is not None:
            params.append(f"older_than_ms={int(older_than_ms)}")
        path = "/sweep_orphan_stages?" + "&".join(params)
        resp = self._do_request("GET", path)
        try:
            return SweepOrphanStagesResult(
                removed=int(resp["removed"]),
                cutoff_ms=int(resp.get("cutoff_ms", 0) or 0),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed sweep_orphan_stages response: {resp!r} ({e})"
            ) from e

    def report_failure(
        self,
        href: str,
        holder_id: str,
        *,
        error_kind: str = "",
        cooldown_ms: Optional[int] = None,
        date: Optional[str] = None,
    ) -> ReportFailureResult:
        """Record a failure on *href* (P2-A).

        Bumps the per-href ``fail_count`` and computes the next
        ``cooldown_until`` according to the server's cooldown ladder
        (``MOVIE_CLAIM_COOLDOWN_LADDER_MS``).  Pass ``cooldown_ms`` to
        override the ladder for one specific failure (e.g. a
        well-understood transient like ``proxy_timeout`` deserving a
        shorter cooldown than a generic ``http_500``).

        Side effect: if *holder_id* still owns the active claim on
        ``href``, the DO releases it as part of the failure report —
        symmetric with the success-path :meth:`complete`, so peers
        observe the slot as free without an extra :meth:`release` call.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        if not holder_id:
            raise MovieClaimUnavailable("holder_id must be a non-empty string")
        body = {
            "href": href,
            "holder_id": holder_id,
            "error_kind": error_kind,
            "date": date or current_shard_date(),
        }
        if cooldown_ms is not None:
            body["cooldown_ms"] = int(cooldown_ms)
        resp = self._do_request("POST", "/report_failure", body)
        try:
            return ReportFailureResult(
                fail_count=int(resp["fail_count"]),
                cooldown_until=int(resp.get("cooldown_until", 0) or 0),
                dead_lettered=bool(resp.get("dead_lettered", False)),
                server_time_ms=_extract_server_time_ms(resp),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed report_failure response: {resp!r} ({e})"
            ) from e

    def get_status(
        self,
        href: str,
        *,
        date: Optional[str] = None,
    ) -> StatusResult:
        """Look up the current state of *href*.  Debug / ops only.

        The spider's hot path uses :meth:`claim` (which already returns
        ``already_completed`` + ``current_holder_id`` in one round-trip);
        :meth:`get_status` exists for the rare case where ops want to
        inspect a shard's state without trying to claim anything.
        """
        if not href:
            raise MovieClaimUnavailable("href must be a non-empty string")
        target_date = date or current_shard_date()
        path = f"/movie_status?href={requests.utils.quote(href, safe='')}&date={target_date}"
        resp = self._do_request("GET", path)
        try:
            holder = resp.get("current_holder_id")
            return StatusResult(
                current_holder_id=str(holder) if holder else None,
                expires_at=int(resp.get("expires_at", 0) or 0),
                already_completed=bool(resp.get("already_completed", False)),
                server_time_ms=_extract_server_time_ms(resp),
                cooldown_until=int(resp.get("cooldown_until", 0) or 0),
                last_error_kind=str(resp.get("last_error_kind", "") or ""),
                fail_count=int(resp.get("fail_count", 0) or 0),
                staged_session_id=str(resp.get("staged_session_id", "") or ""),
                staged_at=int(resp.get("staged_at", 0) or 0),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise MovieClaimUnavailable(
                f"malformed status response: {resp!r} ({e})"
            ) from e

    # ``health_check``, ``close``, and ``_do_request`` are inherited
    # from :class:`BaseDOClient`. A non-2xx status surfaces verbatim in
    # the ``MovieClaimUnavailable`` message so e.g. "503 MOVIE_CLAIM_DO
    # binding missing" (v3 migration not applied) is visible in logs.


_ENABLED_UNSET = object()


def create_movie_claim_client_with_mode_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",
    enabled_env: str = "MOVIE_CLAIM_ENABLED",
    enabled_mode_override: object = None,
) -> Tuple[Optional[MovieClaimClient], str]:
    """Build a client + resolve the activation mode.

    ``PROXY_COORDINATOR_URL`` and ``PROXY_COORDINATOR_TOKEN`` are read
    **only** from :func:`packages.python.javdb_platform.config_helper.cfg`
    (i.e. ``config.py``).  ``os.environ`` is ignored for those two so CI
    jobs that render credentials exclusively into ``config.py`` behave the
    same as CLIs.  Missing or blank values disable movie-claim for this call.

    ``MOVIE_CLAIM_ENABLED`` uses the process environment when the variable
    is present there; otherwise it falls back to ``cfg`` (same attribute
    name).  ``None`` / unset maps to ``auto``; explicit empty string in the
    environment still means force-off.

    When *enabled_mode_override* is provided (not ``None``), it is used
    directly as the raw enabled value, bypassing both ``os.environ`` and
    ``cfg`` lookups for the enabled flag.  This avoids thread-unsafe
    ``os.environ`` manipulation in callers.

    Returns a ``(client_or_none, mode)`` tuple.  The mode is one of
    :data:`MOVIE_CLAIM_MODE_OFF` / :data:`MOVIE_CLAIM_MODE_AUTO` /
    :data:`MOVIE_CLAIM_MODE_FORCE_ON`.  A healthy auto deploy returns
    ``(client, "auto")``; missing URL/token, disabled mode, or ``/health``
    failure collapses to ``(None, off)``.
    """
    if enabled_mode_override is not None:
        raw_value = None if enabled_mode_override is _ENABLED_UNSET else str(enabled_mode_override)
    elif enabled_env in os.environ:
        raw_value = os.environ.get(enabled_env)
    else:
        configured = config_helper.cfg(enabled_env, None)
        raw_value = None if configured is None else str(configured)
    # ``None`` means "var not set at all" → apply the new ``auto`` default.
    # Empty string means "set to nothing" → keep the old "force-off" intuition
    # so an operator who wants to silence the feature can still use
    # ``MOVIE_CLAIM_ENABLED=``.
    mode = MOVIE_CLAIM_MODE_AUTO if raw_value is None else parse_movie_claim_mode(raw_value)

    if mode == MOVIE_CLAIM_MODE_OFF:
        logger.info(
            "Movie-claim client disabled (%s=%r, mode=off) — "
            "using per-process dedup only",
            enabled_env, raw_value if raw_value is not None else "",
        )
        return None, MOVIE_CLAIM_MODE_OFF

    url = (config_helper.cfg(url_env, None) or "").strip()
    token = (config_helper.cfg(token_env, None) or "").strip()
    if not url or not token:
        logger.info(
            "Movie-claim client not configured (%s/%s missing or empty in "
            "config.py, mode=%s) — using per-process dedup only",
            url_env, token_env, mode,
        )
        return None, MOVIE_CLAIM_MODE_OFF

    client = MovieClaimClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Movie-claim Worker URL %s is configured but /health did not respond "
            "(mode=%s) — falling back to per-process dedup for this run",
            url, mode,
        )
        client.close()
        return None, MOVIE_CLAIM_MODE_OFF
    logger.info(
        "Movie-claim client initialised: base_url=%s, mode=%s",
        url, mode,
    )
    return client, mode


def create_movie_claim_client_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",
    enabled_env: str = "MOVIE_CLAIM_ENABLED",
) -> Optional[MovieClaimClient]:
    """Backward-compatible thin wrapper over the with-mode factory.

    URL and token are taken from ``config.py`` via
    :func:`packages.python.javdb_platform.config_helper.cfg` only; see
    :func:`create_movie_claim_client_with_mode_from_env`.
    """
    client, _mode = create_movie_claim_client_with_mode_from_env(
        url_env=url_env, token_env=token_env, enabled_env=enabled_env,
    )
    return client
