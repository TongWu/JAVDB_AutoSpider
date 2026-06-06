import javdb.integrations.notify.telegram.plugin as tg
from javdb.integrations.notify.plugin import NotifyMessage


def test_is_configured_reads_config(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "t",
                                                          "TELEGRAM_CHAT_ID": "c"}.get(name, default))
    assert tg.TelegramNotifyPlugin().is_configured() is True


def test_is_configured_false_when_missing(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: default)
    assert tg.TelegramNotifyPlugin().is_configured() is False


def test_send_posts_to_bot_api(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "TKN",
                                                          "TELEGRAM_CHAT_ID": "CHAT"}.get(name, default))
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(tg.requests, "post", _post)
    result = tg.TelegramNotifyPlugin().send(NotifyMessage(subject="Sub", body="Body"))
    assert "botTKN/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == "CHAT"
    assert "Sub" in captured["json"]["text"] and "Body" in captured["json"]["text"]
    assert result.ok is True


def test_send_reports_failure_on_non_200(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "T",
                                                          "TELEGRAM_CHAT_ID": "C"}.get(name, default))

    class _Resp:
        status_code = 400

    monkeypatch.setattr(tg.requests, "post", lambda url, **kw: _Resp())
    result = tg.TelegramNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False
    assert result.detail == "status 400"


def test_send_reports_failure_on_exception(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "T",
                                                          "TELEGRAM_CHAT_ID": "C"}.get(name, default))

    def _boom(url, **kw):
        raise RuntimeError("conn refused")

    monkeypatch.setattr(tg.requests, "post", _boom)
    result = tg.TelegramNotifyPlugin().send(NotifyMessage(subject="s", body="b"))
    assert result.ok is False
    assert "conn refused" in (result.detail or "")


def test_send_uses_plain_text_without_markdown(monkeypatch):
    # Arbitrary content with Markdown control chars must not be parsed (no
    # parse_mode) — otherwise Telegram 400s and the message is lost.
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "T",
                                                          "TELEGRAM_CHAT_ID": "C"}.get(name, default))
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(tg.requests, "post", _post)
    result = tg.TelegramNotifyPlugin().send(NotifyMessage(subject="file_[1]", body="a*b_c"))
    assert "parse_mode" not in captured["json"]
    assert captured["json"]["text"] == "file_[1]\na*b_c"
    assert result.ok is True


def test_send_truncates_to_telegram_limit(monkeypatch):
    monkeypatch.setattr(tg, "cfg", lambda name, default: {"TELEGRAM_BOT_TOKEN": "T",
                                                          "TELEGRAM_CHAT_ID": "C"}.get(name, default))
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        captured["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(tg.requests, "post", _post)
    result = tg.TelegramNotifyPlugin().send(NotifyMessage(subject="s", body="x" * 5000))
    assert len(captured["json"]["text"]) <= 4096
    assert result.ok is True
