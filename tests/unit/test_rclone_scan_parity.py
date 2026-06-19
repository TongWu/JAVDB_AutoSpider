"""Value-parity tests pinning the Rust rclone scan primitives to Python.

ADR-048 Phase 2: ``scan.py`` routes ``get_year_folders`` / ``get_actor_folders``
through Rust ``parse_lsd_output`` and ``get_all_movie_folders_for_year`` through
the (now 3-level-aware) Rust ``parse_lsjson_for_year``. These tests freeze the
pure-Python output as the golden and assert the Rust-routed output equals it,
key-for-key and order-insensitively. They also confirm the no-Rust monkeypatched
path still scans via the Python fallback.

The fixtures are hand-recorded ``rclone lsjson -R`` / ``lsd`` samples covering:
- multiple actors, multiple movie_codes per actor, multiple leaves per code
- file entries (depth >= 4) contributing size + file_count
- a leaf directory with NO files (size/count 0)
- invalid leaves (bad subtitle / no dash) that BOTH paths must skip
"""
# ruff: noqa: E402

import json
import os
import sys
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from javdb.integrations.rclone import scan
from javdb.integrations.rclone.scan import (
    _py_parse_lsjson_for_year,
    _py_parse_lsd_output,
    get_actor_folders,
    get_all_movie_folders_for_year,
    get_year_folders,
)


# ---------------------------------------------------------------------------
# Recorded fixtures
# ---------------------------------------------------------------------------

# Layout under a year dir: <actor>/<movie_code>/<sensor-subtitle>/<files...>
LSJSON_ENTRIES = [
    # ActorA / ABC-123: two valid leaves, one with files, one with none.
    {"Path": "ActorA", "IsDir": True},
    {"Path": "ActorA/ABC-123", "IsDir": True},
    {"Path": "ActorA/ABC-123/有码-中字", "IsDir": True},
    {"Path": "ActorA/ABC-123/有码-中字/movie.mp4", "IsDir": False, "Size": 1000},
    {"Path": "ActorA/ABC-123/有码-中字/cover.jpg", "IsDir": False, "Size": 50},
    # Dir with NO files -> size 0, file_count 0.
    {"Path": "ActorA/ABC-123/有码-无字", "IsDir": True},
    # ActorA / DEF-456: an uncensored leaf with multiple files.
    {"Path": "ActorA/DEF-456", "IsDir": True},
    {"Path": "ActorA/DEF-456/无码流出-无字", "IsDir": True},
    {"Path": "ActorA/DEF-456/无码流出-无字/part1.mkv", "IsDir": False, "Size": 200},
    {"Path": "ActorA/DEF-456/无码流出-无字/part2.mkv", "IsDir": False, "Size": 300},
    # ActorB / GHI-789: a valid leaf plus an INVALID leaf (bad subtitle) and a
    # dash-less leaf — both invalid ones must be skipped by both paths.
    {"Path": "ActorB", "IsDir": True},
    {"Path": "ActorB/GHI-789", "IsDir": True},
    {"Path": "ActorB/GHI-789/无码-中字", "IsDir": True},
    {"Path": "ActorB/GHI-789/无码-中字/v.mp4", "IsDir": False, "Size": 999},
    {"Path": "ActorB/GHI-789/无码-badsub", "IsDir": True},
    {"Path": "ActorB/GHI-789/无码-badsub/v.mp4", "IsDir": False, "Size": 5},
    {"Path": "ActorB/GHI-789/nodash", "IsDir": True},
    {"Path": "ActorB/GHI-789/nodash/v.mp4", "IsDir": False, "Size": 7},
    # Trailing-slash dir path: splits to 4 parts with IsDir=True, so it hits
    # neither the dir branch (len==3) nor the file branch (len>=4 requires
    # not-dir). BOTH paths silently drop it — pin that parity here.
    {"Path": "ActorX/CODE-001/无码-中字/", "IsDir": True},
]
LSJSON_FIXTURE = json.dumps(LSJSON_ENTRIES)

