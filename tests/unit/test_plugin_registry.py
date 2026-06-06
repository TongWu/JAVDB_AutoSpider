from javdb.integrations.plugins.registry import PluginRegistry


class _Stub:
    name = "stub"


def test_register_get_list():
    reg = PluginRegistry()
    reg.register("notify", _Stub())
    assert reg.get("notify", "stub").name == "stub"
    assert [p.name for p in reg.list("notify")] == ["stub"]


def test_get_unknown_returns_none():
    assert PluginRegistry().get("notify", "nope") is None


def test_discover_entry_points_is_noop_in_phase1():
    assert PluginRegistry().discover_entry_points("javdb.notify_plugins") == 0
