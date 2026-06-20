"""Golden regression tests for the Rust-Required folder-dedup cascade.

ADR-048 Phase 3b: the Python cascade is gone — Rust (`analyze_folder_dedup`,
wrapped by `dedup.analyze_duplicates_for_code`) is now the sole keep/delete
authority. The migration-time Python-vs-Rust parity guard (Phase 3a) is
retired per the ADR ("a parity test is a migration-time guard, not a
steady-state policy"); its diverse fixtures live on here as a **golden**
regression set, frozen from the verified Rust decision (which Phase 3a proved
equal to the Python cascade across a 20k-input brute force).

Three layers of protection:
  * golden keep/delete + byte-identical reason strings on each fixture,
  * a self-contained partition invariant (no Python oracle needed),
  * the Rust-Required chokepoint guard fires when the wheel is absent.

Fixtures vary: single-folder, youma-only, wuma-only, youma+wuma combos,
sensor-priority ladders, equal-priority ties, the size-exception boundary
(just-over, exactly-1.30x, smaller, zero-中字, winner-size-not-max).
"""
# ruff: noqa: E402

import logging
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import pytest

from javdb.integrations.rclone import dedup
from javdb.integrations.rclone.dedup import analyze_duplicates_for_code
from javdb.integrations.rclone.types import FolderInfo


MB = 1024 * 1024
# Exactly-1.30x boundary: 104857600 * 1.30 == 136314880.0 (f64 exact).
ZHONGZI_BOUNDARY = 104_857_600
WUZI_EXACT_130 = 136_314_880


def _folder(code: str, sensor: str, subtitle: str, size: int, tag: str) -> FolderInfo:
    """Build a FolderInfo with a unique full_path so identity is unambiguous.

    ``tag`` makes the full_path unique even when two folders share the same
    (sensor, subtitle) — important for partition/identity comparison — and is
    the key used to express the golden compactly.
    """
    folder_name = f"{code} [{sensor}-{subtitle}] {tag}"
    return FolderInfo(
        full_path=f"gdrive:Movies/2024/Actor/{code}/{folder_name}",
        year="2024",
        actor="Actor",
        movie_code=code,
        sensor_category=sensor,
        subtitle_category=subtitle,
        folder_name=folder_name,
        size=size,
        file_count=1,
    )


def _tag(folder: FolderInfo) -> str:
    return folder.folder_name.rsplit(" ", 1)[-1]


