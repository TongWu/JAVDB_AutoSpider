from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from javdb.storage.d1_client import D1PermanentError, D1TransientError
from javdb.storage.d1_recovery import (
    RecoveryEvent,
    RecoveryPolicy,
    append_event,
    compact_replayed,
    load_latest_events,
    outbox_status,
    pending_by_ordering_key,
    replay_ordering_key,
    startup_drain,
)


def _policy(key="history:s1:seq1", ordering="history:s1", operation_type="pending_stage"):
    return RecoveryPolicy(
        logical_db="history",
        operation_type=operation_type,
        idempotency_key=key,
        ordering_key=ordering,
        recovery_allowed=True,
        max_attempts=3,
    )


def test_recovery_policy_carries_batching_permission():
    policy = RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key="history:s1:seq1",
        ordering_key="history:s1",
        recovery_allowed=True,
        max_attempts=3,
        batching_allowed=True,
    )

    event = RecoveryEvent.queued(policy, "INSERT INTO x VALUES (?)", ["a"], "timeout")

    assert event.batching_allowed is True
    assert RecoveryEvent.from_dict(event.to_dict()).batching_allowed is True


def test_recovery_event_defaults_missing_batching_permission_to_false():
    event = RecoveryEvent.from_dict(
        {
            "logical_db": "history",
            "operation_type": "pending_stage",
            "idempotency_key": "history:s1:seq1",
            "ordering_key": "history:s1",
            "recovery_allowed": True,
            "max_attempts": 3,
            "state": "queued",
            "attempt": 0,
        }
    )

    assert event.batching_allowed is False


def test_append_and_load_latest_events(tmp_path):
    path = tmp_path / "d1_recovery_outbox.jsonl"
    policy = _policy()
    append_event(
        path,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    append_event(path, RecoveryEvent.attempting(policy, attempt=1))

    latest = load_latest_events(path)

    assert latest["history:s1:seq1"].state == "attempting"
    assert latest["history:s1:seq1"].attempt == 1


def test_pending_by_ordering_key_preserves_fifo(tmp_path):
    path = tmp_path / "d1_recovery_outbox.jsonl"
    for idx in range(3):
        policy = _policy(key=f"history:s1:{idx}")
        append_event(
            path,
            RecoveryEvent.queued(
                policy,
                "INSERT INTO x VALUES (?)",
                [idx],
                "timeout",
            ),
        )

    grouped = pending_by_ordering_key(path)

    assert [event.idempotency_key for event in grouped["history:s1"]] == [
        "history:s1:0",
        "history:s1:1",
        "history:s1:2",
    ]


def test_replay_marks_success_and_compacts(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result["replayed"] == 1
    assert result["dead_lettered"] == 0
    assert calls == [("INSERT INTO x VALUES (?)", ["a"])]
    assert "replayed" in processed.read_text(encoding="utf-8")
    assert pending_by_ordering_key(outbox) == {}


def test_replay_dead_letters_permanent_failure(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )

    class Conn:
        def execute(self, sql, params=()):
            raise D1PermanentError("permanent")

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result["replayed"] == 0
    assert result["dead_lettered"] == 1
    latest = load_latest_events(outbox)
    assert latest["history:s1:seq1"].state == "dead_lettered"


def test_replay_treats_pending_stage_seq_duplicate_as_replayed(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO PendingMovieHistoryWrites "
            "(Seq, SessionId, Href, ApplyState) VALUES (?, ?, ?, 'pending')",
            ["seq1", "s1", "https://example.test/movie"],
            "timeout",
        ),
    )
    append_event(outbox, RecoveryEvent.attempting(policy, attempt=1))
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))
            raise D1PermanentError(
                "UNIQUE constraint failed: PendingMovieHistoryWrites.Seq"
            )

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result["replayed"] == 1
    assert result["dead_lettered"] == 0
    assert calls == [
        (
            "INSERT INTO PendingMovieHistoryWrites "
            "(Seq, SessionId, Href, ApplyState) VALUES (?, ?, ?, 'pending')",
            ["seq1", "s1", "https://example.test/movie"],
        )
    ]
    assert pending_by_ordering_key(outbox) == {}
    assert "replayed" in processed.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("operation_type", "table"),
    [
        ("pending_stage_movie", "PendingMovieHistoryWrites"),
        ("pending_stage_torrent", "PendingTorrentHistoryWrites"),
    ],
)
def test_replay_treats_real_pending_stage_seq_duplicates_as_replayed(
    tmp_path,
    operation_type,
    table,
):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy(operation_type=operation_type)
    sql = (
        f"INSERT INTO {table} "
        "(Seq, SessionId, Href, ApplyState) VALUES (?, ?, ?, 'pending')"
    )
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            sql,
            ["seq1", "s1", "https://example.test/item"],
            "timeout",
        ),
    )
    append_event(outbox, RecoveryEvent.attempting(policy, attempt=1))

    class Conn:
        def execute(self, sql, params=()):
            raise D1PermanentError(f"UNIQUE constraint failed: {table}.Seq")

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result == {"replayed": 1, "dead_lettered": 0}
    assert pending_by_ordering_key(outbox) == {}
    assert "replayed" in processed.read_text(encoding="utf-8")


