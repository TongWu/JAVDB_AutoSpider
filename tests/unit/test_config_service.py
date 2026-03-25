import os
import sys
from types import ModuleType


project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from apps.api.services import config_service  # noqa: E402


def test_load_runtime_config_derives_qb_url_from_legacy_config(monkeypatch):
    legacy_config = ModuleType("config")
    legacy_config.QB_HOST = "qb.internal"
    legacy_config.QB_PORT = "8080"
    legacy_config.QB_SCHEME = "http"
    legacy_config.QB_ALLOW_INSECURE_HTTP = True

    monkeypatch.setattr(config_service.importlib, "import_module", lambda _name: legacy_config)
    monkeypatch.setattr(config_service, "load_store", lambda: {})

    cfg = config_service.load_runtime_config()

    assert cfg["QB_URL"] == "http://qb.internal:8080"