def _gen_fixtures():
    """Generate a diverse list of (label, folders) fixtures."""
    fixtures = []

    def add(label, folders):
        fixtures.append((label, folders))

    code = "ABC-123"

    # Single / trivial
    add("single_youma", [_folder(code, "有码", "中字", 100 * MB, "a")])
    add("single_wuma", [_folder(code, "无码", "无字", 100 * MB, "a")])

    # Youma subtitle dedup
    add("youma_zhongzi_wuzi", [
        _folder(code, "有码", "中字", 100 * MB, "a"),
        _folder(code, "有码", "无字", 100 * MB, "b"),
    ])
    add("youma_two_zhongzi", [
        _folder(code, "有码", "中字", 100 * MB, "a"),
        _folder(code, "有码", "中字", 200 * MB, "b"),
    ])
    add("youma_only_wuzi", [
        _folder(code, "有码", "无字", 100 * MB, "a"),
        _folder(code, "有码", "无字", 200 * MB, "b"),
    ])

    # Youma size exception boundary
    add("youma_size_just_over", [
        _folder(code, "有码", "中字", 100 * MB, "a"),
        _folder(code, "有码", "无字", 150 * MB, "b"),
    ])
    add("youma_size_exactly_130", [
        _folder(code, "有码", "中字", ZHONGZI_BOUNDARY, "a"),
        _folder(code, "有码", "无字", WUZI_EXACT_130, "b"),
    ])
    add("youma_size_smaller", [
        _folder(code, "有码", "中字", 100 * MB, "a"),
        _folder(code, "有码", "无字", 80 * MB, "b"),
    ])
    add("youma_size_zero_zhongzi", [
        _folder(code, "有码", "中字", 0, "a"),
        _folder(code, "有码", "无字", 100 * MB, "b"),
    ])
    # Multiple 中字 with different sizes => max is used for the threshold.
    add("youma_max_zhongzi_threshold", [
        _folder(code, "有码", "中字", 50 * MB, "a"),
        _folder(code, "有码", "中字", 100 * MB, "b"),
        _folder(code, "有码", "无字", 140 * MB, "c"),  # > 1.30*100 -> kept
    ])

    # Wuma sensor priority ladders
    add("wuma_liuchu_over_wuma", [
        _folder(code, "无码流出", "无字", 100 * MB, "a"),
        _folder(code, "无码", "无字", 100 * MB, "b"),
    ])
    add("wuma_over_pojie", [
        _folder(code, "无码", "无字", 100 * MB, "a"),
        _folder(code, "无码破解", "无字", 100 * MB, "b"),
    ])
    add("wuma_full_ladder_wuzi", [
        _folder(code, "无码流出", "无字", 100 * MB, "a"),
        _folder(code, "无码", "无字", 100 * MB, "b"),
        _folder(code, "无码破解", "无字", 100 * MB, "c"),
    ])
    # Equal-priority tie: two 无码 -> STABLE sort keeps the first in input order.
    add("wuma_equal_priority_tie", [
        _folder(code, "无码", "无字", 100 * MB, "first"),
        _folder(code, "无码", "无字", 999 * MB, "second"),  # bigger but later
    ])

    # Wuma zhongzi vs wuzi
    add("wuma_zhongzi_beats_wuzi", [
        _folder(code, "无码流出", "无字", 100 * MB, "a"),
        _folder(code, "无码", "中字", 100 * MB, "b"),
    ])
    add("wuma_zhongzi_size_exception", [
        _folder(code, "无码流出", "中字", 100 * MB, "a"),
        _folder(code, "无码流出", "无字", 140 * MB, "b"),
    ])
    add("wuma_zhongzi_size_exactly_130", [
        _folder(code, "无码流出", "中字", ZHONGZI_BOUNDARY, "a"),
        _folder(code, "无码流出", "无字", WUZI_EXACT_130, "b"),
    ])
    # The size exception compares against the KEPT priority winner's size, not
    # max-of-all 中字: winner 无码流出(100MB) beats loser 无码(200MB), and the
    # 无字(150MB) is kept (150 > 1.30*100) — under max-zhongzi(200MB) it would
    # be deleted (150 < 1.30*200).
    add("wuma_winner_smaller_than_loser_size_exception", [
        _folder(code, "无码流出", "中字", 100 * MB, "winner"),
        _folder(code, "无码", "中字", 200 * MB, "loser"),
        _folder(code, "无码流出", "无字", 150 * MB, "wuzi"),
    ])

    # Complex wuma
    add("complex_wuma", [
        _folder(code, "无码流出", "中字", 100 * MB, "a"),
        _folder(code, "无码流出", "无字", 100 * MB, "b"),
        _folder(code, "无码", "中字", 100 * MB, "c"),
        _folder(code, "无码破解", "无字", 100 * MB, "d"),
    ])

    # Youma + wuma processed independently
    add("youma_and_wuma_independent", [
        _folder(code, "有码", "中字", 100 * MB, "a"),
        _folder(code, "有码", "无字", 100 * MB, "b"),
        _folder(code, "无码", "中字", 100 * MB, "c"),
        _folder(code, "无码", "无字", 100 * MB, "d"),
    ])
    add("youma_and_full_wuma_ladder", [
        _folder(code, "有码", "中字", 100 * MB, "a"),
        _folder(code, "有码", "无字", 100 * MB, "b"),
        _folder(code, "无码流出", "中字", 100 * MB, "c"),
        _folder(code, "无码", "无字", 100 * MB, "d"),
        _folder(code, "无码破解", "无字", 100 * MB, "e"),
        _folder(code, "无码破解", "中字", 100 * MB, "f"),
    ])

    return fixtures


_FIXTURES = _gen_fixtures()
_FIXTURE_IDS = [label for label, _ in _FIXTURES]