def test_replay_transient_failure_remains_pending_for_retry(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )

    class Conn:
        def execute(self, sql, params=()):
            raise D1TransientError("temporary")

    with pytest.raises(D1TransientError, match="temporary"):
        replay_ordering_key(outbox, processed, "history:s1", Conn())

    latest = load_latest_events(outbox)
    assert latest["history:s1:seq1"].state == "attempting"
    assert pending_by_ordering_key(outbox)["history:s1"][0].params == ["a"]
    assert not processed.exists()


def test_replay_unclassified_failure_remains_pending_for_diagnosis(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )

    class Conn:
        def execute(self, sql, params=()):
            raise RuntimeError("adapter fault")

    with pytest.raises(RuntimeError, match="adapter fault"):
        replay_ordering_key(outbox, processed, "history:s1", Conn())

    latest = load_latest_events(outbox)
    assert latest["history:s1:seq1"].state == "attempting"
    assert pending_by_ordering_key(outbox)["history:s1"][0].sql is not None
    assert not processed.exists()


def test_replay_dead_letters_when_max_attempts_exceeded(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key="history:s1:seq1",
        ordering_key="history:s1",
        recovery_allowed=True,
        max_attempts=1,
    )
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    append_event(outbox, RecoveryEvent.attempting(policy, attempt=1))
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))

    result = replay_ordering_key(outbox, processed, "history:s1", Conn())

    assert result["replayed"] == 0
    assert result["dead_lettered"] == 1
    assert calls == []
    latest = load_latest_events(outbox)
    assert latest["history:s1:seq1"].state == "dead_lettered"
    assert "max attempts" in (latest["history:s1:seq1"].error or "")


def test_startup_drain_skips_dead_letters(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    append_event(
        outbox,
        RecoveryEvent.dead_lettered(policy, attempt=1, error="permanent"),
    )

    result = startup_drain(outbox, processed, connection_factory=lambda _db: object())

    assert result["replayed"] == 0
    assert result["dead_lettered"] == 1


def test_startup_drain_replays_pending_key(tmp_path):
    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))

    result = startup_drain(outbox, processed, connection_factory=lambda _db: Conn())

    assert result["replayed"] == 1
    assert result["dead_lettered"] == 0
    assert calls == [("INSERT INTO x VALUES (?)", ["a"])]


def test_compact_replayed_moves_replayed_events(tmp_path):
    active = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy(key="reports:s1:stats", ordering="reports:s1")
    append_event(
        active,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO stats VALUES (?)",
            ["s1"],
            "timeout",
        ),
    )
    append_event(active, RecoveryEvent.replayed(policy, attempt=1))

    result = compact_replayed(active, processed)

    assert result == {"active": 0, "processed": 2}
    assert active.read_text(encoding="utf-8") == ""
    assert "reports:s1:stats" in processed.read_text(encoding="utf-8")


def test_compact_replayed_keeps_active_when_processed_append_fails(
    tmp_path,
    monkeypatch,
):
    active = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy(key="reports:s1:stats", ordering="reports:s1")
    append_event(
        active,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO stats VALUES (?)",
            ["s1"],
            "timeout",
        ),
    )
    append_event(active, RecoveryEvent.replayed(policy, attempt=1))
    original = active.read_text(encoding="utf-8")

    real_open = Path.open

    def fail_processed_append(self, *args, **kwargs):
        if self == processed and args and args[0] == "a":
            raise OSError("processed append failed")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_processed_append)

    try:
        compact_replayed(active, processed)
    except OSError as exc:
        assert str(exc) == "processed append failed"
    else:
        raise AssertionError("expected processed append failure")

    assert active.read_text(encoding="utf-8") == original


