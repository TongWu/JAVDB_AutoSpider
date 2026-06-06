"""Telegram notify plugin — Bot API sendMessage (ADR-039 D3)."""

from __future__ import annotations

import requests

from javdb.infra.config import cfg
from javdb.integrations.notify.plugin import NotifyMessage, NotifyResult
from javdb.integrations.plugins.registry import REGISTRY

# Telegram sendMessage rejects messages longer than 4096 chars with a 400.
_TELEGRAM_MAX_CHARS = 4096


class TelegramNotifyPlugin:
    name = "telegram"

    def is_configured(self) -> bool:
        return bool(cfg("TELEGRAM_BOT_TOKEN", "")) and bool(cfg("TELEGRAM_CHAT_ID", ""))

    def send(self, message: NotifyMessage) -> NotifyResult:
        token = cfg("TELEGRAM_BOT_TOKEN", "")
        chat_id = cfg("TELEGRAM_CHAT_ID", "")
        # Send as plain text (no parse_mode): NotifyMessage carries arbitrary
        # content (error text, URLs, filenames) whose Markdown control chars
        # would otherwise make Telegram reject the whole message with a 400.
        # Truncate to Telegram's hard 4096-char limit so a long report or
        # traceback can't drop the notification entirely.
        text = f"{message.subject}\n{message.body}"
        if len(text) > _TELEGRAM_MAX_CHARS:
            text = text[:_TELEGRAM_MAX_CHARS - 1] + "…"
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=15,
            )
            ok = resp.status_code == 200
            return NotifyResult(plugin=self.name, ok=ok,
                                detail=None if ok else f"status {resp.status_code}")
        except Exception as exc:
            return NotifyResult(plugin=self.name, ok=False, detail=str(exc))


REGISTRY.register("notify", TelegramNotifyPlugin())
