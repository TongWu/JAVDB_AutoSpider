# tests/harness/fake_external.py
"""Neuter building blocks for the non-core subprocess seams (ADR-037 D5, Phase 2).

PikPak and rclone are not on the daily spider->uploader->commit path the harness
drives, so these are deliberately minimal: PikPak is already globally mocked at
conftest import (the ``pikpakapi`` module is a MagicMock), and ``neuter_rclone``
stubs the rclone shell-out probe so a future in-process scenario can exercise the
rclone manager without an ``rclone`` binary."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def assert_pikpak_neutered() -> None:
    """Confirm conftest's global pikpakapi MagicMock is in place."""
    mod = sys.modules.get("pikpakapi")
    assert isinstance(mod, MagicMock), (
        "pikpakapi is not globally mocked — tests/conftest.py installs a MagicMock "
        "at import time; a real pikpakapi here would dial the network."
    )


def neuter_rclone(monkeypatch) -> None:
    """Stub the rclone install probe so the manager runs without a binary."""
    import javdb.integrations.rclone.manager.service as rclone_service
    # check_rclone_installed() -> Tuple[bool, str] (javdb/integrations/rclone/
    # helper.py:394); callers unpack ``ok, msg = ...`` (manager/service.py:1223,
    # :1415). The stub MUST return a 2-tuple, not a bare bool, or the manager
    # raises "cannot unpack non-iterable bool object".
    monkeypatch.setattr(rclone_service, "check_rclone_installed",
                        lambda: (True, "stubbed: harness neuter"))
