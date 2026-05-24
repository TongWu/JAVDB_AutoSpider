"""Tests for _downloaded_map_by_href caching (TTL-based).

The downloaded map should be cached server-side so that rapid calls from the
Browse page (IntersectionObserver batches every ~150 ms) do not re-read the
CSV file on every request.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from apps.api.services import explore_service


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the downloaded-map cache before each test."""
    explore_service._DOWNLOADED_MAP_CACHE.clear()
    yield
    explore_service._DOWNLOADED_MAP_CACHE.clear()


def _write_csv(path: Path, hrefs: list[str]) -> None:
    lines = ["href,title"] + [f"{h},movie" for h in hrefs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestDownloadedMapCacheTTL:
    """_downloaded_map_by_href should cache its result for TTL seconds."""

    def test_second_call_within_ttl_returns_cached_result(self, tmp_path: Path):
        csv_file = tmp_path / "history.csv"
        _write_csv(csv_file, ["/v/ABC-123"])

        with patch.object(
            explore_service, "_resolved_history_csv_path", return_value=csv_file
        ):
            cfg: dict = {}
            result1 = explore_service._downloaded_map_by_href(cfg)
            assert result1.get("/v/ABC-123") is True

            _write_csv(csv_file, ["/v/ABC-123", "/v/DEF-456"])

            result2 = explore_service._downloaded_map_by_href(cfg)
            assert result2.get("/v/DEF-456") is None, (
                "Should return cached map, not re-read file"
            )

    def test_cache_expires_after_ttl(self, tmp_path: Path):
        csv_file = tmp_path / "history.csv"
        _write_csv(csv_file, ["/v/ABC-123"])

        with (
            patch.object(
                explore_service, "_resolved_history_csv_path", return_value=csv_file
            ),
            patch.object(explore_service, "time") as mock_time,
        ):
            mock_time.time.return_value = 1000.0
            cfg: dict = {}
            result1 = explore_service._downloaded_map_by_href(cfg)
            assert result1.get("/v/ABC-123") is True

            _write_csv(csv_file, ["/v/ABC-123", "/v/DEF-456"])

            mock_time.time.return_value = 1000.0 + 11
            result2 = explore_service._downloaded_map_by_href(cfg)
            assert result2.get("/v/DEF-456") is True, (
                "Cache should have expired; new file content should be read"
            )

    def test_different_paths_use_separate_cache_entries(self, tmp_path: Path):
        csv_a = tmp_path / "a.csv"
        csv_b = tmp_path / "b.csv"
        _write_csv(csv_a, ["/v/A-001"])
        _write_csv(csv_b, ["/v/B-001"])

        cfg: dict = {}
        with patch.object(
            explore_service, "_resolved_history_csv_path", return_value=csv_a
        ):
            result_a = explore_service._downloaded_map_by_href(cfg)

        with patch.object(
            explore_service, "_resolved_history_csv_path", return_value=csv_b
        ):
            result_b = explore_service._downloaded_map_by_href(cfg)

        assert result_a.get("/v/A-001") is True
        assert result_a.get("/v/B-001") is None
        assert result_b.get("/v/B-001") is True
        assert result_b.get("/v/A-001") is None

    def test_cache_respects_configurable_ttl(self, tmp_path: Path):
        csv_file = tmp_path / "history.csv"
        _write_csv(csv_file, ["/v/X-001"])

        with (
            patch.object(
                explore_service, "_resolved_history_csv_path", return_value=csv_file
            ),
            patch.object(explore_service, "time") as mock_time,
            patch(
                "apps.api.services.context.EXPLORE_DOWNLOADED_MAP_CACHE_TTL_SECONDS",
                5,
            ),
        ):
            cfg: dict = {}
            mock_time.time.return_value = 1000.0
            explore_service._downloaded_map_by_href(cfg)

            _write_csv(csv_file, ["/v/X-001", "/v/Y-001"])

            mock_time.time.return_value = 1000.0 + 4
            result = explore_service._downloaded_map_by_href(cfg)
            assert result.get("/v/Y-001") is None, "Should still be cached at 4s"

            mock_time.time.return_value = 1000.0 + 6
            result = explore_service._downloaded_map_by_href(cfg)
            assert result.get("/v/Y-001") is True, "Cache should expire after 5s TTL"
