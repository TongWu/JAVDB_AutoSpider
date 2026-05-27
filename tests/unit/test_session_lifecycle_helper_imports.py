from __future__ import annotations

import importlib.util

from javdb.storage.sessions import lifecycle_helpers


def test_canonical_lifecycle_helpers_remain_importable():
    assert lifecycle_helpers.normalize_run_started_at is not None


def test_legacy_session_helper_wrappers_are_deleted():
    assert importlib.util.find_spec("apps.cli.db._session_helpers") is None
    assert importlib.util.find_spec("javdb.storage.rollback.session_helpers") is None
