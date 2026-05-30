from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StatsSinkResult:
    saved: bool
    backend: str | None
    error: str | None


@dataclass(frozen=True)
class UploaderStats:
    total_torrents: int = 0
    duplicate_count: int = 0
    attempted: int = 0
    successfully_added: int = 0
    failed_count: int = 0
    hacked_sub: int = 0
    hacked_nosub: int = 0
    subtitle_count: int = 0
    no_subtitle_count: int = 0
    success_rate: float = 0.0


@dataclass(frozen=True)
class PikPakStats:
    # Default mirrors db_save_pikpak_stats' semantic default (3 days). Callers
    # (the PikPak bridge) always pass an explicit value, so this only affects
    # bare PikPakStats() construction.
    threshold_days: int = 3
    total_torrents: int = 0
    filtered_old: int = 0
    successful_count: int = 0
    failed_count: int = 0
    uploaded_count: int = 0
    delete_failed_count: int = 0


def _use_sqlite() -> bool:
    from javdb.infra.config import use_sqlite

    return use_sqlite()


def _init_db() -> None:
    from javdb.storage.db import init_db

    init_db()


def _current_backend() -> str:
    from javdb.storage.db import current_backend

    return current_backend()


def _db_save_uploader_stats(session_id: str, payload: dict[str, Any]) -> None:
    from javdb.storage.repos.stats_repo import StatsRepo

    StatsRepo().save_uploader_stats(session_id, payload)


def _db_save_pikpak_stats(session_id: str, payload: dict[str, Any]) -> None:
    from javdb.storage.repos.stats_repo import StatsRepo

    StatsRepo().save_pikpak_stats(session_id, payload)


def save_uploader_stats(session_id: str | None, stats: UploaderStats) -> StatsSinkResult:
    if not session_id or not _use_sqlite():
        return StatsSinkResult(saved=False, backend=None, error=None)
    try:
        _init_db()
        _db_save_uploader_stats(session_id, asdict(stats))
        return StatsSinkResult(saved=True, backend=_current_backend(), error=None)
    except Exception as exc:
        return StatsSinkResult(saved=False, backend=None, error=str(exc))


def save_pikpak_stats(session_id: str | None, stats: PikPakStats) -> StatsSinkResult:
    if not session_id or not _use_sqlite():
        return StatsSinkResult(saved=False, backend=None, error=None)
    try:
        _init_db()
        _db_save_pikpak_stats(session_id, asdict(stats))
        return StatsSinkResult(saved=True, backend=_current_backend(), error=None)
    except Exception as exc:
        return StatsSinkResult(saved=False, backend=None, error=str(exc))
