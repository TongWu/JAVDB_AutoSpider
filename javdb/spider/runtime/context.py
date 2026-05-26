from __future__ import annotations

from dataclasses import dataclass, field
import threading
import uuid
from typing import Any, Optional

from javdb.spider.runtime.config import LOGIN_ATTEMPTS_PER_PROXY_LIMIT, PROXY_POOL


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
    last_applied_config_version: int = -1


@dataclass
class MovieClaimRuntimeState:
    client_pending: Any = None
    client_public: Any = None
    mode: str = "off"
    intended_mode: str = "off"
    last_recommended: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    swept_at_exit: bool = False


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

    def close(self) -> None:
        with self._close_lock:
            if self.closed:
                return
            self.closed = True