# rclone lsd output: -1 <date> <time> -1 <folder name>
LSD_YEARS = (
    "-1 2024-01-01 00:00:00 -1 2024\n"
    "-1 2024-01-01 00:00:00 -1 2025\n"
    "-1 2024-01-01 00:00:00 -1 未知\n"
    "-1 2024-01-01 00:00:00 -1 not-a-year\n"  # filtered out by get_year_folders
)
LSD_ACTORS = (
    "-1 2024-01-01 00:00:00 -1 Actor One\n"   # name with a space
    "-1 2024-01-01 00:00:00 -1 ActorB\n"
)


def _by_full_path(folders):
    """Normalize a List[FolderInfo] to a {full_path: dict} for order-insensitive
    comparison (both Rust HashMap and Python set iterate nondeterministically)."""
    return {f.full_path: asdict(f) for f in folders}


def _mock_run(stdout):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# lsjson parity (get_all_movie_folders_for_year)
# ---------------------------------------------------------------------------

class TestLsjsonParity:
    def test_rust_extension_is_active(self):
        # Guard: the parity assertions below are only meaningful if the Rust
        # path is actually exercised. If this fails, the build/shim is wrong.
        assert scan._RUST_RCLONE_PARSE is True

    def test_python_golden_shape(self):
        """Freeze the Python baseline: exact rows the parser must emit."""
        rows = _py_parse_lsjson_for_year(LSJSON_FIXTURE)
        by_key = {
            (r["actor"], r["movie_code"], r["folder_name"]): r for r in rows
        }
        assert set(by_key) == {
            ("ActorA", "ABC-123", "有码-中字"),
            ("ActorA", "ABC-123", "有码-无字"),
            ("ActorA", "DEF-456", "无码流出-无字"),
            ("ActorB", "GHI-789", "无码-中字"),
        }
        # files contribute size/count
        assert by_key[("ActorA", "ABC-123", "有码-中字")]["size"] == 1050
        assert by_key[("ActorA", "ABC-123", "有码-中字")]["file_count"] == 2
        # dir with no files -> 0/0
        assert by_key[("ActorA", "ABC-123", "有码-无字")]["size"] == 0
        assert by_key[("ActorA", "ABC-123", "有码-无字")]["file_count"] == 0
        # multi-file leaf
        assert by_key[("ActorA", "DEF-456", "无码流出-无字")]["size"] == 500
        assert by_key[("ActorA", "DEF-456", "无码流出-无字")]["file_count"] == 2
        # sensor/subtitle parsed
        row = by_key[("ActorB", "GHI-789", "无码-中字")]
        assert (row["sensor"], row["subtitle"]) == ("无码", "中字")

    def test_rust_rows_equal_python_rows(self):
        """Rust dict output == Python dict output, key-for-key."""
        py_rows = _py_parse_lsjson_for_year(LSJSON_FIXTURE)
        rs_rows = scan._rs_parse_lsjson_for_year(LSJSON_FIXTURE)

        def index(rows):
            return {
                (r["actor"], r["movie_code"], r["folder_name"]): r for r in rows
            }

        assert index(rs_rows) == index(py_rows)

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_get_all_movie_folders_rust_equals_python(self, mock_run):
        """End-to-end FolderInfo parity through get_all_movie_folders_for_year:
        Rust path vs. forced-Python path produce identical structures."""
        mock_run.return_value = _mock_run(LSJSON_FIXTURE)
        rust_folders = get_all_movie_folders_for_year("gdrive", "Movies", "2025")

        # Force the Python fallback for the same fixture.
        mock_run.return_value = _mock_run(LSJSON_FIXTURE)
        with patch.object(scan, "_RUST_RCLONE_PARSE", False):
            py_folders = get_all_movie_folders_for_year("gdrive", "Movies", "2025")

        assert _by_full_path(rust_folders) == _by_full_path(py_folders)
        # Sanity: full_path is assembled with year + 3-level path.
        paths = set(_by_full_path(rust_folders))
        assert "gdrive:Movies/2025/ActorA/ABC-123/有码-中字" in paths
        assert "gdrive:Movies/2025/ActorA/ABC-123/有码-无字" in paths
        assert len(paths) == 4  # invalid leaves skipped

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_no_rust_fallback_scans_via_python(self, mock_run):
        """The no-Rust monkeypatched path still scans correctly via the
        pure-Python fallback (no Rust call)."""
        mock_run.return_value = _mock_run(LSJSON_FIXTURE)
        # Make any accidental Rust call explode, proving the Python branch runs.
        with patch.object(scan, "_RUST_RCLONE_PARSE", False), patch.object(
            scan, "_rs_parse_lsjson_for_year",
            side_effect=AssertionError("Rust path must not be used when disabled"),
        ):
            folders = get_all_movie_folders_for_year("gdrive", "Movies", "2025")
        assert len(folders) == 4
        assert "gdrive:Movies/2025/ActorA/ABC-123/有码-中字" in _by_full_path(folders)

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_invalid_json_raises_runtime_error_on_both_paths(self, mock_run):
        """Error-contract parity: valid-but-non-array JSON must surface as a
        RuntimeError from get_all_movie_folders_for_year on BOTH the Rust path
        and the forced-Python fallback. Without the broadened except tuple the
        Python path would leak a raw TypeError/AttributeError on these inputs."""
        garbage_inputs = ["{}", "null", "42", "[1,2,3]"]
        for stdout in garbage_inputs:
            # Rust-routed path.
            mock_run.return_value = _mock_run(stdout)
            with pytest.raises(RuntimeError):
                get_all_movie_folders_for_year("gdrive", "Movies", "2025")
            # Forced Python-fallback path (Rust routing monkeypatched off).
            mock_run.return_value = _mock_run(stdout)
            with patch.object(scan, "_RUST_RCLONE_PARSE", False):
                with pytest.raises(RuntimeError):
                    get_all_movie_folders_for_year("gdrive", "Movies", "2025")

    def test_import_time_warning_when_rust_missing(self, caplog):
        """ADR-041 D3: importing scan.py with the Rust extension unavailable
        emits a loud WARNING and wires the pure-Python fallbacks."""
        import builtins
        import importlib

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "javdb.rust_core":
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)

        with caplog.at_level("WARNING", logger=scan.logger.name):
            with patch.object(builtins, "__import__", side_effect=_blocked_import):
                reloaded = importlib.reload(scan)
            try:
                assert reloaded._RUST_RCLONE_PARSE is False
                assert reloaded.parse_lsd_output is reloaded._py_parse_lsd_output
                assert reloaded.parse_folder_name is reloaded._py_parse_folder_name
                assert any(
                    "rust_core unavailable" in r.getMessage() for r in caplog.records
                )
            finally:
                # Restore the real (Rust-backed) module for the rest of the suite.
                importlib.reload(scan)
        assert scan._RUST_RCLONE_PARSE is True


