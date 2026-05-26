from __future__ import annotations

from javdb.spider.runtime.context import SpiderRuntime


def test_runtime_instances_do_not_share_detail_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    first.detail.parsed_links.add("/v/first")

    assert "/v/first" in first.detail.parsed_links
    assert second.detail.parsed_links == set()


def test_runtime_instances_do_not_share_login_budget_state():
    first = SpiderRuntime()
    second = SpiderRuntime()

    first.login.login_attempts_per_proxy["proxy-a"] = 2
    first.login.login_total_attempts = 2

    assert second.login.login_attempts_per_proxy == {}
    assert second.login.login_total_attempts == 0


def test_runtime_instances_do_not_share_proxy_ban_html_files():
    first = SpiderRuntime()
    second = SpiderRuntime()

    first.proxy.proxy_ban_html_files.append("logs/proxy_ban_a.txt")

    assert first.proxy.proxy_ban_html_files == ["logs/proxy_ban_a.txt"]
    assert second.proxy.proxy_ban_html_files == []


def test_runtime_instances_have_distinct_holder_ids():
    first = SpiderRuntime()
    second = SpiderRuntime()

    assert first.runner_registry.holder_id.startswith("runner-")
    assert second.runner_registry.holder_id.startswith("runner-")
    assert first.runner_registry.holder_id != second.runner_registry.holder_id


def test_runtime_close_is_idempotent_before_services_are_migrated():
    runtime = SpiderRuntime()

    runtime.close()
    runtime.close()

    assert runtime.closed is True
