"""D1-canonical persistence wiring for acquisition-outcome reconciliation."""

from __future__ import annotations

import contextlib

from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo


@contextlib.contextmanager
def open_outcome_repo():
    """Yield an AcquisitionOutcomeRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual). The DB
    path is resolved at call time (``_db.OPERATIONS_DB_PATH``) rather than bound
    at import, so pytest's path monkeypatch is honoured (BFR-016).
    """
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield AcquisitionOutcomeRepo(conn)
