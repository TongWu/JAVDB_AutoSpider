"""Contract test: every ``lastrowid`` read in the tree is classified.

BFR-034's last Follow-Up item asks for "a lint rule or a contract test
enumerating ``lastrowid`` call sites [to] end the recurrence rather than
fixing it a fourth time" — the 2026-05-08 SessionId incident, the Batch C
``MovieHistory`` / ``TorrentHistory`` fix and BFR-034 itself are three
instances of one pattern:

    ``get_db()`` routes on ``STORAGE_BACKEND``, so an INSERT can land on a
    ``DualConnection``. ``DualCursor.lastrowid`` surfaces the *SQLite* leg's
    AUTOINCREMENT rowid while D1 allocates its own; once the two counters
    diverge (they do — D1 auto-commits per statement, the mirror is frozen
    under ``d1`` mode, and ``sync_d1_to_sqlite`` only realigns ``max(Id)``),
    reusing that rowid as a foreign key attaches the D1-side child rows to a
    *different* row, and the FK is satisfied so nothing raises.

This test does not judge safety — it pins the *inventory*. Adding a new
``lastrowid`` read (or a new file that reads one) fails here until the author
records the classification below, which is the moment to ask whether the
value is reused across backends.

Classifications (BFR-034 sweep, 2026-08-12):

* **(a) fixed** — cross-backend reachable and the value was reused as an FK /
  read back. ``_db_reports.db_insert_report_rows``,
  ``csv_to_sqlite.migrate_history`` / ``migrate_single_csv``,
  ``test_mode._insert_movie`` / ``_insert_torrent`` and
  ``profile_hot_paths._seed_history`` all mint ``generate_integer_id()``
  values instead and therefore no longer appear in this inventory at all.
* **(b) value unused** — reachable under ``dual``, but the returned id is
  discarded by every in-tree caller and never persisted into another row.
* **(c) single-backend** — provably local: forced sqlite override, a raw
  ``sqlite3`` connection, or a cursor that belongs to D1 itself (so a D1 id
  is only ever used for a D1 row).
* **(d) infrastructure** — the guard machinery that compares the two legs'
  ``lastrowid`` values; these reads are the defence, not a use of the id.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

REPO_ROOT = Path(project_root)
SCANNED_TREES = ("javdb", "apps", "scripts")

# path (repo-relative, POSIX) → (number of lastrowid reads, classification)
EXPECTED_LASTROWID_READS: dict[str, tuple[int, str]] = {
    # (d) DualCursor / _maybe_warn_id_drift read both legs' lastrowid purely to
    # compare them and raise DualWriteIdMismatchError for guarded tables.
    "javdb/storage/dual_connection.py": (7, "infrastructure"),
    # (c) _clone_cursors replays a D1Cursor's own meta.last_row_id on a
    # schema-cache hit — a D1 value copied into a D1 cursor.
    "javdb/storage/d1_port.py": (1, "single-backend (D1's own id)"),
    # (c) reconcile_d1_drift talks to a D1Connection directly (never get_db),
    # so the captured id is D1's and is used as the FK for D1 child rows —
    # which is the whole point of the reconciler (module docstring: "a
    # SQLite-side lastrowid would pollute D1 further").
    "javdb/migrations/tools/reconcile_d1_drift.py": (2, "single-backend (D1's own id)"),
    # (c) _migrate_v5_to_v6's report_rows → ReportMovies/ReportTorrents step.
    # Reached only from init_db, which is a no-op under `d1` and forces the
    # sqlite-only thread-local override under `dual`, so the connection is
    # always a plain sqlite3.Connection.
    "javdb/storage/db/_db_migrations.py": (1, "single-backend (init override)"),
    # (b) SpiderStats / UploaderStats / PikpakStats upserts return the rowid;
    # StatsRepo hands it to run_service and workflow.stats_sink, both of which
    # discard it. Nothing stores or joins on these Ids.
    "javdb/storage/db/_db_stats.py": (3, "value unused"),
    # (b) PikpakHistory: OperationsRepo.append_pikpak_history's caller
    # (pikpak/bridge/service.py) drops the return value. DedupRecords: the id
    # is only compared against the -1 duplicate sentinel in
    # dedup_store.append_dedup_record, which returns a bool.
    "javdb/storage/db/_db_operations.py": (2, "value unused"),
    # (b) PipelineEvent.seq is returned by store.emit(); no caller assigns it.
    # The consumer cursor advances from seqs read back out of the table
    # (read_since), not from this value.
    "javdb/storage/repos/pipeline_event_repo.py": (1, "value unused"),
    # (a-mitigated) ContentFilterRepo.add_rule returns an operator-facing rule
    # id and _canonical_lastrowid deliberately prefers the D1 leg's value,
    # because reads (and later remove_rule / set_enabled) resolve on D1.
    "javdb/storage/repos/content_filter_repo.py": (2, "D1-canonical by design"),
}


class _LastrowidReadVisitor(ast.NodeVisitor):
    """Collect line numbers of ``x.lastrowid`` / ``getattr(x, "lastrowid")`` reads.

    AST-based on purpose: the modules involved discuss ``lastrowid`` at length
    in comments and docstrings, and a textual grep cannot tell those apart
    from a real read. Assignments (``self.lastrowid = ...``) are Store
    context and excluded — they define the attribute rather than trust it.
    """

    def __init__(self) -> None:
        self.lines: list[int] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "lastrowid" and isinstance(node.ctx, ast.Load):
            self.lines.append(node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "lastrowid"
        ):
            self.lines.append(node.lineno)
        self.generic_visit(node)


def _scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for tree_name in SCANNED_TREES:
        for path in sorted((REPO_ROOT / tree_name).rglob("*.py")):
            visitor = _LastrowidReadVisitor()
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            if visitor.lines:
                found[path.relative_to(REPO_ROOT).as_posix()] = visitor.lines
    return found


def test_lastrowid_read_inventory_is_classified():
    """No unclassified ``lastrowid`` read may exist under javdb/ apps/ scripts/."""
    found = _scan()

    unknown = sorted(set(found) - set(EXPECTED_LASTROWID_READS))
    assert not unknown, (
        "New lastrowid read(s) in "
        + ", ".join(f"{p}:{found[p]}" for p in unknown)
        + ". Under STORAGE_BACKEND=dual, cur.lastrowid is the SQLite leg's "
        "rowid only (see BFR-034). If the value is reused as a foreign key, "
        "read back, or returned to a caller that persists it, mint the id "
        "with generate_integer_id() and pass it in the INSERT column list "
        "instead. Otherwise add the file to EXPECTED_LASTROWID_READS with "
        "its classification."
    )

    counts = {path: len(lines) for path, lines in found.items()}
    expected_counts = {
        path: count for path, (count, _reason) in EXPECTED_LASTROWID_READS.items()
    }
    assert counts == expected_counts, (
        "lastrowid read count changed; re-classify the call sites and update "
        f"EXPECTED_LASTROWID_READS. found={counts} expected={expected_counts}"
    )


def test_fixed_call_sites_stay_fixed():
    """The BFR-034 (a)-class writers must never read a rowid back again."""
    found = _scan()
    for path in (
        "javdb/storage/db/_db_reports.py",
        "javdb/migrations/tools/csv_to_sqlite.py",
        "apps/api/routers/test_mode.py",
        "apps/cli/ops/profile_hot_paths.py",
    ):
        assert (REPO_ROOT / path).is_file(), f"{path} moved — update this test"
        assert path not in found, (
            f"{path} reads cur.lastrowid again at lines {found.get(path)}; "
            "it writes a guarded table and must supply Id explicitly "
            "(BFR-034)."
        )
