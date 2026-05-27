from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import threading
import uuid
from typing import Any, Optional

from javdb.spider.runtime.config import LOGIN_ATTEMPTS_PER_PROXY_LIMIT, PROXY_POOL
from javdb.proxy.coordinator.movie_claim_client import (
    MOVIE_CLAIM_MODE_AUTO,
    MOVIE_CLAIM_MODE_FORCE_ON,
    MOVIE_CLAIM_MODE_OFF,
)


def _new_holder_id() -> str:
    return f"runner-{uuid.uuid4().hex[:16]}"


@dataclass
class DetailRunState:
    parsed_links: set[str] = field(default_factory=set)


@dataclass
class ProxyRunState:
    proxy_ban_html_files: list[str] = field(default_factory=list)
    always_bypass_time: Optional[int] = None
    proxies_requiring_cf_bypass: dict[str, float] = field(default_factory=dict)
    cf_bypass_lock: threading.Lock = field(default_factory=threading.Lock)
    signal_banned_proxies: set[str] = field(default_factory=set)
    signal_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class LoginRunState:
    login_attempted: bool = False
    refreshed_session_cookie: Optional[str] = None
    logged_in_proxy_name: Optional[str] = None
    current_login_state_version: Optional[int] = None
    login_attempts_per_proxy: dict[str, int] = field(default_factory=dict)
    login_failures_per_proxy: dict[str, int] = field(default_factory=dict)
    login_total_attempts: int = 0
    login_total_budget: int = field(
        default_factory=lambda: (
            len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT
            if PROXY_POOL else 0
        )
    )
    login_budget_deducted_proxies: set[str] = field(default_factory=set)
    login_budget_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class RunnerRegistryState:
    holder_id: str = field(default_factory=_new_holder_id)
    session: Any = None
    heartbeat_thread: Optional[threading.Thread] = None
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    unregistered: bool = False
    heartbeat_interval_multi_runner_sec: float = 60.0
    heartbeat_interval_single_runner_sec: float = 15.0
    runner_heartbeat_interval_sec: float = 60.0
    last_applied_config_version: int = -1
    signal_banned_proxies: set[str] = field(default_factory=set)
    signal_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class MovieClaimRuntimeState:
    client_pending: Any = None
    client_public: Any = None
    mode: str = MOVIE_CLAIM_MODE_OFF
    intended_mode: str = MOVIE_CLAIM_MODE_OFF
    last_recommended: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    swept_at_exit: bool = False
    sweep_at_exit_older_than_ms: int = 6 * 60 * 60 * 1000


@dataclass
class SleepRuntimeState:
    penalty_tracker: Any = None
    triple_window_throttle: Any = None
    dual_window_throttle: Any = None
    movie_sleep_mgr: Any = None


@dataclass
class RuntimeServices:
    proxy_pool: Any = None
    request_handler: Any = None
    proxy_coordinator: Any = None
    login_state_client: Any = None
    movie_claim_client: Any = None
    runner_registry_client: Any = None
    recommend_proxy_policy: Any = None
    work_distributor_client: Any = None


