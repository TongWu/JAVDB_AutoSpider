"""D1-canonical persistence wiring for acquisition-outcome reconciliation."""

from __future__ import annotations

import contextlib
import logging

from javdb.storage.db import get_db
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def open_outcome_repo():
    """Yield an AcquisitionOutcomeRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual).
    """
    with get_db("operations") as conn:
        yield AcquisitionOutcomeRepo(conn)