# Golden keep/delete decision, frozen from the verified Rust cascade
# (Phase 3a proved Rust == the legacy Python cascade across 20k random inputs).
# Keys are the per-folder ``tag``; delete maps tag -> exact reason string.
_GOLDEN = {
    'single_youma': {'keep': ['a'], 'delete': {}},
    'single_wuma': {'keep': ['a'], 'delete': {}},
    'youma_zhongzi_wuzi': {
        'keep': ['a'],
        'delete': {'b': 'Rule2: Subtitle version exists in 有码 category, delete no-subtitle version'},
    },
    'youma_two_zhongzi': {'keep': ['a', 'b'], 'delete': {}},
    'youma_only_wuzi': {'keep': ['a', 'b'], 'delete': {}},
    'youma_size_just_over': {'keep': ['a', 'b'], 'delete': {}},
    'youma_size_exactly_130': {
        'keep': ['a'],
        'delete': {'b': 'Rule2: Subtitle version exists in 有码 category, delete no-subtitle version'},
    },
    'youma_size_smaller': {
        'keep': ['a'],
        'delete': {'b': 'Rule2: Subtitle version exists in 有码 category, delete no-subtitle version'},
    },
    'youma_size_zero_zhongzi': {
        'keep': ['a'],
        'delete': {'b': 'Rule2: Subtitle version exists in 有码 category, delete no-subtitle version'},
    },
    'youma_max_zhongzi_threshold': {'keep': ['a', 'b', 'c'], 'delete': {}},
    'wuma_liuchu_over_wuma': {
        'keep': ['a'],
        'delete': {'b': 'Rule1: Uncensored priority (无码流出 > 无码), keep 无码流出, delete 无码'},
    },
    'wuma_over_pojie': {
        'keep': ['a'],
        'delete': {'b': 'Rule1: Uncensored priority (无码 > 无码破解), keep 无码, delete 无码破解'},
    },
    'wuma_full_ladder_wuzi': {
        'keep': ['a'],
        'delete': {
            'b': 'Rule1: Uncensored priority (无码流出 > 无码), keep 无码流出, delete 无码',
            'c': 'Rule1: Uncensored priority (无码流出 > 无码破解), keep 无码流出, delete 无码破解',
        },
    },
    'wuma_equal_priority_tie': {
        'keep': ['first'],
        'delete': {'second': 'Rule1: Uncensored priority (无码 > 无码), keep 无码, delete 无码'},
    },
    'wuma_zhongzi_beats_wuzi': {
        'keep': ['b'],
        'delete': {'a': 'Rule2: Subtitle version exists (无码-中字), delete no-subtitle version'},
    },
    'wuma_zhongzi_size_exception': {'keep': ['a', 'b'], 'delete': {}},
    'wuma_zhongzi_size_exactly_130': {
        'keep': ['a'],
        'delete': {'b': 'Rule2: Subtitle version exists (无码流出-中字), delete no-subtitle version'},
    },
    'wuma_winner_smaller_than_loser_size_exception': {
        'keep': ['winner', 'wuzi'],
        'delete': {'loser': 'Rule1: Uncensored priority (无码流出 > 无码), keep 无码流出, delete 无码'},
    },
    'complex_wuma': {
        'keep': ['a'],
        'delete': {
            'b': 'Rule2: Subtitle version exists (无码流出-中字), delete no-subtitle version',
            'c': 'Rule1: Uncensored priority (无码流出 > 无码), keep 无码流出, delete 无码',
            'd': 'Rule1: Uncensored priority (无码流出 > 无码破解), keep 无码流出, delete 无码破解',
        },
    },
    'youma_and_wuma_independent': {
        'keep': ['a', 'c'],
        'delete': {
            'b': 'Rule2: Subtitle version exists in 有码 category, delete no-subtitle version',
            'd': 'Rule2: Subtitle version exists (无码-中字), delete no-subtitle version',
        },
    },
    'youma_and_full_wuma_ladder': {
        'keep': ['a', 'c'],
        'delete': {
            'b': 'Rule2: Subtitle version exists in 有码 category, delete no-subtitle version',
            'd': 'Rule2: Subtitle version exists (无码流出-中字), delete no-subtitle version',
            'e': 'Rule1: Uncensored priority (无码 > 无码破解), keep 无码, delete 无码破解',
            'f': 'Rule1: Uncensored priority (无码流出 > 无码破解), keep 无码流出, delete 无码破解',
        },
    },
}


