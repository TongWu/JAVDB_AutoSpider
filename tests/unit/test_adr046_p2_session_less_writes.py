"""ADR-046 Phase 2 review-fix regression: nullable-session Operations writes
must succeed (and persist with SessionId NULL) when NO session is active.

Background — the original Phase 2 routed every session-tagging OperationsRepo
write through a *raising* ``_require_session``. But ``DedupRecords.SessionId``
and ``PikpakHistory.SessionId`` are NULLABLE — a session-less write is a
deliberate, valid state for standalone jobs:

  * WeeklyDedup (``apps.cli.rclone.manager --report``/``--validate``) runs the
    dedup self-heal (``mark_orphan_records``) without ever setting an active
    session → the raise produced an uncaught RuntimeError.
  * Ad-hoc PikPak (``apps.cli.pikpak.bridge --days N``) calls
    ``append_pikpak_history`` with no session → the raise was swallowed by a
    ``try/except``, silently dropping every history row.

These tests use a REAL ``OperationsRepo`` against the autouse temp DB
(``_isolate_sqlite``) — no mocks — so a reintroduced raise would fail here.
"""
import pytest

from javdb.storage.repos.operations_repo import OperationsRepo


@pytest.fixture(autouse=True)
def _no_active_session():
    """Every test here runs with NO active session (standalone-job shape)."""
    from javdb.storage.db import set_active_session_id

    set_active_session_id(None)
    yield
    set_active_session_id(None)


def test_mark_orphan_records_session_less_persists_null_session():
    """WeeklyDedup self-heal path: marking a dedup orphan with no active
    session must NOT raise and must land with SessionId NULL."""
    # Imported inside the test so the autouse ``_isolate_sqlite`` fixture's
    # temp-DB path patch is in effect (a module-level import would bind the
    # pre-fixture production path and read the wrong DB).
    from javdb.storage.db import (
        db_append_dedup_record, get_db, OPERATIONS_DB_PATH,
    )

    db_append_dedup_record(
        {
            'VideoCode': 'ORPHAN',
            'ExistingGdrivePath': '2025/Actor/ORPHAN/有码-中字',
            'DeletionReason': 'Subtitle upgrade',
            'IsDeleted': 0,
        },
        session_id=None,
    )

    # No bound session on the repo, no explicit arg → session-less write.
    updated = OperationsRepo().mark_orphan_records(
        ['2025/Actor/ORPHAN/有码-中字'], 'stale-orphan', '2026-01-01 00:00:00',
    )
    assert updated == 1

    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT IsDeleted, SessionId FROM DedupRecords "
            "WHERE ExistingGdrivePath = ?",
            ('2025/Actor/ORPHAN/有码-中字',),
        ).fetchone()
    assert row is not None
    assert int(row['IsDeleted']) == 1
    assert row['SessionId'] is None


def test_append_pikpak_history_session_less_persists_null_session():
    """Ad-hoc PikPak path: appending a history row with no active session must
    NOT raise and must land with SessionId NULL (previously silently dropped)."""
    from javdb.storage.db import get_db, OPERATIONS_DB_PATH

    record = {
        'torrent_hash': 'abc123',
        'torrent_name': 'Some.Torrent',
        'category': 'Uncensored',
        'magnet_uri': 'magnet:?xt=urn:btih:abc123',
        'added_to_qb_date': '2026-01-01 00:00:00',
        'deleted_from_qb_date': '',
        'uploaded_to_pikpak_date': '2026-01-01 00:00:00',
        'transfer_status': 'success',
        'error_message': '',
    }

    row_id = OperationsRepo().append_pikpak_history(record)
    assert row_id is not None

    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT TorrentHash, SessionId FROM PikpakHistory WHERE Id = ?",
            (row_id,),
        ).fetchone()
    assert row is not None
    assert row['TorrentHash'] == 'abc123'
    assert row['SessionId'] is None


def test_append_dedup_record_session_less_persists_null_session():
    """The dedup-record batch in WeeklyDedup ``_persist_dedup_records`` runs
    session-less too; a session-less append must persist with SessionId NULL."""
    from javdb.storage.db import get_db, OPERATIONS_DB_PATH

    row_id = OperationsRepo().append_dedup_record(
        {
            'VideoCode': 'NEW-001',
            'ExistingGdrivePath': '2025/Actor/NEW-001/有码-中字',
            'DeletionReason': 'Subtitle upgrade',
            'IsDeleted': 0,
        },
    )
    assert row_id is not None and row_id > 0

    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT VideoCode, SessionId FROM DedupRecords WHERE Id = ?",
            (row_id,),
        ).fetchone()
    assert row is not None
    assert row['VideoCode'] == 'NEW-001'
    assert row['SessionId'] is None


def test_bound_session_still_tags_rows():
    """A constructor-bound session is still honoured (the pipeline shape):
    the row lands tagged with that session id."""
    from javdb.storage.db import get_db, OPERATIONS_DB_PATH

    sid = '20260603T000000.000000Z-bind-0001'
    row_id = OperationsRepo(session_id=sid).append_dedup_record(
        {
            'VideoCode': 'BOUND-001',
            'ExistingGdrivePath': '2025/Actor/BOUND-001/有码-中字',
            'DeletionReason': 'Subtitle upgrade',
            'IsDeleted': 0,
        },
    )
    assert row_id is not None and row_id > 0

    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT SessionId FROM DedupRecords WHERE Id = ?",
            (row_id,),
        ).fetchone()
    assert row is not None
    assert row['SessionId'] == sid
