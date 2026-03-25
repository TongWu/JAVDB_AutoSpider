import os
import runpy
import sys
from types import SimpleNamespace

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import compat  # noqa: E402


def test_legacy_spider_wrapper_executes_legacy_main(monkeypatch):
    calls = {"count": 0}

    def fake_alias_module(legacy_name: str, canonical_name: str):
        assert legacy_name == "__main__"
        assert canonical_name == "legacy._spider_legacy"

        def fake_main():
            calls["count"] += 1
            return 0

        return SimpleNamespace(main=fake_main)

    monkeypatch.setattr(compat, "alias_module", fake_alias_module)

    with monkeypatch.context() as ctx:
        ctx.setattr(sys, "argv", [os.path.join(project_root, "scripts", "_spider_legacy.py")])
        try:
            runpy.run_path(
                os.path.join(project_root, "scripts", "_spider_legacy.py"),
                run_name="__main__",
            )
        except SystemExit as exc:
            assert exc.code in (0, None)

    assert calls["count"] == 1
