"""Unit tests for W6.A.1 — apply W5.3 ConfigSnapshot to runtime.

Two surfaces under test:

* :meth:`TripleWindowThrottle.apply_config` — partial overrides + clamps.
* :func:`state._apply_config_snapshot` — heartbeat → throttle propagation
  and the version-based skip optimisation.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.runner_registry_client import (  # noqa: E402
    ConfigSnapshot,
)
from packages.python.javdb_spider.runtime.sleep import (  # noqa: E402
    TripleWindowThrottle,
    triple_window_throttle,
)


# ---------------------------------------------------------------------------
# TripleWindowThrottle.apply_config — direct behaviour
# ---------------------------------------------------------------------------


class TestApplyConfigDirect:
    def test_no_args_is_no_op(self):
        twt = TripleWindowThrottle()
        before = (twt.short_max, twt.long_max, twt.extra_max,
                  twt.short_window, twt.long_window, twt.extra_window)
        twt.apply_config()
        after = (twt.short_max, twt.long_max, twt.extra_max,
                 twt.short_window, twt.long_window, twt.extra_window)
        assert before == after

    def test_partial_update_only_touches_supplied_keys(self):
        twt = TripleWindowThrottle()
        original_long = twt.long_max
        twt.apply_config(short_max=7)
        assert twt.short_max == 7
        assert twt.long_max == original_long  # untouched

    def test_setting_short_max_updates_base_for_set_runner_scale(self):
        # The W6.A.1 contract: ``apply_config`` updates _base_* so that
        # subsequent set_runner_scale divisions use the new baseline.
        twt = TripleWindowThrottle()
        twt.apply_config(long_max=60, extra_max=400)
        twt.set_runner_scale(3)
        # 60 // 3 = 20, 400 // 3 = 133.
        assert twt.long_max == 20
        assert twt.extra_max == 133

    def test_negative_max_clamped_to_one(self):
        twt = TripleWindowThrottle()
        twt.apply_config(short_max=-5, long_max=0)
        assert twt.short_max == 1
        assert twt.long_max == 1

    def test_window_seconds_floor_at_one(self):
        twt = TripleWindowThrottle()
        twt.apply_config(short_window_sec=0.0)
        assert twt.short_window == 1.0

    def test_float_max_coerced_to_int(self):
        twt = TripleWindowThrottle()
        twt.apply_config(short_max=4.9)  # type: ignore[arg-type]
        assert twt.short_max == 4
        assert twt._base_short_max == 4


# ---------------------------------------------------------------------------
# state._apply_config_snapshot — heartbeat propagation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state_version_and_throttle():
    """Reset the module-level version + throttle baselines + heartbeat
    constants so each test starts from a known state.

    ``_apply_config_snapshot`` mutates module-level constants
    (``_RUNNER_HEARTBEAT_INTERVAL_SEC``, ``_HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC``)
    when ``heartbeat_interval_sec`` is present in the snapshot; those
    mutations persist across the whole pytest session unless we restore
    them, which would break unrelated tests that assert the baseline.
    """
    import packages.python.javdb_spider.runtime.state as state_mod
    state_mod._last_applied_config_version = -1
    original = (
        triple_window_throttle.short_max,
        triple_window_throttle.long_max,
        triple_window_throttle.extra_max,
        triple_window_throttle.short_window,
        triple_window_throttle.long_window,
        triple_window_throttle.extra_window,
        triple_window_throttle._base_short_max,
        triple_window_throttle._base_long_max,
        triple_window_throttle._base_extra_max,
    )
    original_heartbeat = (
        state_mod._RUNNER_HEARTBEAT_INTERVAL_SEC,
        state_mod._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC,
    )
    yield
    state_mod._last_applied_config_version = -1
    (
        triple_window_throttle.short_max,
        triple_window_throttle.long_max,
        triple_window_throttle.extra_max,
        triple_window_throttle.short_window,
        triple_window_throttle.long_window,
        triple_window_throttle.extra_window,
        triple_window_throttle._base_short_max,
        triple_window_throttle._base_long_max,
        triple_window_throttle._base_extra_max,
    ) = original
    state_mod._RUNNER_HEARTBEAT_INTERVAL_SEC = original_heartbeat[0]
    state_mod._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC = original_heartbeat[1]


class TestApplyConfigSnapshot:
    def test_applies_throttle_max_overrides(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        snap = ConfigSnapshot(
            version=1, updated_at_ms=0,
            values={"short_max": "1", "long_max": "9", "extra_max": "42"},
        )
        _apply_config_snapshot(snap)
        assert triple_window_throttle.short_max == 1
        assert triple_window_throttle.long_max == 9
        assert triple_window_throttle.extra_max == 42

    def test_applies_throttle_window_overrides(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        snap = ConfigSnapshot(
            version=2, updated_at_ms=0,
            values={
                "short_window_sec": "15",
                "long_window_sec": "150",
                "extra_window_sec": "900",
            },
        )
        _apply_config_snapshot(snap)
        assert triple_window_throttle.short_window == 15.0
        assert triple_window_throttle.long_window == 150.0
        assert triple_window_throttle.extra_window == 900.0

    def test_skips_when_version_unchanged(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        snap1 = ConfigSnapshot(
            version=5, updated_at_ms=0, values={"short_max": "2"},
        )
        _apply_config_snapshot(snap1)
        assert triple_window_throttle.short_max == 2
        # Push a different value with the SAME version → must be ignored.
        snap2 = ConfigSnapshot(
            version=5, updated_at_ms=0, values={"short_max": "999"},
        )
        _apply_config_snapshot(snap2)
        assert triple_window_throttle.short_max == 2  # unchanged

    def test_reapplies_when_version_increments(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        _apply_config_snapshot(
            ConfigSnapshot(version=1, updated_at_ms=0, values={"short_max": "2"}),
        )
        _apply_config_snapshot(
            ConfigSnapshot(version=2, updated_at_ms=0, values={"short_max": "8"}),
        )
        assert triple_window_throttle.short_max == 8

    def test_unknown_keys_are_silently_skipped(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        original_short = triple_window_throttle.short_max
        snap = ConfigSnapshot(
            version=1, updated_at_ms=0,
            values={"totally_made_up_key": "1", "ban_ttl_ms": "5000"},
        )
        _apply_config_snapshot(snap)
        # No throttle fields touched.
        assert triple_window_throttle.short_max == original_short

    def test_non_numeric_value_warns_and_skips_that_key(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        original_short = triple_window_throttle.short_max
        snap = ConfigSnapshot(
            version=1, updated_at_ms=0,
            values={"short_max": "not-a-number", "long_max": "12"},
        )
        with patch(
            "packages.python.javdb_spider.runtime.state.logger.warning",
        ) as warn:
            _apply_config_snapshot(snap)
        # Bad key skipped; good key still applied.
        assert triple_window_throttle.short_max == original_short
        assert triple_window_throttle.long_max == 12
        assert warn.called

    def test_empty_values_is_safe(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        snap = ConfigSnapshot(version=1, updated_at_ms=0, values={})
        original_short = triple_window_throttle.short_max
        _apply_config_snapshot(snap)
        # Nothing changed, but the version watermark advanced.
        import packages.python.javdb_spider.runtime.state as state_mod
        assert state_mod._last_applied_config_version == 1
        assert triple_window_throttle.short_max == original_short

    def test_heartbeat_interval_override_takes_effect(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_config_snapshot,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        snap = ConfigSnapshot(
            version=1, updated_at_ms=0,
            values={"heartbeat_interval_sec": "30"},
        )
        _apply_config_snapshot(snap)
        assert state_mod._RUNNER_HEARTBEAT_INTERVAL_SEC == 30.0
        assert state_mod._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC == 30.0
