"""Unit tests for the B2 preference gate in the qBittorrent uploader (ADR-022)."""

from unittest.mock import patch


def _cfg_side_effect_disabled(key, default=None):
    """Side effect for cfg() that sets PREFERENCE_GATE_ENABLED=False but returns sensible defaults for other keys."""
    if key == 'PREFERENCE_GATE_ENABLED':
        return False
    # Return the default for other config keys to avoid breaking imports
    return default


def _cfg_side_effect_enabled(key, default=None):
    """Side effect for cfg() that sets PREFERENCE_GATE_ENABLED=True but returns sensible defaults for other keys."""
    if key == 'PREFERENCE_GATE_ENABLED':
        return True
    # Return the default for other config keys to avoid breaking imports
    return default


class TestPreferenceGate:

    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_disabled)
    def test_gate_disabled_always_returns_false(self, _):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/ANYONE'}) is False

    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_disabled)
    def test_gate_disabled_with_empty_actor_link(self, _):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({}) is False

    @patch('javdb.integrations.qb.uploader._preference_gate_blocks.__wrapped__', create=True)
    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           return_value=True)
    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_blocks_when_actor_disliked(self, _cfg, _blocked, _wrap):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/BLOCKED'}) is True

    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           return_value=False)
    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_allows_when_actor_not_blocked(self, _cfg, _blocked):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/LIKED'}) is False

    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_allows_when_no_actor_link(self, _cfg):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({}) is False

    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_allows_when_actor_link_is_empty_string(self, _cfg):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': ''}) is False

    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           side_effect=Exception("DB unavailable"))
    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_fails_open_on_exception(self, _cfg, _blocked):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/ANY'}) is False

    @patch('javdb.integrations.qb.uploader.service._resolve_actor_link',
           return_value='/actors/BLOCKED')
    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           return_value=True)
    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_resolves_actor_from_href_when_actor_link_absent(
        self, _cfg, _blocked, _resolve
    ):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        # No actor_link in the CSV row, but href is present -> resolve from history.
        assert _preference_gate_blocks({'href': '/video/ABC-001'}) is True
        _resolve.assert_called_once_with('/video/ABC-001')

    @patch('javdb.integrations.qb.uploader.service._resolve_actor_link',
           return_value='')
    @patch('javdb.infra.config.cfg', side_effect=_cfg_side_effect_enabled)
    def test_gate_allows_when_actor_link_unresolvable_from_href(
        self, _cfg, _resolve
    ):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'href': '/video/UNKNOWN'}) is False
