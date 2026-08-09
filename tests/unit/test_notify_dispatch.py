import javdb.integrations.notify.dispatch as dispatch
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import PluginRegistry


class _Plugin:
    def __init__(self, name, configured=True, raises=False):
        self.name = name
        self._configured = configured
        self._raises = raises
    def is_configured(self):
        return self._configured
    def send(self, message):
        if self._raises:
            raise RuntimeError("boom")
        return NotifyResult(plugin=self.name, ok=True)


def _registry(*plugins):
    reg = PluginRegistry()
    for p in plugins:
        reg.register("notify", p)
    return reg


def test_active_names_default_email(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: default)
    assert dispatch.active_names() == ["email"]


def test_active_names_csv_string(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: "email, telegram")
    assert dispatch.active_names() == ["email", "telegram"]


def test_send_fans_out_and_isolates_failure(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["email", "telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY",
                        _registry(_Plugin("email", raises=True), _Plugin("telegram")))
    results = {r.plugin: r for r in dispatch.send(NotifyMessage(subject="s", body="b"))}
    assert results["email"].ok is False        # isolated failure
    assert results["telegram"].ok is True       # still delivered


def test_send_skips_unconfigured(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_Plugin("telegram", configured=False)))
    results = dispatch.send(NotifyMessage(subject="s", body="b"))
    assert results[0].ok is False
    assert "not configured" in (results[0].detail or "")


def test_active_names_ignores_non_iterable(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: 123)
    assert dispatch.active_names() == ["email"]


def test_send_reports_not_registered(monkeypatch):
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["ghost"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry())
    results = dispatch.send(NotifyMessage(subject="s", body="b"))
    assert results[0].ok is False
    assert "not registered" in (results[0].detail or "")


def test_send_isolates_is_configured_error(monkeypatch):
    # A plugin whose is_configured() raises must not abort the whole fan-out;
    # it is isolated as a failed result and later backends still deliver.
    class _BadConfig:
        name = "bad"
        def is_configured(self):
            raise RuntimeError("config boom")
        def send(self, message):
            return NotifyResult(plugin=self.name, ok=True)

    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["bad", "telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_BadConfig(), _Plugin("telegram")))
    results = {r.plugin: r for r in dispatch.send(NotifyMessage(subject="s", body="b"))}
    assert results["bad"].ok is False
    assert "config boom" in (results["bad"].detail or "")
    assert results["telegram"].ok is True       # still delivered


def test_send_exclude_skips_named_backend(monkeypatch):
    # ADR-039 D4: a caller that already handled 'email' by another route passes
    # exclude={'email'} so it is not iterated/double-notified; other backends run.
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["email", "telegram"])
    monkeypatch.setattr(dispatch, "REGISTRY",
                        _registry(_Plugin("email"), _Plugin("telegram")))
    results = dispatch.send(NotifyMessage(subject="s", body="b"), exclude={"email"})
    plugins = {r.plugin for r in results}
    assert plugins == {"telegram"}              # email excluded entirely
    assert results[0].ok is True


def test_active_names_sanitizes_invalid_list_items(monkeypatch):
    # Fail-safe: a list with non-string / empty / unhashable items (config typo)
    # is sanitized to clean backend names so send()'s `name in excluded` hashing
    # and the REGISTRY lookup never see a non-string/unhashable name.
    monkeypatch.setattr(dispatch, "cfg",
                        lambda name, default: ["email", {}, "", 123, " telegram "])
    assert dispatch.active_names() == ["email", "telegram"]


def test_send_tolerates_invalid_iterable_items(monkeypatch):
    # A typo'd NOTIFY_BACKENDS with an unhashable element must not crash the
    # fan-out (regression for the exclude membership test hashing the name).
    monkeypatch.setattr(dispatch, "cfg", lambda name, default: ["telegram", {}])
    monkeypatch.setattr(dispatch, "REGISTRY", _registry(_Plugin("telegram")))
    results = dispatch.send(NotifyMessage(subject="s", body="b"), exclude={"email"})
    assert [r.plugin for r in results] == ["telegram"]
    assert results[0].ok is True
