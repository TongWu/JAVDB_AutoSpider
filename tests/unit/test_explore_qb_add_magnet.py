"""Regression tests for explore_service._qb_add_magnet's torrent-add payload.

BFR-018: the explore one-click "add to qB" flow hand-rolled the
/api/v2/torrents/add POST and used field names qBittorrent ignores —
"addPaused" (correct: "paused") and "name" (correct: "rename") — so the
AUTO_START preference and custom torrent name were silently dropped. The fix
delegates to the canonical QBittorrentClient.add_torrent, which uses the right
field names. These tests pin the corrected wire payload so it cannot regress.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from apps.api.services import explore_service  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _mk_response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


class _FakeSession:
    """Records POSTs; logs in OK, returns ``add_status`` for /torrents/add."""

    def __init__(self, add_status: int = 200):
        self.verify = True
        self.calls = []
        self._add_status = add_status

    def post(self, url, data=None, **kwargs):
        self.calls.append((url, data, kwargs))
        if url.endswith("/api/v2/auth/login"):
            return _mk_response(204, "")
        if url.endswith("/api/v2/torrents/add"):
            return _mk_response(self._add_status, "")
        return _mk_response(404, "")

    def add_payload(self):
        for url, data, _ in self.calls:
            if url.endswith("/api/v2/torrents/add"):
                return data
        raise AssertionError("no /torrents/add POST was made")


def _cfg(**overrides):
    base = {
        "QB_URL": "https://qb.example:8080",
        "QB_USERNAME": "admin",
        "QB_PASSWORD": "secret",
        "QB_VERIFY_TLS": False,
        "QB_ALLOW_INSECURE_HTTP": True,
        "REQUEST_TIMEOUT": 5,
        "TORRENT_SAVE_PATH": "/downloads",
    }
    base.update(overrides)
    return base


def _add(cfg, magnet="magnet:?xt=urn:btih:abc", title="My Movie", category="adhoc",
         add_status=200):
    fake = _FakeSession(add_status=add_status)
    with patch("requests.Session", return_value=fake):
        explore_service._qb_add_magnet(cfg, magnet, title, category)
    return fake


def test_add_magnet_uses_paused_field_not_addpaused():
    fake = _add(_cfg(AUTO_START=False))
    payload = fake.add_payload()
    assert payload.get("paused") == "true"
    assert "addPaused" not in payload


def test_add_magnet_auto_start_true_is_not_paused():
    fake = _add(_cfg(AUTO_START=True))
    assert fake.add_payload().get("paused") == "false"


def test_add_magnet_uses_rename_field_not_name():
    fake = _add(_cfg(AUTO_START=True), title="Custom Name")
    payload = fake.add_payload()
    assert payload.get("rename") == "Custom Name"
    assert "name" not in payload


def test_add_magnet_threads_category_and_savepath():
    fake = _add(_cfg(AUTO_START=True), category="my-cat")
    payload = fake.add_payload()
    assert payload.get("category") == "my-cat"
    assert payload.get("savepath") == "/downloads"


def test_add_magnet_non_200_raises_502():
    with pytest.raises(HTTPException) as exc:
        _add(_cfg(AUTO_START=True), add_status=403)
    assert exc.value.status_code == 502