@pytest.mark.parametrize("label,folders", _FIXTURES, ids=_FIXTURE_IDS)
class TestDedupCascadeGolden:
    def test_keep_matches_golden(self, label, folders):
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert sorted(_tag(f) for f in result.folders_to_keep) == _GOLDEN[label]["keep"], label

    def test_delete_matches_golden(self, label, folders):
        """Delete set AND byte-identical reason strings match the golden."""
        result = analyze_duplicates_for_code("ABC-123", folders)
        got = {_tag(f): reason for f, reason in result.folders_to_delete}
        assert got == _GOLDEN[label]["delete"], label

    def test_partition_is_total_and_disjoint(self, label, folders):
        """Every input folder lands in exactly one of keep/delete (D5), and a
        non-empty input never yields an empty keep set — checked against the
        input itself, no oracle needed."""
        result = analyze_duplicates_for_code("ABC-123", folders)
        keep = frozenset(f.full_path for f in result.folders_to_keep)
        delete = frozenset(f.full_path for f, _ in result.folders_to_delete)
        all_paths = frozenset(f.full_path for f in folders)
        assert keep | delete == all_paths, label
        assert keep & delete == frozenset(), label
        if folders:
            assert keep, label


class TestRustRequiredChokepoint:
    """ADR-048 D4: with the Rust wheel absent the cascade fails loud — never a
    silent Python fallback (there is none)."""

    def _two_folders(self):
        return [
            _folder("ABC-123", "有码", "中字", 100 * MB, "a"),
            _folder("ABC-123", "有码", "无字", 100 * MB, "b"),
        ]

    def test_analyze_duplicates_for_code_raises_without_rust(self, monkeypatch):
        monkeypatch.setattr(dedup, "_RUST_DEDUP_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="Rust-Required"):
            dedup.analyze_duplicates_for_code("ABC-123", self._two_folders())

    def test_analyze_all_duplicates_raises_without_rust(self, monkeypatch):
        monkeypatch.setattr(dedup, "_RUST_DEDUP_AVAILABLE", False)
        structure = {"2024": {"Actor": self._two_folders()}}
        with pytest.raises(RuntimeError, match="maturin develop"):
            dedup.analyze_all_duplicates(structure)


class TestAnalyzeAllDuplicatesFailSafe:
    """A per-code invariant violation is logged + skipped (no deletions for it),
    while the rest of the run proceeds — the load-bearing fail-safe now that
    Rust is the sole purge authority. Pins the two-tier except handler."""

    def test_one_bad_code_skipped_others_proceed(self, monkeypatch, caplog):
        good = [
            _folder("GOOD-1", "有码", "中字", 100 * MB, "a"),
            _folder("GOOD-1", "有码", "无字", 100 * MB, "b"),  # -> deleted (Rule2)
        ]
        bad = [
            _folder("BAD-1", "无码", "无字", 100 * MB, "a"),
            _folder("BAD-1", "无码", "无字", 200 * MB, "b"),
        ]
        structure = {"2024": {"Actor": good + bad}}

        real = dedup.analyze_duplicates_for_code

        def fake(code, folders):
            if code == "BAD-1":
                raise ValueError("simulated D5 invariant violation")
            return real(code, folders)

        monkeypatch.setattr(dedup, "analyze_duplicates_for_code", fake)

        with caplog.at_level(logging.ERROR, logger="javdb.integrations.rclone.dedup"):
            results = dedup.analyze_all_duplicates(structure, max_workers=1)

        codes = {r.movie_code for r in results}
        assert "GOOD-1" in codes  # the healthy code's deletions still land
        assert "BAD-1" not in codes  # the bad code is skipped, never purged
        assert "DEDUP_INVARIANT_VIOLATION" in caplog.text
        assert "BAD-1" in caplog.text
