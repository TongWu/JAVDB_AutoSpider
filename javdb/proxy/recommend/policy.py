"""W6.B (W5.5) — TTL-cached health provider backed by /recommend_proxy.

:class:`ProxyPool.set_health_provider` accepts any
``Callable[[str], Optional[float]]``. This module wires the
:class:`RecommendProxyClient` HTTP surface into that callable with:

- A background ``threading.Timer`` that refreshes the score cache every
  ``refresh_interval_sec`` (default 60 s). The timer is a daemon thread
  so a crashed spider tears the refresh loop down with it.
- A TTL fallback: scores older than ``stale_after_sec`` (default 5 min)
  are treated as ``None`` so a stuck Worker doesn't keep stale rankings
  alive indefinitely.
- ``shutdown()`` cancels the timer cleanly; idempotent so atexit
  handlers can call it without coordinating.

The pool's selection math expects ``Optional[float]`` returns (``None``
falls back to the neutral 0.5 weight), so a freshly-started runner
that hasn't completed its first refresh still gets uniform random
selection until the first poll lands.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from javdb.infra.logging import get_logger
from javdb.proxy.recommend.client import (
    RecommendProxyClient,
    RecommendProxyUnavailable,
)

logger = get_logger(__name__)


DEFAULT_REFRESH_INTERVAL_SEC = 60.0
DEFAULT_STALE_AFTER_SEC = 300.0


class RecommendProxyPolicy:
    """Background-refreshing health provider sourced from /recommend_proxy.

    Usage:

    .. code-block:: python

        client = create_recommend_proxy_client_from_env()
        if client is not None:
            policy = RecommendProxyPolicy(client, proxy_ids=["P1", "P2"])
            policy.start()
            pool.set_health_provider(policy.score_for)
            atexit.register(policy.shutdown)

    The instance owns the timer. Construct one per process / per pool.
    """

    def __init__(
        self,
        client: RecommendProxyClient,
        proxy_ids: List[str],
        *,
        refresh_interval_sec: float = DEFAULT_REFRESH_INTERVAL_SEC,
        stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
        include_unhealthy: bool = True,
    ):
        self._client = client
        # Defensive copy + clean — empty strings ignored.
        self._proxy_ids: List[str] = [
            pid.strip() for pid in proxy_ids if pid and pid.strip()
        ]
        # Refresh interval floored at 5 s in production (prevents a typo
        # from beating the Worker into the ground). ``stale_after_sec``
        # has no minimum — operators who want sub-second staleness for
        # tests or debugging can set it freely.
        self._refresh_interval_sec = max(5.0, float(refresh_interval_sec))
        self._stale_after_sec = max(0.0, float(stale_after_sec))
        self._include_unhealthy = bool(include_unhealthy)

        # Score cache + the wall-clock timestamp at which it was filled.
        # Read on every pool selection (hot path), so kept lock-free —
        # writes replace the entire dict atomically (Python dict refs).
        self._scores: Dict[str, float] = {}
        self._scores_at: float = 0.0

        # Timer / shutdown coordination. ``_timer`` is replaced on each
        # tick; ``_shutdown_event`` signals the refresher to bail out.
        self._timer_lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._shutdown_event = threading.Event()
        self._started = False

    def start(self) -> None:
        """Kick off the first refresh + arm the recurring timer.

        Idempotent: calling twice is a no-op. The first refresh runs
        synchronously so the initial pool selection has data; subsequent
        refreshes are scheduled on the timer thread.
        """
        with self._timer_lock:
            if self._started:
                return
            self._started = True
        # Synchronous prime so the first ProxyPool.get_next_proxy after
        # bootstrap has data; never raises (the refresh wraps everything).
        self._refresh_now()
        self._schedule_next()

    def shutdown(self) -> None:
        """Cancel the timer + flag the policy as torn down. Idempotent."""
        self._shutdown_event.set()
        with self._timer_lock:
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()

    def score_for(self, name: str) -> Optional[float]:
        """Hot-path callable for ``ProxyPool.set_health_provider``.

        Returns the cached score for *name* when it's fresh enough; None
        otherwise so the pool falls back to its neutral 0.5 baseline.
        Never raises and never blocks on the network — the timer thread
        is the only thing that ever talks to the Worker.
        """
        if not name:
            return None
        # Atomic snapshot; ``self._scores`` is replaced wholesale by
        # ``_refresh_now``, so reading scores + age out of band of the
        # timer thread is safe.
        scores = self._scores
        scores_at = self._scores_at
        if not scores:
            return None
        if (time.time() - scores_at) > self._stale_after_sec:
            return None
        return scores.get(name)

    # -- internals ---------------------------------------------------------

    def _refresh_now(self) -> None:
        """Pull a fresh recommendation set and replace the cache.

        Wrapped in a broad try/except so a Worker outage never escalates
        into a crashed timer. The previous cache stays in place until
        either a successful refresh replaces it or it ages past
        ``_stale_after_sec``.
        """
        try:
            result = self._client.recommend(
                self._proxy_ids,
                include_unhealthy=self._include_unhealthy,
            )
        except RecommendProxyUnavailable as e:
            logger.debug(
                "RecommendProxy refresh failed (will retry next tick): %s", e,
            )
            return
        except Exception:  # noqa: BLE001 — refresh must never block selection
            logger.warning(
                "RecommendProxy refresh crashed unexpectedly", exc_info=True,
            )
            return

        new_scores: Dict[str, float] = {
            rec.proxy_id: rec.score for rec in result.recommendations
        }
        # Atomic swap — ``score_for`` reads ``self._scores`` directly so
        # the assignment publishes the new map to subsequent reads.
        self._scores = new_scores
        self._scores_at = time.time()
        logger.debug(
            "RecommendProxy refreshed (%d scores)", len(new_scores),
        )

    def _schedule_next(self) -> None:
        """Arm the next timer tick. No-op if shutdown has been called."""
        if self._shutdown_event.is_set():
            return
        timer = threading.Timer(self._refresh_interval_sec, self._on_tick)
        timer.daemon = True
        with self._timer_lock:
            self._timer = timer
        timer.start()

    def _on_tick(self) -> None:
        """Timer callback: refresh + re-arm."""
        if self._shutdown_event.is_set():
            return
        self._refresh_now()
        self._schedule_next()
