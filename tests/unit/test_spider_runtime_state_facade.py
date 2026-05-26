from __future__ import annotations

import javdb.spider.runtime.state as state
from javdb.spider.runtime.context import SpiderRuntime


def test_bind_active_runtime_rebinds_mutable_detail_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    state.bind_active_runtime(first)
    state.parsed_links.add("/v/first")
    assert first.detail.parsed_links == {"/v/first"}

    state.bind_active_runtime(second)
    assert state.parsed_links is second.detail.parsed_links
    assert state.parsed_links == set()


def test_bind_active_runtime_rebinds_proxy_ban_html_files():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)
    state.proxy_ban_html_files.append("logs/proxy_ban.txt")

    assert runtime.proxy.proxy_ban_html_files == ["logs/proxy_ban.txt"]


def test_bind_active_runtime_exposes_runtime_holder_id():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)

    assert state.runtime_holder_id == runtime.runner_registry.holder_id


def test_clear_active_runtime_leaves_facade_importable():
    runtime = SpiderRuntime()

    state.bind_active_runtime(runtime)
    state.clear_active_runtime(runtime)

    assert state.get_active_runtime() is None
    assert isinstance(state.parsed_links, set)
