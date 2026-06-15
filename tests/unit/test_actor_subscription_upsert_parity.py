"""Pin ActorSubscription UPSERT SQL parity with the TS Worker (ADR-054 WS2)."""

import re

from javdb.storage.repos.subscription_repo import ACTOR_SUBSCRIPTION_UPSERT_SQL

CANONICAL = (
    "INSERT INTO ActorSubscription "
    "(actor_href, actor_name, active, created_at, updated_at) "
    "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
    "ON CONFLICT(actor_href) DO UPDATE SET "
    "actor_name = excluded.actor_name, active = excluded.active, "
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_python_upsert_matches_canonical():
    assert _norm(ACTOR_SUBSCRIPTION_UPSERT_SQL) == CANONICAL
