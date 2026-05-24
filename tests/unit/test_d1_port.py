from __future__ import annotations

import json

import pytest

from javdb.storage.d1_client import D1PermanentError, D1TransientError
from javdb.storage.d1_port import D1AccessPort, D1PortConfig


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakePoster:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _port(poster, *, max_retries=2, batch_limit=50):
    return D1AccessPort(
        url="https://example.test/query",
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        config=D1PortConfig(
            timeout=3,
            batch_limit=batch_limit,
            max_retries=max_retries,
            retry_base_sec=0,
            retry_max_sleep_sec=0,
        ),
        post_request=poster,
        sleep=lambda _seconds: None,
        jitter=lambda: 0,
    )


def test_execute_posts_single_statement_body():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 1}, "results": []}],
                }
            )
        ]
    )
    port = _port(poster)

    cursors = port.execute("SELECT 1", [])

    assert len(cursors) == 1
    assert poster.calls[0]["json"] == {"sql": "SELECT 1", "params": []}
    assert port.summary()["http_posts"] == 1


def test_transient_error_retries_then_succeeds():
    poster = FakePoster(
        [
            FakeResponse(
                status_code=500,
                payload={"success": False, "errors": [{"message": "temporary"}]},
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"n": 1}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster, max_retries=2)

    cursors = port.execute("SELECT 1", [])

    assert cursors[0].fetchone() == {"n": 1}
    assert len(poster.calls) == 2
    assert port.summary()["retries"] == 1
    assert port.summary()["retry_successes"] == 1


def test_permanent_error_does_not_retry():
    poster = FakePoster(
        [
            FakeResponse(
                status_code=400,
                payload={"success": False, "errors": [{"message": "no such table: x"}]},
            )
        ]
    )
    port = _port(poster, max_retries=3)

    with pytest.raises(D1PermanentError):
        port.execute("SELECT * FROM x", [])

    assert len(poster.calls) == 1
    assert port.summary()["permanent_errors"] == 1


def test_transient_error_exhaustion_raises_transient():
    poster = FakePoster(
        [
            FakeResponse(
                status_code=429,
                payload={"success": False, "errors": [{"message": "overloaded"}]},
            ),
            FakeResponse(
                status_code=429,
                payload={"success": False, "errors": [{"message": "overloaded"}]},
            ),
        ]
    )
    port = _port(poster, max_retries=2)

    with pytest.raises(D1TransientError):
        port.execute("SELECT 1", [])

    assert port.summary()["transient_errors"] == 2


def test_schema_metadata_queries_are_cached_with_cloned_cursor_hits():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Id"}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster)

    first_cursor = port.execute('PRAGMA table_info("MovieHistory")')[0]
    second_cursor = port.execute('PRAGMA table_info("MovieHistory")')[0]
    first = first_cursor.fetchall()
    second = second_cursor.fetchall()

    assert first == [{"name": "Id"}]
    assert second == [{"name": "Id"}]
    assert first_cursor is not second_cursor
    assert len(poster.calls) == 1
    assert port.summary()["schema_cache_hits"] == 1
    assert port.summary()["schema_cache_misses"] == 1


def test_schema_cache_hit_does_not_reuse_mutated_cursor_rows():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Id"}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster)

    first_cursor = port.execute('PRAGMA table_info("MovieHistory")')[0]
    first_rows = first_cursor.fetchall()
    first_rows.append({"name": "Injected"})
    second_cursor = port.execute('PRAGMA table_info("MovieHistory")')[0]

    assert second_cursor.fetchall() == [{"name": "Id"}]
    assert first_cursor is not second_cursor


def test_business_select_is_not_cached():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"n": 1}]}
                    ],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"n": 2}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster)

    assert port.execute("SELECT COUNT(*) AS n FROM MovieHistory")[0].fetchone() == {
        "n": 1
    }
    assert port.execute("SELECT COUNT(*) AS n FROM MovieHistory")[0].fetchone() == {
        "n": 2
    }
    assert len(poster.calls) == 2
    assert port.summary()["schema_cache_hits"] == 0


def test_ddl_with_leading_line_comment_clears_schema_cache():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Id"}]}
                    ],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 0}, "results": []}],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Other"}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster)

    assert port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall() == [
        {"name": "Id"}
    ]
    port.execute("-- planned migration\nCREATE TABLE x(id INTEGER)")
    assert port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall() == [
        {"name": "Other"}
    ]

    assert len(poster.calls) == 3


def test_ddl_with_leading_block_comment_clears_schema_cache():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Id"}]}
                    ],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 0}, "results": []}],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Added"}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster)

    assert port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall() == [
        {"name": "Id"}
    ]
    port.execute("/* planned migration */\nALTER TABLE MovieHistory ADD COLUMN y TEXT")
    assert port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall() == [
        {"name": "Added"}
    ]

    assert len(poster.calls) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE sqlite_master SET sql = sql WHERE name = 'MovieHistory'",
        "DELETE FROM sqlite_master WHERE name = 'MovieHistory'",
        "INSERT INTO sqlite_master(type, name) VALUES ('table', 'MovieHistory')",
    ],
)
def test_direct_sqlite_master_mutations_clear_schema_cache(sql):
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Id"}]}
                    ],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 1}, "results": []}],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 0}, "results": [{"name": "Changed"}]}
                    ],
                }
            ),
        ]
    )
    port = _port(poster)

    assert port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall() == [
        {"name": "Id"}
    ]
    port.execute(sql)
    assert port.execute('PRAGMA table_info("MovieHistory")')[0].fetchall() == [
        {"name": "Changed"}
    ]

    assert len(poster.calls) == 3


def test_executemany_splits_by_batch_limit_and_updates_metrics():
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 1}, "results": []},
                        {"meta": {"changes": 1}, "results": []},
                    ],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [
                        {"meta": {"changes": 1}, "results": []},
                        {"meta": {"changes": 1}, "results": []},
                    ],
                }
            ),
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 1}, "results": []}],
                }
            ),
        ]
    )
    port = _port(poster, batch_limit=2)

    cursors = port.executemany(
        "INSERT INTO MovieHistory(Id) VALUES (?)",
        [(1,), (2,), (3,), (4,), (5,)],
    )

    assert len(cursors) == 5
    assert [call["json"] for call in poster.calls] == [
        {
            "batch": [
                {"sql": "INSERT INTO MovieHistory(Id) VALUES (?)", "params": [1]},
                {"sql": "INSERT INTO MovieHistory(Id) VALUES (?)", "params": [2]},
            ]
        },
        {
            "batch": [
                {"sql": "INSERT INTO MovieHistory(Id) VALUES (?)", "params": [3]},
                {"sql": "INSERT INTO MovieHistory(Id) VALUES (?)", "params": [4]},
            ]
        },
        {
            "batch": [
                {"sql": "INSERT INTO MovieHistory(Id) VALUES (?)", "params": [5]},
            ]
        },
    ]
    assert port.summary()["batches"] == 3
    assert port.summary()["batch_statements"] == 5
    assert port.summary()["sql_statements"] == 5


def test_write_summary_creates_formatted_json(tmp_path):
    poster = FakePoster(
        [
            FakeResponse(
                payload={
                    "success": True,
                    "result": [{"meta": {"changes": 0}, "results": []}],
                }
            )
        ]
    )
    port = _port(poster)
    port.execute("SELECT 1", [])

    path = tmp_path / "d1_port_summary.json"
    port.write_summary(path)

    content = path.read_text(encoding="utf-8")
    data = json.loads(content)
    assert data["http_posts"] == 1
    assert data["sql_statements"] == 1
    assert content.endswith("\n")
    assert "\n  " in content
