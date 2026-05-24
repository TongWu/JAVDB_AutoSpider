from __future__ import annotations

import json
from pathlib import Path

from javdb.storage.d1_recovery import (
    RecoveryEvent,
    RecoveryPolicy,
    append_event,
    compact_replayed,
    load_latest_events,
    outbox_status,
    pending_by_ordering_key,
)


def _policy(key="history:s1:seq1", ordering="history:s1"):
    return RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key=key,
        ordering_key=ordering,
        recovery_allowed=True,
        max_attempts=3,
    )


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


def test_cli_inspect_outputs_counts(tmp_path, capsys):
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

    rc = cli.main(["inspect", "--outbox", str(path)])

    assert rc == 1
    assert "history:s1" in capsys.readouterr().out


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
