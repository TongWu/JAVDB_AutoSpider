"""Shared backend contract for spider detail execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from packages.python.javdb_spider.fetch.fetch_engine import EngineResult, EngineTask


@dataclass(frozen=True)
class FetchRuntimeState:
    """Mutable-at-runtime flags surfaced by detail backends."""

    use_proxy: bool = False
    use_cf_bypass: bool = False


class FetchBackend(ABC):
    """Execution backend contract for spider detail processing."""

    @property
    @abstractmethod
    def worker_count(self) -> int:
        """Return the effective worker count for logging and stats."""

    @abstractmethod
    def start(self) -> None:
        """Start backend resources."""

    @abstractmethod
    def submit_task(self, task: "EngineTask") -> None:
        """Queue a task for execution."""

    @abstractmethod
    def mark_done(self) -> None:
        """Signal that no more tasks will be submitted."""

    @abstractmethod
    def results(self) -> Iterator["EngineResult"]:
        """Yield task results until the backend is drained."""

    @abstractmethod
    def shutdown(self, *, timeout: float = 10) -> list["EngineTask"]:
        """Stop backend resources and return unfinished tasks."""

    @abstractmethod
    def runtime_state(self) -> FetchRuntimeState:
        """Return the latest runtime flags produced by the backend."""
