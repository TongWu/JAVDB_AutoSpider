from __future__ import annotations

import sys
import time

from javdb.pipeline.models import StepPolicy
from javdb.pipeline.step_runner import SubprocessStepRunner


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


def test_subprocess_step_runner_timeout_cleans_up_descendant_stdout(tmp_path):
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=1)
    alive_marker = tmp_path / "descendant-alive"
    command = [
        sys.executable,
        "-c",
        (
            "import pathlib, subprocess, sys, time; "
            f"marker = {str(alive_marker)!r}; "
            "child_code = "
            "\"import pathlib, time; "
            "print('child-start', flush=True); "
            "time.sleep(2); "
            "pathlib.Path(%r).write_text('alive', encoding='utf-8'); "
            "time.sleep(30)\" % marker; "
            "subprocess.Popen([sys.executable, '-c', child_code]); "
            "print('parent-start', flush=True); "
            "time.sleep(30)"
        ),
    ]

    started_at = time.monotonic()
    result = runner.run(policy, command)
    elapsed = time.monotonic() - started_at

    assert result.status == "timed_out"
    assert elapsed < 5
    assert ("demo", "parent-start\n") in sink.lines
    assert ("demo", "child-start\n") in sink.lines
    time.sleep(2.5)
    assert not alive_marker.exists()


def test_subprocess_step_runner_times_out_when_parent_exits_but_descendant_keeps_stdout():
    sink = RecordingSink()
    runner = SubprocessStepRunner(log_sink=sink)
    policy = StepPolicy(name="demo", required=True, timeout_sec=1)
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', "
            "\"import time; print('child-start', flush=True); time.sleep(3)\"]); "
            "print('parent-done', flush=True)"
        ),
    ]

    started_at = time.monotonic()
    result = runner.run(policy, command)
    elapsed = time.monotonic() - started_at

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert elapsed < 2.5
    assert ("demo", "parent-done\n") in sink.lines
    assert ("demo", "child-start\n") in sink.lines
