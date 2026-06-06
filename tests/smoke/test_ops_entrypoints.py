"""Smoke tests for the apps.cli.ops.* thin CLI entrypoints.

These three modules (health_check, config_generator, fetch_page) used to replace
themselves in ``sys.modules`` with their canonical ``javdb.infra.*`` module
(the "A-residual" self-alias). They now expose a conventional explicit ``main``
re-exported from the canonical module. These tests pin both behaviours:

1. ``python -m apps.cli.ops.<x> --help`` still runs (exit 0).
2. ``import apps.cli.ops.<x>`` yields a real module — not a self-alias — whose
   ``main`` is the canonical ``javdb.infra.<x>.main``.

The import-shape check runs in a subprocess on purpose: the canonical
health_check / fetch_page modules ``os.chdir`` at import time, so an in-process
import would leak the working directory into sibling tests.
"""

import subprocess
import sys

import pytest

OPS_MODULES = ("health_check", "config_generator", "fetch_page")


@pytest.mark.parametrize("name", OPS_MODULES)
def test_ops_cli_help_runs(name):
    """The documented `python -m apps.cli.ops.<x> --help` path stays green."""
    r = subprocess.run(
        [sys.executable, "-m", f"apps.cli.ops.{name}", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "usage:" in r.stdout.lower()


@pytest.mark.parametrize("name", OPS_MODULES)
def test_ops_module_is_real_module_with_main(name):
    """Importing the alias returns a genuine module exposing a callable `main`
    re-exported from the canonical javdb.infra.<x> module — i.e. the sys.modules
    self-replacement is gone (a self-alias would report __name__ == javdb.infra.<x>).
    """
    code = (
        f"import types, apps.cli.ops.{name} as m, javdb.infra.{name} as c\n"
        f"assert isinstance(m, types.ModuleType)\n"
        f"assert m.__name__ == 'apps.cli.ops.{name}', m.__name__\n"
        f"assert callable(m.main) and m.main is c.main\n"
        f"print('SHAPE_OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "SHAPE_OK" in r.stdout
