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


def test_runtime_close_releases_runtime_owned_services():
    from javdb.proxy import ban_manager

    runtime = SpiderRuntime()
    calls = []

    class Closable:
        def __init__(self, label):
            self.label = label

        def close(self):
            calls.append(("close", self.label))

    class Policy:
        def shutdown(self):
            calls.append(("shutdown", "policy"))

    runtime.services.proxy_coordinator = Closable("proxy")
    runtime.services.login_state_client = Closable("login")
    runtime.services.work_distributor_client = Closable("work")
    runtime.services.recommend_proxy_policy = Policy()
    runtime.movie_claim.client_public = Closable("movie-public")
    runtime.movie_claim.client_pending = Closable("movie-pending")
    ban_manager.set_remote_ban_hook(lambda _name: calls.append(("hook", "ban")))
    ban_manager.set_remote_unban_hook(lambda _name: calls.append(("hook", "unban")))

    runtime.close()

    assert ("close", "proxy") in calls
    assert ("close", "login") in calls
    assert ("close", "work") in calls
    assert ("shutdown", "policy") in calls
    assert ("close", "movie-public") in calls
    assert ("close", "movie-pending") in calls
    assert runtime.services.proxy_coordinator is None
    assert runtime.services.login_state_client is None
    assert runtime.services.work_distributor_client is None
    assert runtime.services.recommend_proxy_policy is None
    assert runtime.movie_claim.client_public is None
    assert runtime.movie_claim.client_pending is None
    ban_manager._dispatch_remote_ban("proxy-a")
    ban_manager._dispatch_remote_unban("proxy-a")
    assert ("hook", "ban") not in calls
    assert ("hook", "unban") not in calls
