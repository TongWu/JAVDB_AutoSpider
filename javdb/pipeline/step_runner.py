from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Protocol, Sequence

from javdb.pipeline.models import StepPolicy, StepResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LogSink(Protocol):
    def write_line(self, step_name: str, line: str) -> None:
        pass


class ConsoleAndFileLogSink:
    def __init__(self):
        self._file_handler = None
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.FileHandler):
                self._file_handler = handler
                break

    def write_line(self, step_name: str, line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()
        if self._file_handler is not None:
            self._file_handler.stream.write(line)
            self._file_handler.stream.flush()


class SubprocessStepRunner:
    def __init__(self, *, log_sink: LogSink | None = None):
        self._log_sink = log_sink or ConsoleAndFileLogSink()

    def run(
        self,
        policy: StepPolicy,
        command: Sequence[str],
        *,
        result_path: str | None = None,
    ) -> StepResult:
        started_at = _utc_now_iso()
        deadline = time.monotonic() + policy.timeout_sec
        command_list = list(command)
        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        stdout_finished = object()
        lines: queue.Queue[str | object] = queue.Queue()

        def _read_stdout() -> None:
            try:
                if process.stdout:
                    for line in process.stdout:
                        lines.put(line)
            finally:
                lines.put(stdout_finished)

        reader = threading.Thread(target=_read_stdout, daemon=True)
        reader.start()
        try:
            stdout_done = False
            while True:
                try:
                    item = lines.get(timeout=0.05)
                except queue.Empty:
                    item = None
                if item is stdout_finished:
                    stdout_done = True
                elif isinstance(item, str) and item:
                    self._log_sink.write_line(policy.name, item)

                if time.monotonic() > deadline and process.poll() is None:
                    break
                if stdout_done and process.poll() is not None:
                    break

            if time.monotonic() > deadline and process.poll() is None:
                process.kill()
                process.wait()
                return StepResult(
                    name=policy.name,
                    status="timed_out",
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=command_list,
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=None,
                    failure_reason=f"timed out after {policy.timeout_sec}s",
                    result_path=result_path,
                )
            return_code = process.wait()
            status = "success" if return_code == 0 else "failed"
            return StepResult(
                name=policy.name,
                status=status,
                required=policy.required,
                run_on_failure=policy.run_on_failure,
                command=command_list,
                started_at=started_at,
                finished_at=_utc_now_iso(),
                exit_code=return_code,
                failure_reason=None if return_code == 0 else f"exit code {return_code}",
                result_path=result_path,
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
