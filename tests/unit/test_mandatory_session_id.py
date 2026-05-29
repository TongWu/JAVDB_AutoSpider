"""ADR-032 Phase 1: ``session_id`` is mandatory on session-tagged writes.

These DB write helpers used to default ``session_id`` to a sentinel and
silently fall back to the process-global active session id. A caller that
forgot to pass ``session_id`` would write an *untagged, unrollbackable*
row. Phase 1 removes that fallback by making ``session_id`` a required
keyword-only parameter, so an omission now raises ``TypeError`` *before*
any row is written rather than producing an untagged row.

This module pins that invariant for every affected function:

1. ``session_id`` is keyword-only with no default (structural check).
2. Calling the function without ``session_id`` raises ``TypeError``
   naming the missing argument (behavioural check) — the TypeError is
   raised at call binding time, before the body runs, so no database is
   touched.
"""

import inspect

import pytest

import javdb.storage.db as db


# (name, positional args that satisfy every *other* required parameter)
# The args only need to be enough that the sole remaining missing required
# argument is ``session_id``. They are never used because binding fails first.
_CASES = [
    ("db_replace_rclone_inventory", ([],)),
    ("db_swap_rclone_inventory", ()),
    ("db_append_pikpak_history", ({},)),
    ("db_append_dedup_record", ({},)),
    ("db_mark_records_deleted", ([],)),
    ("db_mark_orphan_records", ([], "reason", "2026-01-01")),
    ("db_open_rclone_staging", ()),
    ("db_append_rclone_staging", ([],)),
    ("db_merge_rclone_inventory_from_stage", ()),
    ("db_upsert_align_no_exact_match", ("ABC-001",)),
    ("db_batch_update_last_visited", ([],)),
    ("db_batch_update_movie_actors", ([],)),
]

_NAMES = [name for name, _ in _CASES]


@pytest.mark.parametrize("name", _NAMES)
def test_session_id_is_keyword_only_without_default(name):
    """``session_id`` must be keyword-only with no default — the sentinel
    fallback is gone, so the parameter can never silently resolve to the
    process-global active session id."""
    fn = getattr(db, name)
    sig = inspect.signature(fn)
    assert "session_id" in sig.parameters, (
        f"{name} must expose a session_id parameter"
    )
    param = sig.parameters["session_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{name}.session_id must be keyword-only, got {param.kind.name}"
    )
    assert param.default is inspect.Parameter.empty, (
        f"{name}.session_id must have no default (no sentinel fallback)"
    )


@pytest.mark.parametrize("name,args", _CASES)
def test_omitting_session_id_raises_type_error(name, args):
    """Omitting ``session_id`` raises ``TypeError`` at call binding time —
    no untagged row is ever written."""
    fn = getattr(db, name)
    with pytest.raises(TypeError) as exc:
        fn(*args)
    assert "session_id" in str(exc.value), (
        f"{name} TypeError should name the missing session_id argument: "
        f"{exc.value}"
    )


# Review hardening: making ``session_id`` *required* stops accidental omission,
# but an explicit ``session_id=None`` under pending mode (the only supported
# mode) would still bypass PendingMovieHistoryWrites staging and write an
# unrollbackable, untagged live row — the very hazard ADR-032 removes. The two
# history-batch helpers reject that combination with ``ValueError`` raised
# before any DB access (non-empty payload so the guard, not the empty-input
# early-return, is exercised).
_PENDING_NONE_CASES = [
    ("db_batch_update_last_visited", (["/v/ABC-123"],)),
    (
        "db_batch_update_movie_actors",
        ([("/v/ABC-123", "Actor", "female", "/actors/x", "[]")],),
    ),
]


@pytest.mark.parametrize("name,args", _PENDING_NONE_CASES)
def test_explicit_none_session_id_raises_value_error_in_pending_mode(name, args):
    """``session_id=None`` under pending mode raises ``ValueError`` before any
    row is written — a None session must not bypass staging into a live row."""
    assert db.get_active_write_mode() == "pending"
    fn = getattr(db, name)
    with pytest.raises(ValueError) as exc:
        fn(*args, session_id=None)
    assert "session_id" in str(exc.value), (
        f"{name} ValueError should name the session_id requirement: "
        f"{exc.value}"
    )