@dataclass
class SpiderRuntime:
    detail: DetailRunState = field(default_factory=DetailRunState)
    proxy: ProxyRunState = field(default_factory=ProxyRunState)
    login: LoginRunState = field(default_factory=LoginRunState)
    runner_registry: RunnerRegistryState = field(default_factory=RunnerRegistryState)
    movie_claim: MovieClaimRuntimeState = field(default_factory=MovieClaimRuntimeState)
    sleep: SleepRuntimeState = field(default_factory=SleepRuntimeState)
    services: RuntimeServices = field(default_factory=RuntimeServices)
    closed: bool = False
    _close_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def proxy_pool(self):
        return self.services.proxy_pool

    @proxy_pool.setter
    def proxy_pool(self, value):
        self.services.proxy_pool = value

    @property
    def request_handler(self):
        return self.services.request_handler

    @request_handler.setter
    def request_handler(self, value):
        self.services.request_handler = value

    @staticmethod
    def _close_service(service) -> None:
        if service is None:
            return
        close = getattr(service, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()

    def close(self) -> None:
        with self._close_lock:
            if self.closed:
                return
            self.closed = True

        self.runner_registry.heartbeat_stop.set()
        thread = self.runner_registry.heartbeat_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=5.0)
        if (
            thread is not None
            and thread is not threading.current_thread()
            and not thread.is_alive()
        ):
            self.runner_registry.heartbeat_thread = None

        client = self.services.runner_registry_client
        if client is not None and not self.runner_registry.unregistered:
            with contextlib.suppress(Exception):
                client.unregister(
                    self.runner_registry.holder_id,
                    session=self.runner_registry.session,
                )
                self.runner_registry.unregistered = True
        self._close_service(client)
        self.services.runner_registry_client = None

        policy = self.services.recommend_proxy_policy
        shutdown = getattr(policy, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception):
                shutdown()
        self.services.recommend_proxy_policy = None

        for attr in (
            "proxy_coordinator",
            "login_state_client",
            "work_distributor_client",
        ):
            self._close_service(getattr(self.services, attr))
            setattr(self.services, attr, None)

        for client in (
            self.movie_claim.client_public,
            self.movie_claim.client_pending,
        ):
            self._close_service(client)
        self.movie_claim.client_public = None
        self.movie_claim.client_pending = None
        self.services.movie_claim_client = None

        import javdb.spider.runtime.state as legacy_state

        with contextlib.suppress(Exception):
            legacy_state.set_remote_ban_hook(None)
            legacy_state.set_remote_unban_hook(None)
        legacy_state._sync_legacy_globals_from_runtime(self)

    def set_active_runner_session(
        self,
        *,
        session_id: str,
        status: str,
        write_mode: Optional[str] = None,
        report_type: Optional[str] = None,
        failure_reason: Optional[str] = None,
        flush_immediately: bool = False,
    ) -> None:
        import javdb.spider.runtime.state as legacy_state

        self.runner_registry.session = legacy_state.SessionPayload(
            session_id=str(session_id),
            status=str(status),
            write_mode=write_mode,
            report_type=report_type,
            failure_reason=failure_reason,
        )
        legacy_state._sync_legacy_globals_from_runtime(self)
        if not flush_immediately:
            return
        client = self.services.runner_registry_client
        if client is None:
            return
        try:
            client.heartbeat(self.runner_registry.holder_id, session=self.runner_registry.session)
        except Exception:
            legacy_state.logger.debug(
                "Coordinator session-status flush failed; will retry on next heartbeat",
                exc_info=True,
            )

    def _next_heartbeat_interval(self) -> float:
        with self.movie_claim.lock:
            mode = self.movie_claim.mode
            recommended = self.movie_claim.last_recommended
        if mode != MOVIE_CLAIM_MODE_AUTO:
            return self.runner_registry.runner_heartbeat_interval_sec
        return (
            self.runner_registry.runner_heartbeat_interval_sec
            if recommended
            else self.runner_registry.heartbeat_interval_single_runner_sec
        )

    def _apply_movie_claim_recommendation(self, recommended: bool) -> None:
        import javdb.spider.runtime.state as legacy_state

        with self.movie_claim.lock:
            self.movie_claim.last_recommended = bool(recommended)
            mode = self.movie_claim.mode

            if mode == MOVIE_CLAIM_MODE_OFF:
                legacy_state._sync_legacy_globals_from_runtime(self)
                return

            if mode == MOVIE_CLAIM_MODE_AUTO:
                if recommended:
                    if (
                        self.movie_claim.client_public is None
                        and self.movie_claim.client_pending is not None
                    ):
                        self.movie_claim.client_public = self.movie_claim.client_pending
                        legacy_state.logger.info(
                            "movie-claim auto: mounted (active_runners >= threshold)",
                        )
                else:
                    if self.movie_claim.client_public is not None:
                        self.movie_claim.client_public = None
                        legacy_state.logger.info(
                            "movie-claim auto: unmounted (active_runners < threshold)",
                        )
                legacy_state._sync_legacy_globals_from_runtime(self)
                return

            if (
                self.movie_claim.client_public is None
                and self.movie_claim.client_pending is not None
            ):
                self.movie_claim.client_public = self.movie_claim.client_pending
                legacy_state.logger.info(
                    "movie-claim force_on: mounted (signal recommended=%s ignored)",
                    recommended,
                )
            legacy_state._sync_legacy_globals_from_runtime(self)

    def setup_movie_claim_client(self) -> Optional[Any]:
        import javdb.spider.runtime.state as legacy_state
        from javdb.infra.config import cfg
        from javdb.proxy.coordinator.movie_claim_client import _ENABLED_UNSET

        with self.movie_claim.lock:
            if self.movie_claim.client_public is not None:
                legacy_state._sync_legacy_globals_from_runtime(self)
                return self.movie_claim.client_public
            if self.movie_claim.client_pending is not None:
                if self.movie_claim.mode == MOVIE_CLAIM_MODE_FORCE_ON or (
                    self.movie_claim.mode == MOVIE_CLAIM_MODE_AUTO
                    and self.movie_claim.last_recommended
                ):
                    self.movie_claim.client_public = self.movie_claim.client_pending
                legacy_state._sync_legacy_globals_from_runtime(self)
                return self.movie_claim.client_pending

        url = (cfg("PROXY_COORDINATOR_URL", "") or "").strip()
        token = (cfg("PROXY_COORDINATOR_TOKEN", "") or "").strip()
        raw_enabled_cfg = cfg("MOVIE_CLAIM_ENABLED", None)
        intended_mode = (
            MOVIE_CLAIM_MODE_AUTO
            if raw_enabled_cfg is None
            else legacy_state.parse_movie_claim_mode(str(raw_enabled_cfg))
        )
        override = _ENABLED_UNSET if raw_enabled_cfg is None else raw_enabled_cfg
        client, mode = legacy_state.create_movie_claim_client_with_mode_from_env(
            enabled_mode_override=override,
        )

        with self.movie_claim.lock:
            if self.movie_claim.client_public is not None:
                if client is not None and client is not self.movie_claim.client_public:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        legacy_state.logger.debug(
                            "Failed to close redundant movie-claim client",
                            exc_info=True,
                        )
                legacy_state._sync_legacy_globals_from_runtime(self)
                return self.movie_claim.client_public
            if self.movie_claim.client_pending is not None:
                if client is not None and client is not self.movie_claim.client_pending:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        legacy_state.logger.debug(
                            "Failed to close redundant movie-claim client",
                            exc_info=True,
                        )
                if self.movie_claim.mode == MOVIE_CLAIM_MODE_FORCE_ON or (
                    self.movie_claim.mode == MOVIE_CLAIM_MODE_AUTO
                    and self.movie_claim.last_recommended
                ):
                    self.movie_claim.client_public = self.movie_claim.client_pending
                legacy_state._sync_legacy_globals_from_runtime(self)
                return self.movie_claim.client_pending

            self.movie_claim.mode = mode
            self.movie_claim.intended_mode = intended_mode

            if client is None or mode == MOVIE_CLAIM_MODE_OFF:
                self.movie_claim.client_pending = None
                self.movie_claim.client_public = None
                legacy_state._sync_legacy_globals_from_runtime(self)
                return None

            self.movie_claim.client_pending = client
            if mode == MOVIE_CLAIM_MODE_FORCE_ON:
                self.movie_claim.client_public = client
                legacy_state.logger.info(
                    "Movie-claim client mounted (force_on): base_url=%s, holder_id=%s",
                    url, self.runner_registry.holder_id,
                )
                legacy_state._sync_legacy_globals_from_runtime(self)
                return client

            self.movie_claim.client_public = client
            legacy_state.logger.info(
                "Movie-claim client optimistically mounted (auto, awaiting registry signal): "
                "base_url=%s, holder_id=%s",
                url, self.runner_registry.holder_id,
            )
            legacy_state._sync_legacy_globals_from_runtime(self)
            return client

    def enforce_movie_claim_for_d1(self) -> None:
        import os as _os
        import javdb.spider.runtime.state as legacy_state
        from javdb.infra.config import storage_backend

        if storage_backend() != "d1":
            return

        if self.movie_claim.intended_mode == MOVIE_CLAIM_MODE_OFF:
            legacy_state.logger.warning(
                "MOVIE_CLAIM_ENABLED resolves to OFF under STORAGE_BACKEND=d1 — "
                "running without cross-runtime detail-claim coordination. Parallel "
                "runtimes will duplicate detail fetches and may race UNIQUE(Href) "
                "INSERTs. Set MOVIE_CLAIM_ENABLED=auto to enable coordination."
            )
            return

        with self.movie_claim.lock:
            have_client = (
                self.movie_claim.client_public is not None
                or self.movie_claim.client_pending is not None
            )
        if have_client:
            return

        allow_uncoordinated = (
            _os.environ.get("JAVDB_ALLOW_UNCOORDINATED_D1", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        if allow_uncoordinated:
            legacy_state.logger.warning(
                "STORAGE_BACKEND=d1 wants movie-claim coordination "
                "(intended mode=%s) but the Worker is unreachable / unconfigured; "
                "JAVDB_ALLOW_UNCOORDINATED_D1 is set so proceeding anyway. This is "
                "only safe for a SINGLE d1-only runtime — concurrent runs will "
                "duplicate fetches and race PRIMARY KEY INSERTs.",
                self.movie_claim.intended_mode,
            )
            return

        raise RuntimeError(
            "STORAGE_BACKEND=d1 requires the MovieClaim coordinator but it is "
            "unreachable or unconfigured (intended MOVIE_CLAIM_ENABLED mode="
            f"{self.movie_claim.intended_mode!r}; PROXY_COORDINATOR_URL/TOKEN must be "
            "set and the Worker /health probe must succeed). Without it, parallel "
            "d1-only runtimes duplicate every detail fetch and race UNIQUE(Href) "
            "INSERTs into MovieHistory. Fix the Worker deployment, or set "
            "JAVDB_ALLOW_UNCOORDINATED_D1=1 to deliberately run a single "
            "uncoordinated d1-only runtime."
        )

    def _movie_claim_sweep_shard_dates(self) -> list[str]:
        import datetime as _dt

        ops_tz = _dt.timezone(_dt.timedelta(hours=8))
        base = _dt.datetime.now(ops_tz)
        return [
            (base - _dt.timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in (0, 1)
        ]

    def _sweep_movie_claim_stages_at_exit(self) -> None:
        import javdb.spider.runtime.state as legacy_state

        with self.movie_claim.lock:
            if self.movie_claim.swept_at_exit:
                return
            self.movie_claim.swept_at_exit = True
            client = self.movie_claim.client_public or self.movie_claim.client_pending
            legacy_state._sync_legacy_globals_from_runtime(self)
        if client is None:
            return
        for shard_date in self._movie_claim_sweep_shard_dates():
            try:
                result = client.sweep_orphan_stages(
                    older_than_ms=self.movie_claim.sweep_at_exit_older_than_ms,
                    date=shard_date,
                )
            except Exception as exc:  # noqa: BLE001
                legacy_state.logger.debug(
                    "Movie-claim orphan-stage sweep at exit failed for shard %s "
                    "(non-fatal): %s",
                    shard_date, exc,
                )
                continue
            if result.removed:
                legacy_state.logger.info(
                    "Movie-claim orphan-stage sweep at exit: removed %d stale "
                    "stage(s) from shard %s",
                    result.removed, shard_date,
                )

    def _apply_config_snapshot(self, snap) -> None:
        import javdb.spider.runtime.state as legacy_state
        from javdb.spider.runtime.sleep import ensure_sleep_runtime

        if snap.version == self.runner_registry.last_applied_config_version:
            return
        values = snap.values or {}

        def _to_int(key: str) -> Optional[int]:
            raw = values.get(key)
            if raw is None or raw == "":
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                legacy_state.logger.warning("Config %s=%r is not an integer; ignoring", key, raw)
                return None

        def _to_float(key: str) -> Optional[float]:
            raw = values.get(key)
            if raw is None or raw == "":
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                legacy_state.logger.warning("Config %s=%r is not a float; ignoring", key, raw)
                return None

        try:
            _throttle = ensure_sleep_runtime(self).triple_window_throttle
            _throttle.apply_config(
                short_max=_to_int("short_max"),
                long_max=_to_int("long_max"),
                extra_max=_to_int("extra_max"),
                short_window_sec=_to_float("short_window_sec"),
                long_window_sec=_to_float("long_window_sec"),
                extra_window_sec=_to_float("extra_window_sec"),
            )
        except Exception:
            legacy_state.logger.warning(
                "Failed to apply throttle config from snapshot v%d",
                snap.version,
                exc_info=True,
            )

        hb_interval = _to_float("heartbeat_interval_sec")
        if hb_interval is not None and hb_interval > 0:
            self.runner_registry.heartbeat_interval_multi_runner_sec = hb_interval
            self.runner_registry.runner_heartbeat_interval_sec = hb_interval

        self.runner_registry.last_applied_config_version = snap.version
        legacy_state._sync_legacy_globals_from_runtime(self)
        legacy_state.logger.info(
            "Applied W5.3 config snapshot version=%d (%d operator overrides)",
            snap.version,
            len(values),
        )

    def _apply_active_signals(self, signals: list) -> None:
        import javdb.spider.runtime.state as legacy_state

        if signals is None:
            signals = []

        desired_factor = 1.0
        desired_pause_until_ms = 0
        desired_bans: set[str] = set()
        for sig in signals:
            try:
                kind = getattr(sig, "kind", None)
                if kind == "throttle_global":
                    f = getattr(sig, "factor", None)
                    if f is not None:
                        desired_factor = max(desired_factor, float(f))
                elif kind == "pause_all":
                    exp = int(getattr(sig, "expires_at_ms", 0) or 0)
                    if exp > desired_pause_until_ms:
                        desired_pause_until_ms = exp
                elif kind == "ban_proxy":
                    pid = getattr(sig, "proxy_id", None)
                    if pid:
                        desired_bans.add(str(pid))
            except Exception:
                legacy_state.logger.warning(
                    "Skipping malformed signal during apply: %r",
                    sig,
                    exc_info=True,
                )

        try:
            from javdb.spider.runtime.sleep import ensure_sleep_runtime
            _mgr = ensure_sleep_runtime(self).movie_sleep_mgr
            _mgr.set_global_factor(desired_factor)
            _mgr.set_pause_until_ms(desired_pause_until_ms)
        except Exception:
            legacy_state.logger.warning(
                "Failed to apply throttle_global / pause_all signal",
                exc_info=True,
            )

        with self.runner_registry.signal_lock:
            new_bans = desired_bans - self.runner_registry.signal_banned_proxies
            removed_bans = self.runner_registry.signal_banned_proxies - desired_bans
            self.runner_registry.signal_banned_proxies = set(desired_bans)

        pool = self.services.proxy_pool
        if pool is None:
            import javdb.spider.runtime.state as state_mod
            pool = state_mod.global_proxy_pool
        if pool is not None:
            for proxy_id in new_bans:
                try:
                    pool.ban_proxy(proxy_id)
                    legacy_state.logger.warning(
                        "W5.4 ban_proxy signal applied: %s now banned",
                        proxy_id,
                    )
                except Exception:
                    legacy_state.logger.warning(
                        "Failed to apply ban_proxy signal for %s",
                        proxy_id,
                        exc_info=True,
                    )
            for proxy_id in removed_bans:
                try:
                    pool.unban_proxy(proxy_id)
                    legacy_state.logger.info(
                        "W5.4 ban_proxy signal expired: %s restored to rotation",
                        proxy_id,
                    )
                except Exception:
                    legacy_state.logger.warning(
                        "Failed to unban %s after signal expiry",
                        proxy_id,
                        exc_info=True,
                    )
        legacy_state._sync_legacy_globals_from_runtime(self)

    def _apply_sleep_runner_count(self, count: int) -> None:
        if count <= 0:
            return
        try:
            from javdb.spider.runtime.sleep import ensure_sleep_runtime
            ensure_sleep_runtime(self).movie_sleep_mgr.set_active_runners(count)
        except Exception:  # noqa: BLE001
            import javdb.spider.runtime.state as legacy_state
            legacy_state.logger.debug(
                "Failed to apply sleep runner count: %d",
                count,
                exc_info=True,
            )

    def _maybe_honour_pipeline_pause(self, *, pipeline_paused_until_ms: int, reason: Optional[str]) -> None:
        import javdb.spider.runtime.state as legacy_state

        if not pipeline_paused_until_ms or pipeline_paused_until_ms <= 0:
            return
        import time as _time
        now_ms = int(_time.time() * 1000)
        if pipeline_paused_until_ms <= now_ms:
            return
        remaining_min = (pipeline_paused_until_ms - now_ms) / 60_000
        legacy_state.logger.warning(
            "Pipeline paused by operator (Coordinator config). Exiting cleanly. "
            "paused_until_ms=%s remaining=%.1f min reason=%s",
            pipeline_paused_until_ms,
            remaining_min,
            reason or "",
        )
        try:
            with open(".publish-config.yml", "w", encoding="utf-8") as fh:
                fh.write(
                    "# Phase-1 ADR-008 — written by runner startup pause check.\n"
                    "pipeline_paused: true\n"
                    f"paused_until_ms: {pipeline_paused_until_ms}\n"
                    f"reason: {reason or ''}\n"
                )
        except Exception:
            legacy_state.logger.debug(
                "Failed to write .publish-config.yml pause marker",
                exc_info=True,
            )
        raise SystemExit(0)

    def _runner_heartbeat_loop(self, client, holder_id: str) -> None:
        import javdb.spider.runtime.state as legacy_state

        while not self.runner_registry.heartbeat_stop.wait(self._next_heartbeat_interval()):
            try:
                result = client.heartbeat(holder_id, session=self.runner_registry.session)
            except legacy_state.RunnerRegistryUnavailable:
                legacy_state.logger.debug("Runner-registry heartbeat unavailable; will retry")
                continue
            except Exception:
                legacy_state.logger.warning(
                    "Unexpected runner-registry heartbeat error; will retry",
                    exc_info=True,
                )
                continue

            if not result.alive:
                try:
                    rereg = client.register(
                        holder_id=holder_id,
                        workflow_run_id=legacy_state.os.environ.get("GITHUB_RUN_ID", ""),
                        workflow_name=legacy_state.os.environ.get("GITHUB_WORKFLOW", ""),
                        proxy_hash=legacy_state.proxy_pool_hash(legacy_state._resolve_proxy_pool_json()),
                        proxy_pool=legacy_state.proxy_pool_summary_for_registry(legacy_state.PROXY_POOL),
                        session=self.runner_registry.session,
                    )
                    legacy_state.logger.info("Runner-registry recovered after eviction")
                    self._apply_movie_claim_recommendation(rereg.movie_claim_recommended)
                    legacy_state._sync_legacy_globals_from_runtime(self)
                    self._apply_sleep_runner_count(len(rereg.active_runners))
                    if rereg.config is not None:
                        self._apply_config_snapshot(rereg.config)
                    self._apply_active_signals(rereg.active_signals)
                except legacy_state.RunnerRegistryUnavailable:
                    legacy_state.logger.debug("Runner-registry re-register unavailable; will retry")
                except Exception:
                    legacy_state.logger.warning(
                        "Unexpected runner-registry re-register error",
                        exc_info=True,
                    )
                continue

            self._apply_movie_claim_recommendation(result.movie_claim_recommended)
            legacy_state._sync_legacy_globals_from_runtime(self)
            self._apply_sleep_runner_count(result.active_runners_count)
            if result.config is not None:
                self._apply_config_snapshot(result.config)
            self._apply_active_signals(result.active_signals)

    def _unregister_runner_at_exit(self) -> None:
        import javdb.spider.runtime.state as legacy_state

        if self.runner_registry.unregistered:
            return
        client = self.services.runner_registry_client
        if client is None:
            return
        self.runner_registry.heartbeat_stop.set()
        thread = self.runner_registry.heartbeat_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=5.0)
            if thread.is_alive():
                legacy_state.logger.warning(
                    "Runner-registry heartbeat thread did not stop before unregister"
                )
            else:
                self.runner_registry.heartbeat_thread = None
        try:
            client.unregister(self.runner_registry.holder_id, session=self.runner_registry.session)
        except legacy_state.RunnerRegistryUnavailable:
            legacy_state.logger.debug("Runner-registry unregister unavailable at exit")
        except Exception:
            legacy_state.logger.warning(
                "Unexpected runner-registry unregister error",
                exc_info=True,
            )
        else:
            self.runner_registry.unregistered = True
        with contextlib.suppress(Exception):
            client.close()
        self.services.runner_registry_client = None
        if self.runner_registry.heartbeat_thread is not None and not self.runner_registry.heartbeat_thread.is_alive():
            self.runner_registry.heartbeat_thread = None
        legacy_state._sync_legacy_globals_from_runtime(self)

    def setup_runner_registry_client(self):
        import os as _os
        import atexit as _atexit
        import threading as _threading
        import javdb.spider.runtime.state as legacy_state
        from javdb.infra.config import cfg

        if self.services.runner_registry_client is not None:
            return self.services.runner_registry_client

        url = (cfg("PROXY_COORDINATOR_URL", "") or "").strip()
        token = (cfg("PROXY_COORDINATOR_TOKEN", "") or "").strip()
        enabled_raw = (str(cfg("RUNNER_REGISTRY_ENABLED", "") or "")).strip().lower()
        if enabled_raw not in {"1", "true", "yes"}:
            legacy_state.logger.info(
                "Runner-registry client disabled (RUNNER_REGISTRY_ENABLED=%r) — runner is invisible to peers",
                enabled_raw,
            )
            self.services.runner_registry_client = None
            return None
        if not url or not token:
            legacy_state.logger.info(
                "Runner-registry client not configured (PROXY_COORDINATOR_URL/TOKEN unset) — runner is invisible to peers",
            )
            self.services.runner_registry_client = None
            return None
        prior = (
            _os.environ.get("PROXY_COORDINATOR_URL"),
            _os.environ.get("PROXY_COORDINATOR_TOKEN"),
            _os.environ.get("RUNNER_REGISTRY_ENABLED"),
        )
        try:
            _os.environ["PROXY_COORDINATOR_URL"] = url
            _os.environ["PROXY_COORDINATOR_TOKEN"] = token
            _os.environ["RUNNER_REGISTRY_ENABLED"] = enabled_raw
            client = legacy_state.create_runner_registry_client_from_env()
        finally:
            for key, value in zip(
                ("PROXY_COORDINATOR_URL", "PROXY_COORDINATOR_TOKEN", "RUNNER_REGISTRY_ENABLED"),
                prior,
                strict=True,
            ):
                if value is None:
                    _os.environ.pop(key, None)
                else:
                    _os.environ[key] = value

        if client is None:
            self.services.runner_registry_client = None
            return None

        pool_json = legacy_state._resolve_proxy_pool_json()
        self_hash = legacy_state.proxy_pool_hash(pool_json)
        try:
            result = client.register(
                holder_id=self.runner_registry.holder_id,
                workflow_run_id=_os.environ.get("GITHUB_RUN_ID", ""),
                workflow_name=_os.environ.get("GITHUB_WORKFLOW", ""),
                proxy_hash=self_hash,
                proxy_pool=legacy_state.proxy_pool_summary_for_registry(legacy_state.PROXY_POOL),
            )
            legacy_state.logger.info(
                "Runner-registry client initialised: base_url=%s, holder_id=%s, active_runners=%d, movie_claim_recommended=%s",
                url,
                self.runner_registry.holder_id,
                len(result.active_runners),
                result.movie_claim_recommended,
            )
            legacy_state._warn_on_proxy_pool_drift(self_hash, result.pool_hash_summary)
            self._maybe_honour_pipeline_pause(
                pipeline_paused_until_ms=result.pipeline_paused_until,
                reason=result.pipeline_pause_reason,
            )
            self._apply_movie_claim_recommendation(result.movie_claim_recommended)
            legacy_state._sync_legacy_globals_from_runtime(self)
            self._apply_sleep_runner_count(len(result.active_runners))
        except legacy_state.RunnerRegistryUnavailable:
            legacy_state.logger.warning(
                "Runner-registry register failed at startup; continuing without registry coordination this run",
            )
            client.close()
            self.services.runner_registry_client = None
            return None
        except Exception:
            legacy_state.logger.warning(
                "Unexpected runner-registry register error; continuing without registry coordination this run",
                exc_info=True,
            )
            client.close()
            self.services.runner_registry_client = None
            return None

        self.services.runner_registry_client = client
        self.runner_registry.heartbeat_stop.clear()
        if self.runner_registry.heartbeat_thread is None or not self.runner_registry.heartbeat_thread.is_alive():
            self.runner_registry.heartbeat_thread = _threading.Thread(
                target=self._runner_heartbeat_loop,
                args=(client, self.runner_registry.holder_id),
                name="runner-heartbeat",
                daemon=True,
            )
            self.runner_registry.heartbeat_thread.start()
        _atexit.register(self._unregister_runner_at_exit)
        legacy_state._sync_legacy_globals_from_runtime(self)
        return client

    def setup_proxy_coordinator(self):
        import javdb.spider.runtime.state as legacy_state
        from javdb.infra.config import cfg

        if self.services.proxy_coordinator is not None:
            return self.services.proxy_coordinator

        url = (cfg("PROXY_COORDINATOR_URL", "") or "").strip()
        token = (cfg("PROXY_COORDINATOR_TOKEN", "") or "").strip()
        if not url or not token:
            legacy_state.logger.info(
                "Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) "
                "— using local throttling only",
            )
            self.services.proxy_coordinator = None
            legacy_state._sync_legacy_globals_from_runtime(self)
            return None

        client = legacy_state.ProxyCoordinatorClient(base_url=url, token=token)
        if not client.health_check():
            legacy_state.logger.error(
                "Proxy coordinator URL %s is configured but /health did not respond — "
                "falling back to local throttling for this run",
                url,
            )
            self.services.proxy_coordinator = None
            legacy_state._sync_legacy_globals_from_runtime(self)
            return None
        legacy_state.logger.info(
            "Proxy coordinator client initialised: base_url=%s",
            url,
        )
        self.services.proxy_coordinator = client
        legacy_state.set_remote_ban_hook(client.mark_proxy_banned)
        legacy_state.set_remote_unban_hook(client.mark_proxy_unbanned)

        try:
            from javdb.spider.runtime.sleep import ensure_sleep_runtime
            _mgr = ensure_sleep_runtime(self).movie_sleep_mgr
            if not _mgr.has_coordinator():
                _mgr.set_coordinator(client)
                legacy_state.logger.debug("Coordinator injected into movie_sleep_mgr")
        except Exception:
            legacy_state.logger.debug("Failed to inject coordinator into movie_sleep_mgr", exc_info=True)

        legacy_state._sync_legacy_globals_from_runtime(self)
        return client

    def setup_login_state_client(self):
        import javdb.spider.runtime.state as legacy_state
        from javdb.infra.config import cfg

        if self.services.login_state_client is not None:
            return self.services.login_state_client

        url = (cfg("PROXY_COORDINATOR_URL", "") or "").strip()
        token = (cfg("PROXY_COORDINATOR_TOKEN", "") or "").strip()
        if not url or not token:
            legacy_state.logger.info(
                "Login-state client not configured (PROXY_COORDINATOR_URL/TOKEN unset) "
                "— using per-runner login only",
            )
            self.services.login_state_client = None
            legacy_state._sync_legacy_globals_from_runtime(self)
            return None

        client = legacy_state.LoginStateClient(base_url=url, token=token)
        if not client.health_check():
            legacy_state.logger.error(
                "Login-state Worker URL %s is configured but /health did not respond — "
                "falling back to per-runner login for this run",
                url,
            )
            client.close()
            self.services.login_state_client = None
            legacy_state._sync_legacy_globals_from_runtime(self)
            return None
        legacy_state.logger.info(
            "Login-state client initialised: base_url=%s, holder_id=%s",
            url, self.runner_registry.holder_id,
        )
        self.services.login_state_client = client
        legacy_state._sync_legacy_globals_from_runtime(self)
        return client

    def setup_work_distributor_client(self):
        import javdb.spider.runtime.state as legacy_state

        if self.services.work_distributor_client is not None:
            return self.services.work_distributor_client

        try:
            from javdb.proxy.coordinator.work_distributor_client import (
                create_work_distributor_client_from_env,
            )
        except Exception:
            legacy_state.logger.warning("WorkDistributor client import failed", exc_info=True)
            return None

        try:
            client = create_work_distributor_client_from_env()
        except Exception:
            legacy_state.logger.warning("WorkDistributor client setup failed", exc_info=True)
            return None

        self.services.work_distributor_client = client
        legacy_state._sync_legacy_globals_from_runtime(self)
        return client

    def initialize_request_handler(self):
        import javdb.spider.runtime.state as legacy_state
        from javdb.spider.runtime.sleep import ensure_sleep_runtime

        sleep_ctx = ensure_sleep_runtime(self)
        _pt = sleep_ctx.penalty_tracker
        _mgr = sleep_ctx.movie_sleep_mgr
        _cd = _mgr.get_cooldown()
        config = legacy_state.RequestConfig(
            base_url=legacy_state.BASE_URL,
            cf_bypass_service_port=legacy_state.CF_BYPASS_SERVICE_PORT,
            cf_bypass_port_map=legacy_state.CF_BYPASS_PORT_MAP,
            cf_bypass_enabled=legacy_state.CF_BYPASS_ENABLED,
            cf_bypass_max_failures=3,
            cf_turnstile_cooldown=_cd,
            fallback_cooldown=_cd,
            javdb_session_cookie=legacy_state.JAVDB_SESSION_COOKIE,
            proxy_http=legacy_state.PROXY_HTTP,
            proxy_https=legacy_state.PROXY_HTTPS,
            proxy_modules=legacy_state.PROXY_MODULES,
            proxy_mode=legacy_state.PROXY_MODE,
            between_attempt_sleep=_mgr.sleep,
        )

        def _global_cf_event_cb(proxy_name):
            coord = self.services.proxy_coordinator
            if coord is None or not proxy_name:
                return
            coord.report_async(proxy_name, "cf")

        def _global_request_complete_cb(proxy_name, kind, latency_ms):
            coord = self.services.proxy_coordinator
            if coord is None or not proxy_name:
                return
            coord.report_async(proxy_name, kind, latency_ms=latency_ms)

        self.services.request_handler = legacy_state.RequestHandler(
            proxy_pool=self.services.proxy_pool, config=config, penalty_tracker=_pt,
            on_cf_event=_global_cf_event_cb,
            on_request_complete=_global_request_complete_cb,
        )
        legacy_state.logger.info("Request handler initialized successfully")
        legacy_state._sync_legacy_globals_from_runtime(self)

    def setup_proxy_pool(self, use_proxy) -> None:
        import atexit as _atexit
        import javdb.spider.runtime.state as legacy_state
        from javdb.proxy.policy import is_proxy_mode_disabled

        self.setup_proxy_coordinator()
        self.setup_login_state_client()
        self.setup_movie_claim_client()
        self.enforce_movie_claim_for_d1()
        _atexit.register(self._sweep_movie_claim_stages_at_exit)
        self.setup_runner_registry_client()
        self.setup_work_distributor_client()

        if is_proxy_mode_disabled(legacy_state.PROXY_MODE):
            legacy_state.logger.info(
                "Proxy globally disabled (PROXY_MODE='%s') - skipping pool init",
                legacy_state.PROXY_MODE,
            )
            self.services.proxy_pool = None
            legacy_state._sync_legacy_globals_from_runtime(self)
            return

        if not use_proxy:
            legacy_state.logger.info("Proxy disabled for this run (--no-proxy) - skipping pool init")
            self.services.proxy_pool = None
            legacy_state._sync_legacy_globals_from_runtime(self)
            return

        if legacy_state.PROXY_POOL and len(legacy_state.PROXY_POOL) > 0:
            if legacy_state.PROXY_MODE == "pool":
                legacy_state.logger.info(
                    f"Initializing proxy pool with {len(legacy_state.PROXY_POOL)} proxies..."
                )
                self.services.proxy_pool = legacy_state.create_proxy_pool_from_config(
                    legacy_state.PROXY_POOL,
                    max_failures=legacy_state.PROXY_POOL_MAX_FAILURES,
                )
                legacy_state.logger.info("Proxy pool initialized successfully")
                legacy_state.logger.info(
                    "Max failures before ban: %d (session-scoped)",
                    legacy_state.PROXY_POOL_MAX_FAILURES,
                )
            elif legacy_state.PROXY_MODE == "single":
                legacy_state.logger.info("Initializing single proxy mode (using first proxy from pool)...")
                self.services.proxy_pool = legacy_state.create_proxy_pool_from_config(
                    [legacy_state.PROXY_POOL[0]],
                    max_failures=legacy_state.PROXY_POOL_MAX_FAILURES,
                )
                legacy_state.logger.info(
                    f"Single proxy initialized: {legacy_state.PROXY_POOL[0].get('name', 'Main-Proxy')}"
                )
        elif legacy_state.PROXY_HTTP or legacy_state.PROXY_HTTPS:
            legacy_state.logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
            legacy_proxy = {
                "name": "Legacy-Proxy",
                "http": legacy_state.PROXY_HTTP,
                "https": legacy_state.PROXY_HTTPS,
            }
            self.services.proxy_pool = legacy_state.create_proxy_pool_from_config(
                [legacy_proxy],
                max_failures=legacy_state.PROXY_POOL_MAX_FAILURES,
            )
        else:
            if legacy_state.should_proxy_module(
                "spider",
                use_proxy,
                legacy_state.PROXY_MODULES,
                proxy_mode=legacy_state.PROXY_MODE,
            ):
                legacy_state.logger.warning(
                    "Proxy enabled but no proxy configuration found (neither PROXY_POOL nor PROXY_HTTP/PROXY_HTTPS)"
                )
            self.services.proxy_pool = None

        if (
            self.services.proxy_pool is not None
            and hasattr(self.services.proxy_pool, "set_health_provider")
        ):
            provider_label = None
            try:
                from javdb.proxy.recommend.client import (
                    create_recommend_proxy_client_from_env,
                )
                from javdb.proxy.recommend.policy import (
                    RecommendProxyPolicy,
                )
                rec_client = create_recommend_proxy_client_from_env()
                if rec_client is not None:
                    proxy_ids = [p.get("name", "") for p in (legacy_state.PROXY_POOL or [])
                                 if isinstance(p, dict) and p.get("name")]
                    policy = RecommendProxyPolicy(rec_client, proxy_ids=proxy_ids)
                    policy.start()
                    self.services.recommend_proxy_policy = policy
                    self.services.proxy_pool.set_health_provider(policy.score_for)
                    _atexit.register(policy.shutdown)
                    provider_label = "W5.5 /recommend_proxy"
            except Exception:
                legacy_state.logger.warning(
                    "Failed to wire RecommendProxy policy; will fall back to local cache",
                    exc_info=True,
                )

            if provider_label is None and self.services.proxy_coordinator is not None:
                try:
                    self.services.proxy_pool.set_health_provider(
                        self.services.proxy_coordinator.get_proxy_health_score
                    )
                    provider_label = "P2-D coordinator cache"
                except Exception:
                    legacy_state.logger.warning(
                        "Failed to wire proxy health provider; falling back to round-robin",
                        exc_info=True,
                    )

            if provider_label is not None:
                legacy_state.logger.info(
                    "Proxy pool health-weighted selection enabled (%s)",
                    provider_label,
                )
        legacy_state._sync_legacy_globals_from_runtime(self)
