"""Contract tests for the ``merge=union`` driver on append-only report files.

Every ingestion run auto-commits shared report files, and ``AdHocIngestion``
carries no ``concurrency:`` group, so parallel dispatches race on the same
paths. Before ``.gitattributes`` declared a merge driver, the push ladder had
to fall back to ``git rebase -X theirs``, which keeps this run's conflicting
hunks and drops the other run's rows (loud since 4cc7569a, still lossy).
``merge=union`` keeps BOTH sides' added lines, so the ladder's first rung —
plain ``git rebase`` — resolves those files with zero loss.

Union is only correct where the writer *appends*: it concatenates conflicting
hunks, which would corrupt any file rewritten as a whole document. These tests
pin both halves of that contract:

1. every path whose writer opens in append mode resolves to ``merge=union``;
2. the whole-file-rewrite reports never do —
   ``reports/D1/d1_port_summary.json`` (a ``json.dump``-ed summary object,
   where concatenating two halves is not valid JSON),
   ``reports/rclone_inventory.csv`` / ``reports/dedup_history.csv`` (full DB
   exports, mode ``'w'``), ``reports/parsed_movies_history.csv`` (rewritten
   whole with ``records.insert(0, …)``, so its diff is a reorder rather than
   an append), and the git-LFS ``reports/*.db`` binaries.

Both the textual parse and the ``git check-attr`` resolution are order-
insensitive, so re-ordering lines in ``.gitattributes`` cannot break them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

# The exact set of patterns allowed to carry ``merge=union``. Widening union
# coverage is a deliberate act that needs a writer-code review, so this set is
# asserted for equality, not containment.
EXPECTED_UNION_PATTERNS = frozenset(
    {
        # javdb/storage/drift_io.append_jsonl_record — open(..., "a")
        "reports/D1/d1_drift.jsonl",
        # javdb/migrations/tools/reconcile_d1_drift._archive_processed_records
        # — open(processed_log, "a")
        "reports/D1/d1_drift.processed.jsonl",
        # javdb/storage/d1_recovery.append_event — outbox.open("a")
        "reports/D1/d1_recovery_outbox.jsonl",
        # javdb/storage/d1_recovery.compact_replayed — processed_path.open("a")
        "reports/D1/d1_recovery_outbox.processed.jsonl",
        # javdb/integrations/pikpak/bridge/service.save_to_pikpak_history
        # — open(PIKPAK_HISTORY_FILE, "a"), header written once at creation
        "reports/pikpak_bridge_history.csv",
        # javdb/infra/csv_writer.write_csv(append_mode=True) — re-emits existing
        # rows in place and appends newly seen video_codes at EOF
        "reports/DailyReport/**/*.csv",
        "reports/AdHoc/**/*.csv",
    }
)

# Paths that must resolve to ``merge=union``. Dated entries are spelled out as
# real committed paths so the glob's directory depth is exercised, plus one
# flat path to pin that ``**`` also matches zero directories.
UNION_PATHS = (
    "reports/D1/d1_drift.jsonl",
    "reports/D1/d1_drift.processed.jsonl",
    "reports/D1/d1_recovery_outbox.jsonl",
    "reports/D1/d1_recovery_outbox.processed.jsonl",
    "reports/pikpak_bridge_history.csv",
    "reports/DailyReport/2026/08/Javdb_TodayTitle_20260811.csv",
    "reports/DailyReport/Javdb_TodayTitle_20260811.csv",
    "reports/AdHoc/2025/06/Javdb_TodayTitle_20250629.csv",
)

# Whole-file-rewrite reports: union would concatenate two rewrites.
NON_UNION_PATHS = (
    "reports/D1/d1_port_summary.json",
    "reports/rclone_inventory.csv",
    "reports/dedup_history.csv",
    "reports/parsed_movies_history.csv",
    # Per-run timestamped filenames — concurrent runs write distinct files, so
    # they need no driver at all.
    "reports/Dedup/2026/08/Dedup_Report_20260803_055928.csv",
    "reports/D1/sync_d1_to_sqlite/sync_d1_to_sqlite_apply_20260508_221535.json",
)

# LFS binaries: union on a pointer file (or on the binary) is nonsense.
LFS_DB_PATHS = (
    "reports/history.db",
    "reports/operations.db",
    "reports/reports.db",
)


def _parse_gitattributes() -> dict[str, list[str]]:
    """Map pattern → attribute list, ignoring blanks and comments."""
    parsed: dict[str, list[str]] = {}
    for raw in GITATTRIBUTES.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *attributes = line.split()
        parsed.setdefault(pattern, []).extend(attributes)
    return parsed


def _patterns_with_attribute(attribute: str) -> set[str]:
    return {
        pattern
        for pattern, attributes in _parse_gitattributes().items()
        if attribute in attributes
    }


def _check_attr(*paths: str) -> dict[str, str]:
    """Resolve the ``merge`` attribute for *paths* via ``git check-attr``.

    Read-only: ``check-attr`` never touches the index or working tree. Returns
    the resolved value per path (``"unspecified"`` when no pattern matches).
    """
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("git unavailable or not a git checkout")
    completed = subprocess.run(
        ["git", "check-attr", "-z", "merge", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    fields = completed.stdout.split("\0")
    # ``-z`` emits NUL-terminated <path> <attribute> <value> triples.
    return {
        fields[index]: fields[index + 2]
        for index in range(0, len(fields) - 2, 3)
    }


# ── Textual contract (works without a git binary) ────────────────────────


def test_expected_append_only_patterns_declare_union():
    declared = _patterns_with_attribute("merge=union")
    missing = EXPECTED_UNION_PATTERNS - declared
    assert not missing, (
        ".gitattributes must declare `merge=union` for these append-only "
        f"report patterns: {sorted(missing)}. Without it a concurrent run's "
        "appended lines are dropped by the push ladder's `-X theirs` rung."
    )


def test_union_coverage_is_not_widened():
    declared = _patterns_with_attribute("merge=union")
    unexpected = declared - EXPECTED_UNION_PATTERNS
    assert not unexpected, (
        f"`merge=union` was added for {sorted(unexpected)}. Union concatenates "
        "conflicting hunks, so it is only safe for files whose writers append. "
        "Verify the writer opens the file in mode 'a' (or only appends rows at "
        "EOF) and add the pattern to EXPECTED_UNION_PATTERNS with the "
        "writer's module path, or drop the attribute."
    )


def test_lfs_db_patterns_keep_the_lfs_merge_driver():
    parsed = _parse_gitattributes()
    for path in LFS_DB_PATHS:
        attributes = parsed.get(path)
        assert attributes is not None, f"{path}: lost its .gitattributes entry"
        assert "merge=lfs" in attributes, f"{path}: must keep merge=lfs"
        assert "merge=union" not in attributes, (
            f"{path}: is a git-LFS binary — union would concatenate two "
            "pointer files / two binaries"
        )


# ── Resolution contract (real gitattributes pattern matching) ────────────


@pytest.mark.parametrize("path", UNION_PATHS)
def test_append_only_paths_resolve_to_union(path):
    assert _check_attr(path)[path] == "union", (
        f"{path}: must resolve to merge=union so a plain `git rebase` keeps "
        "both concurrent runs' appended lines"
    )


@pytest.mark.parametrize("path", NON_UNION_PATHS)
def test_rewritten_reports_do_not_resolve_to_union(path):
    assert _check_attr(path)[path] != "union", (
        f"{path}: is rewritten as a whole document (or is per-run unique) — "
        "union would concatenate two rewrites instead of resolving them"
    )


@pytest.mark.parametrize("path", LFS_DB_PATHS)
def test_lfs_db_paths_resolve_to_lfs_not_union(path):
    assert _check_attr(path)[path] == "lfs", (
        f"{path}: must still resolve to merge=lfs — a later glob must never "
        "capture the LFS binaries into the union driver"
    )