def test_read_helpers_skip_malformed_jsonl_lines(tmp_path):
    path = tmp_path / "d1_recovery_outbox.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    policy = _policy()
    append_event(
        path,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )

    latest = load_latest_events(path)

    assert list(latest) == ["history:s1:seq1"]


def test_outbox_status_reports_malformed_lines(tmp_path):
    path = tmp_path / "d1_recovery_outbox.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")

    status = outbox_status(path)

    assert status["malformed_count"] == 1


def test_from_dict_parses_string_false_as_false():
    event = RecoveryEvent.from_dict(
        {
            "logical_db": "history",
            "operation_type": "pending_stage",
            "idempotency_key": "history:s1:seq1",
            "ordering_key": "history:s1",
            "recovery_allowed": "false",
            "max_attempts": 3,
            "state": "queued",
            "attempt": 0,
        }
    )

    assert event.recovery_allowed is False


def test_cli_inspect_outputs_counts(tmp_path, caplog):
    from apps.cli.db import d1_recovery as cli

    path = tmp_path / "d1_recovery_outbox.jsonl"
    policy = _policy()
    append_event(
        path,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )

    with caplog.at_level(logging.INFO, logger="apps.cli.db.d1_recovery"):
        rc = cli.main(["inspect", "--outbox", str(path)])

    assert rc == 1
    log_output = caplog.text
    assert "Pending events" in log_output


def test_cli_inspect_dead_lettered_outputs_blocking_state(tmp_path, capsys):
    from apps.cli.db import d1_recovery as cli

    path = tmp_path / "d1_recovery_outbox.jsonl"
    policy = _policy()
    append_event(
        path,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    append_event(
        path,
        RecoveryEvent.dead_lettered(policy, attempt=3, error="max attempts"),
    )

    rc = cli.main(["inspect", "--json", "--outbox", str(path)])

    assert rc == 1
    output = json.loads(capsys.readouterr().out)
    assert output["pending_count"] == 0
    assert output["dead_lettered_count"] == 1
    assert "history:s1" in output["dead_lettered_groups"]


def test_cli_inspect_malformed_line_exits_one(tmp_path, capsys):
    from apps.cli.db import d1_recovery as cli

    path = tmp_path / "d1_recovery_outbox.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")

    rc = cli.main(["inspect", "--json", "--outbox", str(path)])

    assert rc == 1
    output = json.loads(capsys.readouterr().out)
    assert output["pending_count"] == 0
    assert output["dead_lettered_count"] == 0
    assert output["malformed_count"] == 1


def test_cli_inspect_empty_json_exits_zero(tmp_path, capsys):
    from apps.cli.db import d1_recovery as cli

    path = tmp_path / "missing.jsonl"

    rc = cli.main(["inspect", "--json", "--outbox", str(path)])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pending_count"] == 0
    assert output["dead_lettered_count"] == 0
    assert output["malformed_count"] == 0
    assert output["pending_groups"] == {}


def test_cli_replay_ordering_key_marks_success(tmp_path, monkeypatch, capsys):
    from apps.cli.db import d1_recovery as cli

    outbox = tmp_path / "d1_recovery_outbox.jsonl"
    processed = tmp_path / "d1_recovery_outbox.processed.jsonl"
    policy = _policy()
    append_event(
        outbox,
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )
    calls = []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, list(params)))

    monkeypatch.setattr(cli, "_make_connection_for_key", lambda *_args: Conn())

    rc = cli.main(
        [
            "replay",
            "--json",
            "--outbox",
            str(outbox),
            "--processed",
            str(processed),
            "--ordering-key",
            "history:s1",
        ]
    )

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["replayed"] == 1
    assert calls == [("INSERT INTO x VALUES (?)", ["a"])]


def test_cli_startup_drain_empty_json_exits_zero(tmp_path, monkeypatch, capsys):
    from apps.cli.db import d1_recovery as cli
    import javdb.storage.d1_client as d1_client

    monkeypatch.setattr(
        d1_client,
        "make_d1_connection",
        lambda _logical_db: object(),
    )

    rc = cli.main(
        [
            "startup-drain",
            "--json",
            "--outbox",
            str(tmp_path / "missing.jsonl"),
        ]
    )

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["replayed"] == 0
    assert output["dead_lettered"] == 0
