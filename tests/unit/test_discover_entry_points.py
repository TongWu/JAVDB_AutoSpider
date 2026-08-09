"""Tests for PluginRegistry.discover_entry_points (ADR-039 Phase 2)."""
from unittest.mock import MagicMock, patch

from javdb.integrations.plugins.registry import PluginRegistry


class _FakePlugin:
    name = "fake"


def _make_ep(load_result=None, raises=False):
    """Build a fake importlib EntryPoint mock."""
    ep = MagicMock()
    if raises:
        ep.load.side_effect = ImportError("broken plugin")
    else:
        ep.load.return_value = load_result
    return ep


def _ep_mock(eps_for_group: list):
    """Return a mock for importlib.metadata.entry_points that accepts group= kwarg
    and returns the given list of fake entry points."""
    mock = MagicMock(return_value=eps_for_group)
    return mock


def test_empty_group_returns_zero():
    """No entry points for the group → returns 0, no exception."""
    reg = PluginRegistry()
    with patch("importlib.metadata.entry_points", _ep_mock([])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 0


def test_one_working_plugin_registered_and_counted():
    """One healthy entry point → registered + count 1."""
    reg = PluginRegistry()
    # ep.load() returns the class; instantiate it to register
    ep = _make_ep(load_result=_FakePlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 1
    plugin = reg.get("notify", "fake")
    assert plugin is not None
    assert plugin.name == "fake"


def test_broken_plugin_skipped_count_unaffected():
    """Entry point that raises on .load() → skipped; count reflects only successes."""
    reg = PluginRegistry()
    ep_bad = _make_ep(raises=True)
    ep_good = _make_ep(load_result=_FakePlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep_bad, ep_good])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 1
    assert reg.get("notify", "fake") is not None


def test_metadata_failure_returns_zero_no_exception():
    """importlib.metadata itself raises → returns 0, never propagates."""
    reg = PluginRegistry()
    with patch("importlib.metadata.entry_points", side_effect=Exception("metadata exploded")):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 0


def test_category_derived_from_group_notify():
    """javdb.notify_plugins → category 'notify'."""
    reg = PluginRegistry()
    ep = _make_ep(load_result=_FakePlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        reg.discover_entry_points("javdb.notify_plugins")
    assert reg.get("notify", "fake") is not None


def test_category_derived_from_group_downloader():
    """javdb.downloader_plugins → category 'downloader'."""
    class _DlPlugin:
        name = "fake-dl"
    reg = PluginRegistry()
    ep = _make_ep(load_result=_DlPlugin)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        reg.discover_entry_points("javdb.downloader_plugins")
    assert reg.get("downloader", "fake-dl") is not None


def test_instance_registered_directly():
    """ep.load() that returns an instance (not a class) is registered if it has .name."""
    reg = PluginRegistry()
    instance = _FakePlugin()
    ep = _make_ep(load_result=instance)
    with patch("importlib.metadata.entry_points", _ep_mock([ep])):
        count = reg.discover_entry_points("javdb.notify_plugins")
    assert count == 1
    assert reg.get("notify", "fake") is not None
