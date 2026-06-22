"""Regression guard: CF_BYPASS_VIA_PROXY must be wired into every production
RequestConfig constructor.

The flag controls whether the spider tunnels CF-bypass requests through the
proxy (so the bypass service can bind to loopback only). It lives on
RequestConfig, but several production entrypoints build their own
RequestConfig and read module-level config constants — if any of them forgets
to pass cf_bypass_via_proxy, daily/AdHoc/login scraping silently keeps the
default (False) and breaks once the bypass service is bound to 127.0.0.1.

The checks parse the source with ``ast`` (rather than counting strings, which
would miss comments/strings and can't prove two kwargs share the same call) and
assert that every RequestConfig(...) call passing cf_bypass_enabled also passes
cf_bypass_via_proxy, and that the config modules define the constant.
``ast.parse`` only parses — it never executes — so modules that sys.exit at
import time (e.g. login.py without config.py) are still safe to inspect.
"""
import ast
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Production constructors that assemble a RequestConfig with CF-bypass fields.
# Legacy (javdb/legacy/) is excluded — not imported by production (ADR-007).
PRODUCTION_FILES = [
    "javdb/spider/runtime/state.py",
    "javdb/spider/fetch/fetch_engine.py",
    "javdb/spider/runtime/context.py",
    "javdb/spider/auth/login.py",
    "javdb/spider/spider_gateway.py",
]

# Modules that define CF_BYPASS_* config constants must define VIA_PROXY too.
CONSTANT_SOURCES = [
    "javdb/spider/runtime/config.py",
    "javdb/spider/auth/login.py",
]


def _parse(rel_path):
    with open(os.path.join(PROJECT_ROOT, rel_path), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=rel_path)


def _request_config_calls(tree):
    """Yield every ``RequestConfig(...)`` call (bare or attribute access)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "RequestConfig":
            yield node


def test_every_enabled_construction_also_sets_via_proxy():
    """Each RequestConfig call setting cf_bypass_enabled must also set via_proxy."""
    seen = 0
    for rel_path in PRODUCTION_FILES:
        for call in _request_config_calls(_parse(rel_path)):
            kwargs = {kw.arg for kw in call.keywords if kw.arg}
            if "cf_bypass_enabled" in kwargs:
                seen += 1
                assert "cf_bypass_via_proxy" in kwargs, (
                    f"{rel_path}:{call.lineno}: RequestConfig sets cf_bypass_enabled "
                    f"but not cf_bypass_via_proxy"
                )
    assert seen >= len(PRODUCTION_FILES), (
        f"expected at least one CF-bypass RequestConfig per production file, saw {seen}"
    )


def test_constant_sources_define_via_proxy():
    """Config modules defining CF_BYPASS_* constants must define VIA_PROXY too."""
    for rel_path in CONSTANT_SOURCES:
        names = {
            target.id
            for node in _parse(rel_path).body  # module-level assignments only
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert "CF_BYPASS_VIA_PROXY" in names, (
            f"{rel_path}: defines CF_BYPASS_* constants but not CF_BYPASS_VIA_PROXY"
        )
