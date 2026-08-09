"""D1-canonical persistence wiring for acquisition-outcome reconciliation."""

from __future__ import annotations

import contextlib

from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
from javdb.storage.repos.consumption_signal_repo import ConsumptionSignalRepo
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo
from javdb.storage.repos.unresolved_media_item_repo import UnresolvedMediaItemRepo


@contextlib.contextmanager
def open_outcome_repo():
    """Yield an AcquisitionOutcomeRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual). The DB
    path is resolved at call time (``_db.OPERATIONS_DB_PATH``) rather than bound
    at import, so pytest's path monkeypatch is honoured (BFR-016).
    """
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield AcquisitionOutcomeRepo(conn)


@contextlib.contextmanager
def open_ledger_repo():
    """Yield an OwnershipLedgerRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual). The DB
    path is resolved at call time (``_db.OPERATIONS_DB_PATH``) rather than bound
    at import, so pytest's path monkeypatch is honoured (BFR-016).
    """
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield OwnershipLedgerRepo(conn)


@contextlib.contextmanager
def open_consumption_repo():
    """Yield a ConsumptionSignalRepo over the operations DB connection.

    Path resolved at call time (_db.OPERATIONS_DB_PATH) per BFR-016, matching
    open_outcome_repo."""
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield ConsumptionSignalRepo(conn)


@contextlib.contextmanager
def open_unresolved_repo():
    """Yield an UnresolvedMediaItemRepo over the operations DB connection."""
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield UnresolvedMediaItemRepo(conn)
