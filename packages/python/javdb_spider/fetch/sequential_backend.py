"""Sequential detail backend that adapts fallback fetches to EngineResult."""

from __future__ import annotations

import queue as queue_module
from typing import Optional

from packages.python.javdb_platform.logging_config import get_logger

from packages.python.javdb_spider.fetch.backend import FetchBackend, FetchRuntimeState
from packages.python.javdb_spider.fetch.fallback import fetch_detail_page_with_fallback
from packages.python.javdb_spider.fetch.fetch_engine import EngineResult, EngineTask
from packages.python.javdb_spider.runtime.sleep import movie_sleep_mgr

logger = get_logger(__name__)


class SequentialFetchBackend(FetchBackend):
    """Single-worker detail backend built on fetch_detail_page_with_fallback."""

    def __init__(
        self,
        session,
        *,
        use_proxy: bool,
        use_cf_bypass: bool,
        use_cookie: bool,
        is_adhoc_mode: bool,
    ):
        self._session = session
        self._use_cookie = use_cookie
        self._is_adhoc_mode = is_adhoc_mode
        self._task_queue: queue_module.Queue[EngineTask] = queue_module.Queue()
        self._started = False
        self._done = False
        self._pending_movie_sleep = False
        self._runtime_state = FetchRuntimeState(
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        )

    @property
    def worker_count(self) -> int:
        return 1

    def start(self) -> None:
        self._started = True

    def submit_task(self, task: EngineTask) -> None:
        if self._done:
            raise RuntimeError("Cannot submit after mark_done()")
        self._task_queue.put(task)

    def mark_done(self) -> None:
        self._done = True

    def runtime_state(self) -> FetchRuntimeState:
        return FetchRuntimeState(
            use_proxy=self._runtime_state.use_proxy,
            use_cf_bypass=self._runtime_state.use_cf_bypass,
        )

    def results(self):
        if not self._started:
            self.start()

        while True:
            try:
                task = self._task_queue.get_nowait()
            except queue_module.Empty:
                return

            if self._pending_movie_sleep:
                movie_sleep_mgr.sleep()
                self._pending_movie_sleep = False

            yield self._process_task(task)

    def shutdown(self, *, timeout: float = 10) -> list[EngineTask]:
        del timeout

        self._done = True
        orphaned: list[EngineTask] = []
        while True:
            try:
                orphaned.append(self._task_queue.get_nowait())
            except queue_module.Empty:
                break
        return orphaned

    def _process_task(self, task: EngineTask) -> EngineResult:
        (
            magnets,
            actor_info,
            actor_gender,
            actor_link,
            supporting_actors,
            parse_success,
            effective_use_proxy,
            effective_use_cf_bypass,
        ) = fetch_detail_page_with_fallback(
            task.url,
            self._session,
            use_cookie=self._use_cookie,
            use_proxy=self._runtime_state.use_proxy,
            use_cf_bypass=self._runtime_state.use_cf_bypass,
            entry_index=task.entry_index,
            is_adhoc_mode=self._is_adhoc_mode,
        )

        if parse_success:
            self._runtime_state = FetchRuntimeState(
                use_proxy=effective_use_proxy,
                use_cf_bypass=effective_use_cf_bypass,
            )
            return EngineResult(
                task=task,
                success=True,
                data={
                    "magnets": magnets,
                    "actor_info": actor_info or "",
                    "actor_gender": actor_gender or "",
                    "actor_link": actor_link or "",
                    "supporting": supporting_actors or "",
                },
                used_cf=effective_use_cf_bypass,
                worker_name="sequential",
                _ack_callback=self._acknowledge_result,
            )

        return EngineResult(
            task=task,
            success=False,
            used_cf=effective_use_cf_bypass,
            error="fetch_failed",
            worker_name="sequential",
            _ack_callback=self._acknowledge_result,
        )

    def _acknowledge_result(
        self,
        outcome_status: str,
        runtime_state_changed: bool,
    ) -> None:
        if outcome_status in {"failed", "skipped"}:
            self._pending_movie_sleep = True
            return

        if outcome_status == "no_row":
            return

        if runtime_state_changed:
            movie_sleep_mgr.sleep()
            self._pending_movie_sleep = False
            return

        self._pending_movie_sleep = True
