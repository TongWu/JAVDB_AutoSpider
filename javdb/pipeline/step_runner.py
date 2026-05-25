from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from dataclasses import replace
from typing import Callable, NamedTuple, Protocol, Sequence

from javdb.pipeline.models import StepPolicy, StepResult
from javdb.spider.app.options import SpiderRunOptions

_PROCESS_STOP_GRACE_SEC = 1.0
_READER_JOIN_GRACE_SEC = 1.0
_IN_PROCESS_CANCEL_GRACE_SEC = 0.2


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _with_cancel_event(options: SpiderRunOptions, cancel_event: threading.Event) -> SpiderRunOptions:
    try:
        return replace(options, cancel_event=cancel_event)
    except TypeError:
        setattr(options, "cancel_event", cancel_event)
        return options


class LogSink(Protocol):
    def write_line(self, step_name: str, line: str) -> None:
        pass


class SpiderStepRunResult(Protocol):
    exit_code: int
    failure_reason: str | None


class _SpiderThreadOutcome(NamedTuple):
    spider_result: SpiderStepRunResult | None
    exception: BaseException | None


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


def _process_group_kwargs() -> dict[str, object]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _taskkill_tree(pid: int) -> None:
    taskkill_exe = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "taskkill.exe",
    )
    subprocess.run(
        [taskkill_exe, "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        _taskkill_tree(process.pid)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        _taskkill_tree(process.pid)
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


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
            **_process_group_kwargs(),
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

        def _drain_available_output() -> bool:
            stdout_done = False
            while True:
                try:
                    item = lines.get_nowait()
                except queue.Empty:
                    break
                if item is stdout_finished:
                    stdout_done = True
                elif isinstance(item, str) and item:
                    self._log_sink.write_line(policy.name, item)
            return stdout_done

        def _terminate_and_wait(*, wait_for_stdout: bool = False) -> None:
            _terminate_process_group(process)
            if process.poll() is None:
                try:
                    process.wait(timeout=_PROCESS_STOP_GRACE_SEC)
                except subprocess.TimeoutExpired:
                    _kill_process_group(process)
                    process.wait()
            if wait_for_stdout:
                reader.join(timeout=_READER_JOIN_GRACE_SEC)
                if reader.is_alive():
                    _kill_process_group(process)
                    reader.join(timeout=_READER_JOIN_GRACE_SEC)

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

                process_done = process.poll() is not None
                if time.monotonic() > deadline and (not process_done or not stdout_done):
                    break
                if stdout_done and process_done:
                    break

            if time.monotonic() > deadline and (process.poll() is None or not stdout_done):
                _terminate_and_wait(wait_for_stdout=True)
                # Drain once for queued lines and once more for the reader sentinel after join.
                _drain_available_output()
                _drain_available_output()
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
                _terminate_and_wait()
            _drain_available_output()
            reader.join(timeout=_READER_JOIN_GRACE_SEC)
            _drain_available_output()


class InProcessSpiderStepRunner:
    def __init__(self, *, run_spider: Callable[[SpiderRunOptions], SpiderStepRunResult]):
        self._run_spider = run_spider

    def run(
        self,
        policy: StepPolicy,
        *,
        options: SpiderRunOptions,
        command_label: Sequence[str],
    ) -> tuple[StepResult, SpiderStepRunResult | None]:
        started_at = _utc_now_iso()
        command_list = list(command_label)
        result_path = getattr(options, "result_json", None)
        cancel_event = threading.Event()
        options = _with_cancel_event(options, cancel_event)

        outcome_queue: queue.Queue[_SpiderThreadOutcome] = queue.Queue(maxsize=1)

        def _run_spider_thread() -> None:
            try:
                outcome_queue.put(_SpiderThreadOutcome(self._run_spider(options), None))
            except BaseException as exc:
                outcome_queue.put(_SpiderThreadOutcome(None, exc))

        runner_thread = threading.Thread(target=_run_spider_thread, daemon=True)
        runner_thread.start()
        runner_thread.join(timeout=policy.timeout_sec)
        if runner_thread.is_alive():
            cancel_event.set()
            runner_thread.join(timeout=_IN_PROCESS_CANCEL_GRACE_SEC)
            return (
                StepResult(
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
                ),
                None,
            )

        outcome = outcome_queue.get()
        if outcome.exception is not None:
            exc = outcome.exception
            if isinstance(exc, SystemExit):
                code = self._system_exit_code(exc)
                return (
                    StepResult(
                        name=policy.name,
                        status="success" if code == 0 else "failed",
                        required=policy.required,
                        run_on_failure=policy.run_on_failure,
                        command=command_list,
                        started_at=started_at,
                        finished_at=_utc_now_iso(),
                        exit_code=code,
                        failure_reason=None if code == 0 else f"exit code {code}",
                        result_path=result_path,
                    ),
                    None,
                )
            if not isinstance(exc, Exception):
                raise exc
            return (
                StepResult(
                    name=policy.name,
                    status="failed",
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=command_list,
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=1,
                    failure_reason=str(exc),
                    result_path=result_path,
                ),
                None,
            )

        spider_result = outcome.spider_result
        if spider_result is None:
            return (
                StepResult(
                    name=policy.name,
                    status="failed",
                    required=policy.required,
                    run_on_failure=policy.run_on_failure,
                    command=command_list,
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                    exit_code=1,
                    failure_reason="spider did not return a result",
                    result_path=result_path,
                ),
                None,
            )

        exit_code = spider_result.exit_code
        failure_reason = spider_result.failure_reason
        if exit_code != 0 and failure_reason is None:
            failure_reason = f"exit code {exit_code}"
        return (
            StepResult(
                name=policy.name,
                status="success" if exit_code == 0 else "failed",
                required=policy.required,
                run_on_failure=policy.run_on_failure,
                command=command_list,
                started_at=started_at,
                finished_at=_utc_now_iso(),
                exit_code=exit_code,
                failure_reason=None if exit_code == 0 else failure_reason,
                result_path=result_path,
            ),
            spider_result,
        )

    @staticmethod
    def _system_exit_code(exc: SystemExit) -> int:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1
