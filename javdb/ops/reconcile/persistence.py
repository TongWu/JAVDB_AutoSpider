"""D1-canonical persistence wiring for acquisition-outcome reconciliation."""

from __future__ import annotations

import contextlib

from javdb.storage.db import OPERATIONS_DB_PATH, get_db
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo


@contextlib.contextmanager
def open_outcome_repo():
    """Yield an AcquisitionOutcomeRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual).
    """
    with get_db(OPERATIONS_DB_PATH) as conn:
        yield AcquisitionOutcomeRepo(conn)
