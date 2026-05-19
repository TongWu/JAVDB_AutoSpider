"""Phase 1: proxy_pool serialiser for RunnerRegistry register payload (ADR-004)."""

import pytest

from javdb.proxy.coordinator.runner_registry_client import (
    proxy_pool_summary_for_registry,
)


def test_empty_pool_returns_empty_list():
    assert proxy_pool_summary_for_registry([]) == []


def test_none_pool_returns_empty_list():
    assert proxy_pool_summary_for_registry(None) == []


def test_basic_pool_returns_id_and_name():
    pool = [
        {"name": "Singapore Arm-3", "http": "http://x:7890", "https": "http://x:7890"},
        {"name": "Tokyo Backup-1", "http": "http://y:7890", "https": "http://y:7890"},
    ]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "Singapore Arm-3", "name": "Singapore Arm-3"},
        {"id": "Tokyo Backup-1", "name": "Tokyo Backup-1"},
    ]


def test_whitespace_in_name_is_stripped():
    pool = [{"name": "  Singapore Arm-3  ", "http": "x"}]
    result = proxy_pool_summary_for_registry(pool)
    assert result == [{"id": "Singapore Arm-3", "name": "Singapore Arm-3"}]


def test_entries_without_name_are_dropped():
    pool = [
        {"name": "Has-Name", "http": "x"},
        {"http": "y"},
        {"name": "", "http": "z"},
        {"name": "   ", "http": "w"},
    ]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "Has-Name", "name": "Has-Name"},
    ]


def test_non_dict_entries_are_silently_skipped():
    pool = [{"name": "A"}, "garbage", None, 42, {"name": "B"}]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "A", "name": "A"},
        {"id": "B", "name": "B"},
    ]


def test_no_credentials_leak_into_payload():
    """ADR-004 security guarantee: the payload MUST NOT contain proxy URLs,
    usernames, passwords, or auth fields. Workers never need these."""
    pool = [
        {
            "name": "Auth-Proxy",
            "http": "http://user:supersecret@host:7890",
            "https": "http://user:supersecret@host:7890",
            "user": "user",
            "password": "supersecret",
            "auth": "Basic ZWFnZXI6c2VjcmV0",
        },
    ]
    result = proxy_pool_summary_for_registry(pool)
    serialised = repr(result)

    assert result == [{"id": "Auth-Proxy", "name": "Auth-Proxy"}]

    for forbidden in ("supersecret", "user:", "Basic ", "7890", "http://"):
        assert forbidden not in serialised, (
            f"PROXY_POOL leak detected: {forbidden!r} present in payload"
        )
