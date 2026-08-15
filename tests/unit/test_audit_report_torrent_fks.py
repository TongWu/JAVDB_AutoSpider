"""Historical-damage audit for ``ReportTorrents.ReportMovieId`` (BFR-034).

The bug being assessed here wrote a torrent's FK from the *SQLite*
``lastrowid`` while the D1 leg allocated its own id, so on D1 the torrent
attached to a different session's movie (or to a movie that a later
rollback deleted). These tests seed each surviving damage shape into a
real SQLite reports DB — the autouse ``_isolate_sqlite`` fixture points
``REPORTS_DB_PATH`` at a fresh file with the production schema — and pin
what the audit must find, what ``--apply`` may repair, and what it must
never touch.

The load-bearing invariant here is a *negative* one: **no FK is ever
rewritten by this tool.** Re-attaching a mismatch to the single surviving
movie with the same ``VideoCode`` looks safe and is not — ``ReportTorrents``
has no ``SessionId`` and no timestamp, and the mis-pointed row survives the
rollback that deleted its true parent, so the lone candidate is often a
*later* re-scrape. Several tests below exist purely so that re-enabling the
auto-re-attachment fails the suite.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from apps.cli.db import audit_report_torrent_fks as audit_cli
from javdb.storage import db as _db
from javdb.storage.db import get_db


# ── seeding helpers ─────────────────────────────────────────────────────


def _torrent(tid, movie_id, code, *, sub=1, cen=1, magnet=None):
    """Build a ReportTorrents seed tuple.

    ``(sub, cen)`` is the category slot every writer fills at most once
    per movie; distinct magnets by default so the audit's
    "candidate already has this magnet" branch is not tripped by accident.
    """
    return (
        tid, movie_id, code,
        magnet or f"magnet:?xt=urn:btih:{code}-{sub}{cen}-{tid}",
        sub, cen,
    )


def _seed(db_path, *, sessions=(), movies=(), torrents=()):
    """Insert rows on a dedicated connection with FK enforcement off.

    Orphan torrents (the whole point of one signal) cannot be inserted
    through ``get_db``, whose connection sets ``PRAGMA foreign_keys=ON``.
    A separate raw connection defaults to OFF, so the damaged states are
    reproducible without weakening the connection the audit itself uses.
    """
    conn = sqlite3.connect(db_path)
    try:
        for sid in sessions:
            conn.execute(
                "INSERT INTO ReportSessions "
                "(Id, ReportType, ReportDate, CsvFilename, DateTimeCreated) "
                "VALUES (?, 'daily', '2026-08-01', ?, ?)",
                (sid, f"{sid}.csv", "2026-08-01T00:00:00Z"),
            )
        for movie_id, session_id, code in movies:
            conn.execute(
                "INSERT INTO ReportMovies (Id, SessionId, Href, VideoCode) "
                "VALUES (?, ?, ?, ?)",
                (movie_id, session_id, f"https://javdb.com/v/{code}", code),
            )
        for row in torrents:
            conn.execute(
                "INSERT INTO ReportTorrents (Id, ReportMovieId, VideoCode, "
                "MagnetUri, SubtitleIndicator, CensorIndicator) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )
        conn.commit()
    finally:
        conn.close()


def _run_audit(**kwargs) -> dict:
    with get_db(_db.REPORTS_DB_PATH) as conn:
        return audit_cli.audit(conn, **kwargs)


def _fk_of(torrent_id):
    conn = sqlite3.connect(_db.REPORTS_DB_PATH)
    try:
        row = conn.execute(
            "SELECT ReportMovieId FROM ReportTorrents WHERE Id=?",
            (torrent_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def _torrent_ids():
    conn = sqlite3.connect(_db.REPORTS_DB_PATH)
    try:
        return {r[0] for r in conn.execute("SELECT Id FROM ReportTorrents")}
    finally:
        conn.close()


def _counts(result):
    return {
        name: result["signals"][name]["count"]
        for name in (
            audit_cli.SIGNAL_ORPHAN,
            audit_cli.SIGNAL_CODE_MISMATCH,
            audit_cli.SIGNAL_DUPLICATE_SLOT,
        )
    }


# ── seeds ───────────────────────────────────────────────────────────────


def _seed_clean(db_path):
    """Two sessions, each movie owning its own torrents. No damage."""
    _seed(
        db_path,
        sessions=("S1", "S2"),
        movies=((101, "S1", "ABC-001"), (102, "S2", "ABC-002")),
        torrents=(
            _torrent(1, 101, "ABC-001", sub=1, cen=1),
            _torrent(2, 101, "ABC-001", sub=0, cen=1),
            _torrent(3, 102, "ABC-002", sub=1, cen=1),
        ),
    )


def _seed_single_candidate_mismatch(db_path):
    """S2's torrent hangs off S1's movie; S2's own movie is childless.

    Childless is the shape the bug leaves behind: on the damaged backend
    the newly inserted movie never received its children, because the FK
    resolved to an older row. It is also the most *tempting* shape — one
    candidate parent, free slot — and still only a suggestion, because
    nothing here distinguishes "S2's robbed movie" from "a later
    re-scrape of the same VideoCode after S2's parent was rolled back".
    """
    _seed(
        db_path,
        sessions=("S1", "S2"),
        movies=((101, "S1", "ABC-001"), (102, "S2", "ABC-002")),
        torrents=(
            _torrent(1, 101, "ABC-001", sub=1, cen=1),
            _torrent(2, 101, "ABC-002", sub=0, cen=1),  # mis-pointed
        ),
    )


# ── clean baseline ──────────────────────────────────────────────────────


def test_clean_db_reports_clean(_isolate_sqlite):
    _seed_clean(_isolate_sqlite)

    result = _run_audit()

    assert _counts(result) == {
        audit_cli.SIGNAL_ORPHAN: 0,
        audit_cli.SIGNAL_CODE_MISMATCH: 0,
        audit_cli.SIGNAL_DUPLICATE_SLOT: 0,
    }
    assert result["verdict"] == audit_cli.VERDICT_CLEAN
    assert result["repair_plan"] == {"delete_orphans": []}
    assert result["manual_repair_suggestions"] == []
    assert result["affected_session_ids"] == []
    assert audit_cli.main([]) == 0


def test_empty_reports_db_is_clean(_isolate_sqlite):
    assert audit_cli.main([]) == 0


# ── signal: ORPHANED_TORRENT (deterministic) ────────────────────────────


def test_orphan_is_detected_and_only_offered_for_deletion(_isolate_sqlite):
    _seed(
        _isolate_sqlite,
        sessions=("S1",),
        movies=((101, "S1", "ABC-001"),),
        torrents=(
            _torrent(1, 101, "ABC-001"),
            _torrent(2, 999, "ABC-002"),  # parent movie does not exist
        ),
    )

    result = _run_audit()

    assert _counts(result)[audit_cli.SIGNAL_ORPHAN] == 1
    assert _counts(result)[audit_cli.SIGNAL_CODE_MISMATCH] == 0
    assert _counts(result)[audit_cli.SIGNAL_DUPLICATE_SLOT] == 0
    assert result["signals"][audit_cli.SIGNAL_ORPHAN]["confidence"] == (
        "deterministic"
    )
    assert result["repair_plan"]["delete_orphans"] == [2]
    # Deletion is irreversible, so an orphan never promises "--apply fixes it".
    assert result["verdict"] == audit_cli.VERDICT_ESCALATE
    assert audit_cli.main([]) == 2
    assert _torrent_ids() == {1, 2}


def test_orphan_deletion_requires_delete_orphans_flag(_isolate_sqlite):
    _seed(
        _isolate_sqlite,
        sessions=("S1",),
        movies=((101, "S1", "ABC-001"),),
        torrents=(_torrent(1, 101, "ABC-001"), _torrent(2, 999, "ABC-002")),
    )

    assert audit_cli.main(["--apply", "--force"]) == 2
    assert _torrent_ids() == {1, 2}

    assert audit_cli.main(["--apply", "--force", "--delete-orphans"]) == 0
    assert _torrent_ids() == {1}
    assert _run_audit()["verdict"] == audit_cli.VERDICT_CLEAN


# ── signal: VIDEO_CODE_MISMATCH (deterministic) ─────────────────────────


def test_business_key_mismatch_is_detected_with_both_sessions(_isolate_sqlite):
    _seed_single_candidate_mismatch(_isolate_sqlite)

    result = _run_audit()
    mismatch = result["signals"][audit_cli.SIGNAL_CODE_MISMATCH]

    assert _counts(result) == {
        audit_cli.SIGNAL_ORPHAN: 0,
        audit_cli.SIGNAL_CODE_MISMATCH: 1,
        audit_cli.SIGNAL_DUPLICATE_SLOT: 0,
    }
    assert mismatch["confidence"] == "deterministic"
    # Deterministic *detection*, report-only repair: the lone candidate is
    # suggested, counted for manual review, and never applied.
    assert mismatch["ambiguous"] == 1
    assert mismatch["ambiguous_by_reason"] == {
        audit_cli.REASON_SINGLE_CANDIDATE: 1,
    }
    assert mismatch["suggested_manual_repairs"] == 1
    assert mismatch["samples"][0]["TorrentId"] == 2
    assert mismatch["samples"][0]["AttachedSessionId"] == "S1"
    # Both the mis-attributed session and the robbed one are named.
    assert result["affected_session_ids"] == ["S1", "S2"]
    assert result["manual_repair_suggestions"] == [{
        "torrent_id": 2,
        "video_code": "ABC-002",
        "from_movie_id": 101,
        "from_session_id": "S1",
        "to_movie_id": 102,
        "to_session_id": "S2",
        "reason": audit_cli.REASON_SINGLE_CANDIDATE,
        "caveat": audit_cli.MANUAL_CORROBORATION_NOTE,
        "sql": "UPDATE ReportTorrents SET ReportMovieId = 102 WHERE Id = 2;",
    }]
    # The executable plan holds no re-attachment at all.
    assert result["repair_plan"] == {"delete_orphans": []}
    assert result["verdict"] == audit_cli.VERDICT_ESCALATE
    assert audit_cli.main([]) == 2


def test_report_only_default_never_writes(_isolate_sqlite):
    _seed_single_candidate_mismatch(_isolate_sqlite)

    assert audit_cli.main([]) == 2

    assert _fk_of(2) == 101
    assert _torrent_ids() == {1, 2}


def test_apply_never_reattaches_a_single_candidate_mismatch(_isolate_sqlite):
    """The whole point: --apply --force must not touch the FK.

    "Exactly one movie carries this VideoCode today" cannot prove that movie
    is the torrent's original parent — ReportTorrents has no SessionId and no
    timestamp — and the damage pattern makes the wrong answer common: the
    mis-pointed row escaped the rollback that deleted its true parent, so the
    survivor may be a later re-scrape. Re-enabling the auto-repair fails here.
    """
    _seed_single_candidate_mismatch(_isolate_sqlite)

    assert audit_cli.main(["--apply", "--force", "--delete-orphans"]) == 2

    assert _fk_of(2) == 101, "no FK may be rewritten by this tool"
    assert _torrent_ids() == {1, 2}
    after = _run_audit()
    assert after["signals"][audit_cli.SIGNAL_CODE_MISMATCH]["count"] == 1
    assert after["verdict"] == audit_cli.VERDICT_ESCALATE


def test_apply_repairs_ignores_a_reattachment_plan(_isolate_sqlite):
    """``apply_repairs`` must have no re-attachment path to re-awaken.

    Handed a plan that *does* carry a ``reattach`` list (the shape the
    retired code consumed), it must still write nothing and report nothing.
    """
    _seed_single_candidate_mismatch(_isolate_sqlite)

    with get_db(_db.REPORTS_DB_PATH) as conn:
        applied = audit_cli.apply_repairs(
            conn,
            {
                "reattach": [{"torrent_id": 2, "to_movie_id": 102}],
                "delete_orphans": [],
            },
            delete_orphans=True,
        )

    assert applied == {"orphans_deleted": 0, "orphans_left": 0}
    assert "reattached" not in applied
    assert _fk_of(2) == 101


def test_exit_code_one_has_no_producer():
    """Exit 1 ("findings, all auto-repairable") was retired, not reused.

    Nothing may map to 1 again without a deliberate contract change: the
    class only existed for the auto-re-attachment this tool no longer does.
    """
    assert set(audit_cli.VERDICT_EXIT_CODE) == {
        audit_cli.VERDICT_CLEAN,
        audit_cli.VERDICT_ESCALATE,
    }
    assert sorted(audit_cli.VERDICT_EXIT_CODE.values()) == [0, 2]
    assert audit_cli.EXIT_DB_ERROR == 3


def test_apply_requires_force(_isolate_sqlite):
    _seed_single_candidate_mismatch(_isolate_sqlite)

    with pytest.raises(SystemExit) as excinfo:
        audit_cli.main(["--apply"])

    assert excinfo.value.code == 2
    assert _fk_of(2) == 101


def test_max_repairs_rail_refuses_a_larger_orphan_plan(_isolate_sqlite):
    """The rail now guards the only executable repair: orphan DELETEs."""
    _seed(
        _isolate_sqlite,
        sessions=("S1",),
        movies=((101, "S1", "ABC-001"),),
        torrents=(
            _torrent(1, 101, "ABC-001", sub=1, cen=1),
            _torrent(2, 998, "ABC-002", sub=0, cen=1),
            _torrent(3, 999, "ABC-003", sub=0, cen=0),
        ),
    )

    assert _run_audit()["repair_plan"]["delete_orphans"] == [2, 3]
    assert audit_cli.main([
        "--apply", "--force", "--delete-orphans", "--max-repairs", "1",
    ]) == 2
    assert _torrent_ids() == {1, 2, 3}


def test_max_repairs_refusal_emits_json_with_distinct_reason(
    _isolate_sqlite, capsys, monkeypatch,
):
    """--json --apply must yield a payload on the rail path, marked as a
    safety-rail refusal so consumers can tell it from a plain ESCALATE."""
    monkeypatch.setattr(audit_cli, "setup_logging", lambda **kwargs: None)
    _seed(
        _isolate_sqlite,
        sessions=("S1",),
        movies=((101, "S1", "ABC-001"),),
        torrents=(
            _torrent(1, 101, "ABC-001", sub=1, cen=1),
            _torrent(2, 998, "ABC-002", sub=0, cen=1),
            _torrent(3, 999, "ABC-003", sub=0, cen=0),
        ),
    )

    assert audit_cli.main([
        "--apply", "--force", "--delete-orphans", "--max-repairs", "1",
        "--json",
    ]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["refused"] == {
        "reason": "max_repairs_exceeded",
        "planned": 2,
        "max_repairs": 1,
    }
    assert _torrent_ids() == {1, 2, 3}


def test_suggested_update_statement_is_printed_for_manual_review(
    _isolate_sqlite, caplog, monkeypatch,
):
    """The printed SQL is the only escape hatch, so it must be printed."""
    monkeypatch.setattr(audit_cli, "setup_logging", lambda **kwargs: None)
    _seed_single_candidate_mismatch(_isolate_sqlite)
    caplog.set_level(logging.WARNING)

    assert audit_cli.main([]) == 2

    text = caplog.text
    assert "UPDATE ReportTorrents SET ReportMovieId = 102 WHERE Id = 2;" in (
        text
    )
    assert audit_cli.REASON_SINGLE_CANDIDATE in text
    # Labelled as needing out-of-band corroboration, with the source named.
    assert "NOT " in text and "auto-repaired" in text
    assert "DailyReport CSVs in git history" in text


# ── every mismatch: reported, never repaired ────────────────────────────


@pytest.mark.parametrize(
    "movies,torrents,expected_reason",
    [
        pytest.param(
            ((101, "S1", "ABC-001"), (102, "S2", "ABC-002")),
            (
                _torrent(1, 101, "ABC-001", sub=1, cen=1),
                _torrent(2, 101, "ABC-002", sub=0, cen=1),
            ),
            audit_cli.REASON_SINGLE_CANDIDATE,
            id="lone_candidate_cannot_prove_provenance",
        ),
        pytest.param(
            (
                (101, "S1", "ABC-001"),
                (102, "S2", "ABC-002"),
                (103, "S3", "ABC-002"),
            ),
            (
                _torrent(1, 101, "ABC-001", sub=1, cen=1),
                _torrent(2, 101, "ABC-002", sub=0, cen=1),
            ),
            audit_cli.REASON_MULTIPLE_CANDIDATES,
            id="two_movies_share_the_video_code",
        ),
        pytest.param(
            ((101, "S1", "ABC-001"),),
            (
                _torrent(1, 101, "ABC-001", sub=1, cen=1),
                _torrent(2, 101, "ZZZ-999", sub=0, cen=1),
            ),
            audit_cli.REASON_NO_CANDIDATE,
            id="intended_parent_no_longer_exists",
        ),
        pytest.param(
            ((101, "S1", "ABC-001"), (102, "S2", "ABC-002")),
            (
                _torrent(1, 101, "ABC-001", sub=1, cen=1),
                _torrent(2, 101, "ABC-002", sub=0, cen=1),
                _torrent(3, 102, "ABC-002", sub=0, cen=1),
            ),
            audit_cli.REASON_SLOT_OCCUPIED,
            id="candidate_already_filled_that_category",
        ),
        pytest.param(
            ((101, "S1", "ABC-001"), (102, "S2", "ABC-002")),
            (
                _torrent(1, 101, "ABC-001", sub=1, cen=1),
                _torrent(2, 101, "ABC-002", sub=0, cen=1, magnet="magnet:?dup"),
                _torrent(3, 102, "ABC-002", sub=1, cen=0, magnet="magnet:?dup"),
            ),
            audit_cli.REASON_MAGNET_PRESENT,
            id="candidate_already_holds_that_magnet",
        ),
    ],
)
def test_mismatch_is_reported_but_untouched(
    _isolate_sqlite, movies, torrents, expected_reason,
):
    """Every reason, including the lone-candidate one, is report-only."""
    _seed(
        _isolate_sqlite,
        sessions=("S1", "S2", "S3"),
        movies=movies,
        torrents=torrents,
    )
    suggested = int(expected_reason == audit_cli.REASON_SINGLE_CANDIDATE)

    result = _run_audit()
    mismatch = result["signals"][audit_cli.SIGNAL_CODE_MISMATCH]

    assert mismatch["count"] == 1
    assert mismatch["ambiguous_by_reason"] == {expected_reason: 1}
    assert mismatch["suggested_manual_repairs"] == suggested
    assert len(result["manual_repair_suggestions"]) == suggested
    # No reason produces an executable re-attachment.
    assert "reattach" not in result["repair_plan"]
    assert result["verdict"] == audit_cli.VERDICT_ESCALATE

    # --apply has nothing safe to do, and must leave the row alone.
    assert audit_cli.main(["--apply", "--force"]) == 2
    assert _fk_of(2) == 101
    assert _torrent_ids() == {t[0] for t in torrents}


# ── signal: UNEXPLAINED_DUPLICATE_SLOT (heuristic) ──────────────────────


def test_duplicate_slot_catches_damage_the_business_key_cannot_see(
    _isolate_sqlite,
):
    """Same video code in two sessions: the codes match, the shape does not."""
    _seed(
        _isolate_sqlite,
        sessions=("S1", "S2"),
        movies=((101, "S1", "DUP-001"), (102, "S2", "DUP-001")),
        torrents=(
            _torrent(1, 101, "DUP-001", sub=1, cen=1),
            _torrent(2, 101, "DUP-001", sub=1, cen=1),  # S2's row, mis-pointed
        ),
    )

    result = _run_audit()
    duplicate = result["signals"][audit_cli.SIGNAL_DUPLICATE_SLOT]

    assert _counts(result)[audit_cli.SIGNAL_CODE_MISMATCH] == 0
    assert duplicate["count"] == 1
    assert duplicate["confidence"] == "heuristic"
    assert duplicate["samples"][0]["AttachedMovieId"] == 101
    assert duplicate["samples"][0]["AttachedSessionId"] == "S1"
    assert sorted(duplicate["samples"][0]["UnexplainedTorrentIds"]) == [1, 2]
    assert result["manual_repair_suggestions"] == []
    assert result["verdict"] == audit_cli.VERDICT_ESCALATE

    # Heuristic evidence names no single foreign row: never auto-repaired.
    assert audit_cli.main(["--apply", "--force", "--delete-orphans"]) == 2
    assert _torrent_ids() == {1, 2}
    assert (_fk_of(1), _fk_of(2)) == (101, 101)


def test_duplicate_slot_explained_by_a_mismatch_is_not_double_counted(
    _isolate_sqlite,
):
    _seed(
        _isolate_sqlite,
        sessions=("S1", "S2"),
        movies=((101, "S1", "ABC-001"), (102, "S2", "ABC-002")),
        torrents=(
            _torrent(1, 101, "ABC-001", sub=1, cen=1),
            _torrent(2, 101, "ABC-002", sub=1, cen=1),  # same slot as #1
        ),
    )

    result = _run_audit()

    assert _counts(result) == {
        audit_cli.SIGNAL_ORPHAN: 0,
        audit_cli.SIGNAL_CODE_MISMATCH: 1,
        audit_cli.SIGNAL_DUPLICATE_SLOT: 0,
    }
    assert result["signals"][audit_cli.SIGNAL_DUPLICATE_SLOT][
        "duplicate_groups_total"
    ] == 1
    assert result["verdict"] == audit_cli.VERDICT_ESCALATE

    # The mismatch accounts for the duplicate group, but accounting for it is
    # not authority to move it.
    assert audit_cli.main(["--apply", "--force"]) == 2
    assert _fk_of(2) == 101


# ── unverifiable rows ───────────────────────────────────────────────────


def test_missing_video_code_is_counted_not_flagged(_isolate_sqlite):
    _seed(
        _isolate_sqlite,
        sessions=("S1",),
        movies=((101, "S1", "ABC-001"), (102, "S1", ""),),
        torrents=(
            (1, 101, None, "magnet:?a", 1, 1),
            (2, 102, "ABC-002", "magnet:?b", 1, 1),
        ),
    )

    result = _run_audit()

    assert _counts(result)[audit_cli.SIGNAL_CODE_MISMATCH] == 0
    assert result["unverifiable"][
        "torrents_without_comparable_video_code"
    ] == 2
    assert result["verdict"] == audit_cli.VERDICT_CLEAN
    assert audit_cli.main([]) == 0


# ── CLI surface ─────────────────────────────────────────────────────────


def test_counts_stay_exact_when_the_detail_fetch_truncates(_isolate_sqlite):
    """Operators need the true blast radius even when the plan is capped."""
    _seed(
        _isolate_sqlite,
        sessions=("S1", "S2"),
        movies=(
            (101, "S1", "ABC-001"),
            (102, "S2", "ABC-002"),
            (103, "S2", "ABC-003"),
        ),
        torrents=(
            _torrent(1, 101, "ABC-001", sub=1, cen=1),
            _torrent(2, 101, "ABC-002", sub=0, cen=1),
            _torrent(3, 101, "ABC-003", sub=0, cen=0),
        ),
    )

    result = _run_audit(max_findings=1)
    mismatch = result["signals"][audit_cli.SIGNAL_CODE_MISMATCH]

    assert mismatch["count"] == 2
    assert mismatch["truncated"] is True
    assert len(result["manual_repair_suggestions"]) == 1


def test_json_output_carries_signals_suggestions_and_verdict(
    _isolate_sqlite, capsys,
):
    _seed_single_candidate_mismatch(_isolate_sqlite)

    assert audit_cli.main(["--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "report_torrent_fk_audit"
    assert payload["verdict"] == audit_cli.VERDICT_ESCALATE
    assert set(payload["signals"]) == {
        audit_cli.SIGNAL_ORPHAN,
        audit_cli.SIGNAL_CODE_MISMATCH,
        audit_cli.SIGNAL_DUPLICATE_SLOT,
    }
    suggestion = payload["manual_repair_suggestions"][0]
    assert suggestion["to_movie_id"] == 102
    assert suggestion["reason"] == audit_cli.REASON_SINGLE_CANDIDATE
    # The caveat travels with the machine-readable output too.
    assert "corroboration" in suggestion["caveat"]
    assert suggestion["sql"].startswith("UPDATE ReportTorrents SET")
    assert payload["repair_plan"] == {"delete_orphans": []}
    assert payload["affected_session_ids"] == ["S1", "S2"]


def test_unreadable_reports_db_exits_three(_isolate_sqlite, tmp_path, monkeypatch):
    """A reports DB without the schema is an operator error, not a finding."""
    empty = tmp_path / "no_schema.db"
    empty.write_bytes(b"")
    monkeypatch.setattr(_db, "REPORTS_DB_PATH", str(empty))

    assert audit_cli.main([]) == audit_cli.EXIT_DB_ERROR
