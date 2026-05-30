"""Unit tests for ContentFilterRepo."""

from __future__ import annotations

import sqlite3

import pytest

from javdb.spider.services.content_filter import Rule
from javdb.storage.repos.content_filter_repo import ContentFilterRepo


_CONTENT_FILTER_DDL = """
CREATE TABLE ContentFilterRule (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension  TEXT NOT NULL,
    mode       TEXT NOT NULL,
    value      TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_content_filter_enabled ON ContentFilterRule(enabled, dimension);
"""


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(_CONTENT_FILTER_DDL)
    yield connection
    connection.close()


def test_add_rule_returns_id_and_lists_rule(conn: sqlite3.Connection) -> None:
    repo = ContentFilterRepo(conn)

    rule_id = repo.add_rule("actor", "exclude", "/actors/abc")

    assert rule_id == 1
    assert repo.list_rules() == [
        Rule(
            id=rule_id,
            dimension="actor",
            mode="exclude",
            value="/actors/abc",
            enabled=True,
        )
    ]


def test_load_rules_returns_enabled_rules_only(conn: sqlite3.Connection) -> None:
    repo = ContentFilterRepo(conn)
    enabled_id = repo.add_rule("tag", "include", "字幕")
    disabled_id = repo.add_rule("gender", "require_lead", "female")
    repo.set_enabled(disabled_id, False)

    assert repo.load_rules() == [
        Rule(
            id=enabled_id,
            dimension="tag",
            mode="include",
            value="字幕",
            enabled=True,
        )
    ]


def test_list_rules_includes_disabled_rules(conn: sqlite3.Connection) -> None:
    repo = ContentFilterRepo(conn)
    first_id = repo.add_rule("actor", "exclude", "Actor A")
    second_id = repo.add_rule("gender", "exclude_all_male", "")
    repo.set_enabled(second_id, False)

    assert repo.list_rules() == [
        Rule(first_id, "actor", "exclude", "Actor A", True),
        Rule(second_id, "gender", "exclude_all_male", "", False),
    ]


def test_remove_rule_deletes_rule(conn: sqlite3.Connection) -> None:
    repo = ContentFilterRepo(conn)
    rule_id = repo.add_rule("actor", "exclude", "Actor A")

    repo.remove_rule(rule_id)

    assert repo.list_rules() == []


def test_set_enabled_toggles_rule(conn: sqlite3.Connection) -> None:
    repo = ContentFilterRepo(conn)
    rule_id = repo.add_rule("tag", "include", "高清")

    repo.set_enabled(rule_id, False)
    assert repo.list_rules()[0].enabled is False

    repo.set_enabled(rule_id, True)
    assert repo.list_rules()[0].enabled is True
