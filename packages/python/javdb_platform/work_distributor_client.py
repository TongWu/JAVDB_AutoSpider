"""W6.C (W5.2) — Python client for the Worker's WorkDistributor DO.

The Worker DO exposes a singleton queue with visibility leases. This
client wraps the five HTTP endpoints
(``/work/{enqueue, pull, complete, release, stats}``) in the same
fail-open style as the other DO clients in this package.

Opt-in via the ``WORK_DISTRIBUTOR_ENABLED`` env var. When enabled, the
:mod:`javdb_spider.detail.runner` consumer loop pulls work from this
queue instead of iterating its locally-discovered href list; MovieClaim
stays mounted as a defence-in-depth layer (see W5.2 plan).

Wire format types are defined in
``JAVDB_AutoSpider_Proxycoordinator/src/types.ts``: the Python types
here are direct mirrors — names match, semantics match.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

from packages.python.javdb_platform import config_helper
from packages.python.javdb_platform.do_client_base import (
    BaseDOClient,
    DOClientUnavailable,
)
from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)


_DEFAULT_USER_AGENT = "javdb-spider-work-distributor-client/1.0"
_DEFAULT_VISIBILITY_TIMEOUT_MS = 5 * 60 * 1000  # 5 min, matches Worker default


class WorkDistributorUnavailable(DOClientUnavailable):
    """Raised when the work-distribution DO cannot be reached.

    Fail-open: every callsite treats it as "queue offline, fall back to
    the existing local dispatch path". Mirrors the other ``*Unavailable``
    types in this package.
    """


@dataclass(frozen=True)
class WorkItem:
    """One queued entry returned from /work/pull.

    Direct mirror of the Worker's ``WorkItem``. ``payload`` is opaque
    (typically a JSON object the caller embeds at enqueue time);
    ``attempt_count`` increments on every successful pull, so a
    consumer can implement poison-pill detection.
    """

    key: str
    payload: Any
    enqueued_at_ms: int
    attempt_count: int


@dataclass(frozen=True)
class EnqueueResult:
    """Reply from ``POST /work/enqueue``."""

    enqueued: List[str] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)
    queue_size: int = 0
    server_time_ms: int = 0


@dataclass(frozen=True)
class PullResult:
    """Reply from ``POST /work/pull``.

    ``items`` may be empty when the queue is drained or every visible
    entry is currently leased to another holder.
    """

    items: List[WorkItem] = field(default_factory=list)
    queue_size: int = 0
    server_time_ms: int = 0


@dataclass(frozen=True)
class CompleteResult:
    """Reply from ``POST /work/complete``."""

    completed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    server_time_ms: int = 0


@dataclass(frozen=True)
class ReleaseResult:
    """Reply from ``POST /work/release``."""

    released: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    server_time_ms: int = 0


@dataclass(frozen=True)
class StatsResult:
    """Reply from ``GET /work/stats``."""

    queue_size: int = 0
    visible: int = 0
    leased: int = 0
    oldest_enqueued_at_ms: Optional[int] = None
    server_time_ms: int = 0


def _parse_work_item(payload: Any) -> Optional[WorkItem]:
    """Defensive decode for one pull item. Returns None on malformed input."""
    if not isinstance(payload, dict):
        return None
    try:
        key = str(payload.get("key", ""))
        if not key:
            return None
        return WorkItem(
            key=key,
            payload=payload.get("payload"),
            enqueued_at_ms=int(payload.get("enqueued_at_ms", 0) or 0),
            attempt_count=int(payload.get("attempt_count", 0) or 0),
        )
    except (TypeError, ValueError):
        return None


def _coerce_str_list(payload: Any) -> List[str]:
    """Filter a Worker-returned ``string[]`` payload to actual strings."""
    if not isinstance(payload, list):
        return []
    return [str(x) for x in payload if isinstance(x, str)]


class WorkDistributorClient(BaseDOClient):
    """HTTP client for the WorkDistributor singleton DO (W5.2).

    Construct once per process; methods are blocking and short-lived
    (5 s default per-request timeout — set ``timeout`` to override).

    Args:
        base_url: Worker URL, e.g. ``https://proxy-coordinator.acme.workers.dev``.
        token: Bearer token (must match ``PROXY_COORDINATOR_TOKEN``).
        timeout: Per-request HTTP timeout in seconds.
        user_agent: Optional override for the ``User-Agent`` header.
    """

    _unavailable_exc = WorkDistributorUnavailable

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 5.0,
        user_agent: str = _DEFAULT_USER_AGENT,
    ):
        super().__init__(base_url, token, timeout=timeout, user_agent=user_agent)

    # -- public API ---------------------------------------------------------

    def enqueue(
        self,
        items: Iterable[Any],
        *,
        replace_existing: bool = False,
    ) -> EnqueueResult:
        """Idempotent enqueue. ``items`` is either a list of strings (keys)
        or a list of ``{"key": ..., "payload": ...}`` dicts. Mixed input is
        normalised; blank keys are dropped client-side.

        ``replace_existing=True`` overwrites payloads while preserving the
        existing ``enqueued_at_ms`` + ``attempt_count``.

        Worker caps each call at 100 items.
        """
        normalised: List[dict] = []
        for entry in items:
            if isinstance(entry, str):
                key = entry.strip()
                if key:
                    normalised.append({"key": key})
            elif isinstance(entry, dict):
                key = str(entry.get("key", "")).strip()
                if key:
                    normalised.append({
                        "key": key,
                        "payload": entry.get("payload"),
                    })
        if not normalised:
            return EnqueueResult()
        # Client-side chunking would be nicer, but the Worker rejects
        # >100 items with HTTP 400; let the caller see the error so they
        # batch correctly rather than hiding it.
        body = {"items": normalised, "replace_existing": bool(replace_existing)}
        resp = self._do_request("POST", "/work/enqueue", body)
        return EnqueueResult(
            enqueued=_coerce_str_list(resp.get("enqueued")),
            duplicates=_coerce_str_list(resp.get("duplicates")),
            queue_size=int(resp.get("queue_size", 0) or 0),
            server_time_ms=int(resp.get("server_time", 0) or 0),
        )

    def pull(
        self,
        holder_id: str,
        *,
        max_items: int = 10,
        visibility_timeout_ms: int = _DEFAULT_VISIBILITY_TIMEOUT_MS,
    ) -> PullResult:
        """Pull up to ``max_items`` items not currently leased. Each
        pulled item gets a lease for ``visibility_timeout_ms`` (Worker
        clamps to ``[1s, 1h]``).
        """
        if not holder_id:
            raise WorkDistributorUnavailable("holder_id must be non-empty")
        body = {
            "holder_id": str(holder_id),
            "max_items": int(max_items),
            "visibility_timeout_ms": int(visibility_timeout_ms),
        }
        resp = self._do_request("POST", "/work/pull", body)
        raw_items = resp.get("items") or []
        items: List[WorkItem] = []
        if isinstance(raw_items, list):
            for entry in raw_items:
                item = _parse_work_item(entry)
                if item is not None:
                    items.append(item)
        return PullResult(
            items=items,
            queue_size=int(resp.get("queue_size", 0) or 0),
            server_time_ms=int(resp.get("server_time", 0) or 0),
        )

    def complete(self, holder_id: str, keys: Iterable[str]) -> CompleteResult:
        """Remove items the caller has finished. Non-owner completes are
        silently skipped server-side (returned in ``skipped``)."""
        cleaned = [str(k).strip() for k in keys if isinstance(k, str) and k.strip()]
        if not holder_id or not cleaned:
            return CompleteResult()
        resp = self._do_request(
            "POST", "/work/complete",
            {"holder_id": str(holder_id), "keys": cleaned},
        )
        return CompleteResult(
            completed=_coerce_str_list(resp.get("completed")),
            skipped=_coerce_str_list(resp.get("skipped")),
            server_time_ms=int(resp.get("server_time", 0) or 0),
        )

    def release(self, holder_id: str, keys: Iterable[str]) -> ReleaseResult:
        """Return leased items to the visible pool. Same non-owner skip
        rule as :meth:`complete`."""
        cleaned = [str(k).strip() for k in keys if isinstance(k, str) and k.strip()]
        if not holder_id or not cleaned:
            return ReleaseResult()
        resp = self._do_request(
            "POST", "/work/release",
            {"holder_id": str(holder_id), "keys": cleaned},
        )
        return ReleaseResult(
            released=_coerce_str_list(resp.get("released")),
            skipped=_coerce_str_list(resp.get("skipped")),
            server_time_ms=int(resp.get("server_time", 0) or 0),
        )

    def stats(self) -> StatsResult:
        """Read-only queue depth snapshot for ops dashboards."""
        resp = self._do_request("GET", "/work/stats")
        oldest = resp.get("oldest_enqueued_at_ms")
        return StatsResult(
            queue_size=int(resp.get("queue_size", 0) or 0),
            visible=int(resp.get("visible", 0) or 0),
            leased=int(resp.get("leased", 0) or 0),
            oldest_enqueued_at_ms=int(oldest) if isinstance(oldest, (int, float)) else None,
            server_time_ms=int(resp.get("server_time", 0) or 0),
        )


def create_work_distributor_client_from_env(
    *,
    url_env: str = "PROXY_COORDINATOR_URL",
    token_env: str = "PROXY_COORDINATOR_TOKEN",  # noqa: S107
    enabled_env: str = "WORK_DISTRIBUTOR_ENABLED",
) -> Optional[WorkDistributorClient]:
    """Build a client from env vars + cfg, returning ``None`` when disabled.

    Three independent disable paths, identical to the other DO factories:

    - ``WORK_DISTRIBUTOR_ENABLED`` not in ``{"1", "true", "yes"}``
      (default OFF — every existing deployment keeps using the local
      ``for candidate in prepared_entries`` dispatch loop);
    - either of ``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN``
      is empty;
    - the URL is configured but ``/health`` does not respond.
    """
    raw_enabled = (os.environ.get(enabled_env) or "").strip().lower()
    if raw_enabled not in {"1", "true", "yes"}:
        logger.info(
            "WorkDistributor client disabled (%s=%r) — using local dispatch",
            enabled_env, os.environ.get(enabled_env, ""),
        )
        return None

    url = (config_helper.cfg(url_env, "") or "").strip()
    token = (config_helper.cfg(token_env, "") or "").strip()
    if not url or not token:
        logger.info(
            "WorkDistributor client not configured (%s/%s missing)",
            url_env, token_env,
        )
        return None

    client = WorkDistributorClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "WorkDistributor URL %s is configured but /health did not respond — "
            "falling back to local dispatch for this run",
            url,
        )
        client.close()
        return None
    logger.info("WorkDistributor client initialised: base_url=%s", url)
    return client
