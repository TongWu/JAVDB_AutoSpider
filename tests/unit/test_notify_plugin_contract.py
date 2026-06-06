from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult


def test_message_defaults():
    m = NotifyMessage(subject="s", body="b")
    assert m.level == "info"


def test_result_ok_flag():
    r = NotifyResult(plugin="email", ok=True)
    assert r.ok is True
    assert r.detail is None
