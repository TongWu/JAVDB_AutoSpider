from __future__ import annotations

import sys

from javdb.pipeline.models import StepPolicy
from javdb.pipeline.step_runner import LogSink, SubprocessStepRunner


class RecordingSink:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def write_line(self, step_name: str, line: str) -> None:
        self.lines.append((step_name, line))


def test_subprocess_step_runner_streams_lines():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=10)

    result = runner.run(
        policy,
        [sys.executable, "-c", "print('one'); print('two')"],
    )

    assert result.status == "success"
    assert result.exit_code == 0
    assert sink.lines == [("demo", "one\n"), ("demo", "two\n")]


def test_subprocess_step_runner_maps_nonzero_exit_to_failed():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=10)

    result = runner.run(
        policy,
        [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"],
    )

    assert result.status == "failed"
    assert result.exit_code == 3
    assert result.failure_reason == "exit code 3"


def test_subprocess_step_runner_times_out():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=1)

    result = runner.run(
        policy,
        [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(5)"],
    )

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert "timed out" in (result.failure_reason or "")
    assert ("demo", "start\n") in sink.lines
