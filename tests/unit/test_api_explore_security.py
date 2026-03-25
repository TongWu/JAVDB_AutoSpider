import os
import sys

import pytest
from fastapi import HTTPException

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.services import explore_service  # noqa: E402


def test_qb_login_session_hides_transport_validation_details():
    with pytest.raises(HTTPException) as exc_info:
        explore_service._qb_login_session(
            {
                "QB_HOST": "qb.internal",
                "QB_PORT": "8080",
                "QB_USERNAME": "admin",
                "QB_PASSWORD": "secret",
                "QB_SCHEME": "http",
                "QB_ALLOW_INSECURE_HTTP": False,
            }
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid qBittorrent transport settings"


def test_validate_javdb_url_requires_https(monkeypatch):
    def fake_resolve(url: str):
        return type("Parsed", (), {"scheme": "http"})(), "javdb.com", "1.2.3.4"

    monkeypatch.setattr(explore_service, "_resolve_public_target_or_422", fake_resolve)

    with pytest.raises(HTTPException, match="url must use https"):
        explore_service._validate_javdb_url_or_422("http://javdb.com/v/abc123")
