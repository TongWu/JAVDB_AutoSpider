"""ADR-046 Phase 5 regression guard: the ambient session-id global is retired.

``get_active_session_id`` / ``set_active_session_id`` (plus the
``_active_session_id_value`` module global and the ``_SESSION_ID_SENTINEL`` /
``_resolve_session_id`` resolver) were deleted from
``javdb.storage.db._db_session`` and its package re-export. Session identity is
now threaded explicitly (ADR-046 D2). These assertions fail loudly if any of
that machinery is reintroduced.

What STAYS (run identity, write mode, the id generators, and the shared lock)
is asserted importable so an over-eager future deletion doesn't take them out.
"""
from __future__ import annotations

import inspect

import pytest

_RETIRED = ("get_active_session_id", "set_active_session_id")


def test_session_id_accessors_not_on_package():
    import javdb.storage.db as db

    for name in _RETIRED:
        assert not hasattr(db, name), f"{name} should be retired from javdb.storage.db"
        assert name not in getattr(db, "__all__", ()), f"{name} still in __all__"


@pytest.mark.parametrize("name", _RETIRED)
def test_direct_import_of_retired_accessor_raises(name):
    # A real ``from javdb.storage.db import <name>`` must now raise ImportError.
    with pytest.raises(ImportError):
        exec(f"from javdb.storage.db import {name}", {})


def test_session_id_machinery_gone_from_db_session_source():
    from javdb.storage.db import _db_session

    src = inspect.getsource(_db_session)
    for token in (
        "def set_active_session_id",
        "def get_active_session_id",
        "_active_session_id_value",
        "_SESSION_ID_SENTINEL",
        "def _resolve_session_id",
    ):
        assert token not in src, f"{token!r} should be deleted from _db_session.py"


def test_kept_machinery_still_importable():
    # Run identity, write mode, and id generators are explicitly out of Phase-5
    # scope (ADR-046 DD-4) and must remain importable from the package.
    from javdb.storage.db import (  # noqa: F401
        SESSION_ID_PATTERN,
        generate_integer_id,
        generate_session_id,
        get_active_run_identity,
        get_active_write_mode,
        set_active_run_identity,
        set_active_write_mode,
    )

    # The lock historically named for the session id is shared with the
    # run-identity / write-mode state, so it must survive the deletion.
    from javdb.storage.db._db_session import _active_session_id_lock  # noqa: F401

    assert callable(generate_session_id)