# ---------------------------------------------------------------------------
# lsd parity (get_year_folders / get_actor_folders)
# ---------------------------------------------------------------------------

class TestLsdParity:
    def test_parse_lsd_rust_equals_python(self):
        assert scan._rs_parse_lsd_output(LSD_YEARS) == _py_parse_lsd_output(LSD_YEARS)
        assert scan._rs_parse_lsd_output(LSD_ACTORS) == _py_parse_lsd_output(LSD_ACTORS)

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_get_year_folders_filters_and_parses(self, mock_run):
        mock_run.return_value = _mock_run(LSD_YEARS)
        years = get_year_folders("gdrive", "Movies")
        assert years == ["2024", "2025", "未知"]  # not-a-year dropped

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_get_year_folders_rust_equals_python(self, mock_run):
        mock_run.return_value = _mock_run(LSD_YEARS)
        rust_years = get_year_folders("gdrive", "Movies")
        mock_run.return_value = _mock_run(LSD_YEARS)
        with patch.object(scan, "parse_lsd_output", _py_parse_lsd_output):
            py_years = get_year_folders("gdrive", "Movies")
        assert rust_years == py_years

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_get_actor_folders_preserves_spaces(self, mock_run):
        mock_run.return_value = _mock_run(LSD_ACTORS)
        actors = get_actor_folders("gdrive", "Movies", "2025")
        assert actors == ["Actor One", "ActorB"]

    @patch("javdb.integrations.rclone.scan.subprocess.run")
    def test_get_actor_folders_rust_equals_python(self, mock_run):
        mock_run.return_value = _mock_run(LSD_ACTORS)
        rust_actors = get_actor_folders("gdrive", "Movies", "2025")
        mock_run.return_value = _mock_run(LSD_ACTORS)
        with patch.object(scan, "parse_lsd_output", _py_parse_lsd_output):
            py_actors = get_actor_folders("gdrive", "Movies", "2025")
        assert rust_actors == py_actors
