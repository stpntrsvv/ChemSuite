import json
import math
import multiprocessing as mp
import os
import signal
import time
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path

from chem_suite.core.artifacts import write_json
from chem_suite.core.contracts import JobEvent, JobRecord, JobRequest, JobState
from chem_suite.core.registry import ModuleRegistry
from chem_suite.core.worker import execute_job


@dataclass
class _Running:
    process: object
    receiver: object
    cancelled: object
    stop_started: float | None = None
    stop_state: JobState | None = None
    terminated_at: float | None = None


class JobService:
    """Single-owner, nonblocking supervisor. Each job gets its own spawn process.

    Qt drives poll() with a timer; a CLI can drive it in a loop. No Qt, NumPy or
    module implementation is imported by the supervisor. No shared process pool
    can be poisoned by a native crash. max_per_module leaves room for peers.
    """

    def __init__(
        self,
        registry: ModuleRegistry,
        workspace: str | Path,
        *,
        max_active: int = 2,
        max_per_module: int = 1,
        cancel_grace: float = 0.5,
    ) -> None:
        if max_active < 1 or max_per_module < 1 or cancel_grace < 0:
            raise ValueError("Invalid scheduler limits")
        self.registry = registry
        self.workspace = Path(workspace).resolve()
        (self.workspace / "jobs").mkdir(parents=True, exist_ok=True)
        self.max_active = max_active
        self.max_per_module = max_per_module
        self.cancel_grace = cancel_grace
        self._context = mp.get_context("spawn")
        self._records: dict[str, JobRecord] = {}
        self._pending: deque[str] = deque()
        self._running: dict[str, _Running] = {}
        self._events: deque[JobEvent] = deque()
        self._closed = False

    def submit(self, request: JobRequest) -> JobRecord:
        if self._closed:
            raise RuntimeError("Job service is closed")
        self.registry.action(request.module, request.action)
        if not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0:
            raise ValueError("Timeout must be finite and positive")
        # Detach caller-owned settings and reject data that cannot cross the process boundary.
        config = json.loads(json.dumps(request.config, allow_nan=False))
        if not isinstance(config, dict):
            raise ValueError("Job config must be an object")
        request = JobRequest(
            request.module,
            request.action,
            tuple(str(Path(p).resolve()) for p in request.inputs),
            config,
            request.timeout_seconds,
        )
        record = JobRecord(uuid.uuid4().hex, request)
        self._records[record.id] = record
        self._pending.append(record.id)
        self._emit(record)
        return record

    def records(self) -> tuple[JobRecord, ...]:
        return tuple(self._records.values())

    def record(self, job_id: str) -> JobRecord:
        return self._records[job_id]

    def _emit(self, record: JobRecord) -> None:
        self._events.append(JobEvent(record.id, record.state, record.progress, record.message))

    def _finish(self, record: JobRecord, state: JobState, message: str, error: str = "") -> None:
        if record.state.terminal:
            return
        from chem_suite.core.publications import finish

        try:
            finish(self.workspace / "jobs" / record.id, state == JobState.SUCCEEDED)
        except Exception as exc:
            state, message, error = JobState.FAILED, "Export publication failed", str(exc)
        record.state, record.message, record.error = state, message, error
        record.finished_at = time.monotonic()
        if state == JobState.SUCCEEDED:
            record.progress = 1.0
        else:
            record.result_path = None
            (self.workspace / "jobs" / record.id / "result.json").unlink(missing_ok=True)
        write_json(self.workspace / "jobs" / record.id / "job.json", asdict(record))
        self._emit(record)

    def cancel(self, job_id: str) -> None:
        record = self.record(job_id)
        if record.state.terminal or record.state == JobState.CANCELLING:
            return
        if record.state == JobState.QUEUED:
            self._pending.remove(job_id)
            self._finish(record, JobState.CANCELLED, "Отменено до запуска")
        else:
            self._stop(job_id, JobState.CANCELLED)

    def _stop(self, job_id: str, final_state: JobState) -> None:
        running = self._running[job_id]
        if running.stop_started is not None:
            return
        running.cancelled.set()
        running.stop_started = time.monotonic()
        running.stop_state = final_state
        record = self.record(job_id)
        record.state = JobState.CANCELLING
        record.message = "Остановка по таймауту" if final_state == JobState.TIMED_OUT else "Отмена расчёта"
        self._emit(record)

    def _drain(self, job_id: str, running: _Running) -> bool:
        record = self.record(job_id)
        # Bounded work per tick keeps a misbehaving progress producer from starving Qt.
        exhausted = True
        for _ in range(100):
            try:
                if not running.receiver.poll():
                    exhausted = False
                    break
                event = running.receiver.recv()
            except (EOFError, OSError):
                exhausted = False
                break
            if record.state.terminal or running.stop_started is not None:
                continue
            if event["type"] == "progress":
                record.progress = event["progress"]
                record.message = event["message"]
                self._emit(record)
            elif event["type"] == "success":
                expected = self.workspace / "jobs" / job_id / "result.json"
                if event["result_path"] != str(expected) or not expected.is_file():
                    self._finish(record, JobState.FAILED, "Некорректный результат процесса")
                else:
                    record.result_path = str(expected)
                    self._finish(record, JobState.SUCCEEDED, "Готово")
            elif event["type"] == "error":
                self._finish(record, JobState.FAILED, "Ошибка расчёта", event["message"])
            elif event["type"] == "cancelled":
                self._finish(record, JobState.CANCELLED, "Расчёт отменён")
        # A dead process can still have hundreds of messages buffered before
        # its terminal event. Continue bounded draining on the next Qt tick.
        return exhausted and not record.state.terminal and running.receiver.poll()

    @staticmethod
    def _terminate(running, *, hard=False):
        if hasattr(os, "killpg"):
            try:
                os.killpg(running.process.pid, signal.SIGKILL if hard else signal.SIGTERM)
                return
            except ProcessLookupError:
                pass
        if running.process.is_alive():
            running.process.kill() if hard else running.process.terminate()

    def poll(self) -> list[JobEvent]:
        now = time.monotonic()
        for job_id, running in list(self._running.items()):
            record = self.record(job_id)
            if (
                not record.state.terminal
                and running.stop_started is None
                and now - record.started_at >= record.request.timeout_seconds
            ):
                self._stop(job_id, JobState.TIMED_OUT)
            self._drain(job_id, running)
            if running.process.is_alive():
                if running.stop_started is not None:
                    if running.terminated_at is None and now - running.stop_started >= self.cancel_grace:
                        self._terminate(running)
                        running.terminated_at = now
                    elif running.terminated_at is not None and now - running.terminated_at >= 0.5:
                        self._terminate(running, hard=True)
                continue
            pending = self._drain(job_id, running)
            running.process.join(timeout=0)
            if running.stop_state is not None:
                self._terminate(running, hard=True)
                state = running.stop_state
                self._finish(
                    record, state, "Истёк лимит времени" if state == JobState.TIMED_OUT else "Расчёт отменён"
                )
            elif pending:
                continue
            elif not record.state.terminal:
                self._finish(
                    record,
                    JobState.FAILED,
                    "Рабочий процесс аварийно завершился",
                    f"Worker exited with code {running.process.exitcode}",
                )
            running.receiver.close()
            running.process.close()
            del self._running[job_id]

        active_modules = Counter(self.record(j).request.module for j in self._running)
        for job_id in tuple(self._pending):
            if len(self._running) >= self.max_active:
                break
            record = self.record(job_id)
            if active_modules[record.request.module] >= self.max_per_module:
                continue
            self._pending.remove(job_id)
            receiver, sender = self._context.Pipe(duplex=False)
            cancelled = self._context.Event()
            module = self.registry.module(record.request.module)
            handler = self.registry.action(module.id, record.request.action).handler
            directory = self.workspace / "jobs" / job_id
            directory.mkdir(parents=True, exist_ok=True)
            process = self._context.Process(
                target=execute_job,
                args=(record.request, handler, module.version, str(directory), cancelled, sender),
                name=f"chem-suite-{module.id}-{job_id[:8]}",
            )
            try:
                process.start()
            except Exception as exc:
                receiver.close()
                sender.close()
                process.close()
                self._finish(record, JobState.FAILED, "Не удалось запустить процесс", str(exc))
                continue
            sender.close()
            record.state, record.started_at, record.pid = JobState.RUNNING, time.monotonic(), process.pid
            record.message = "Запуск расчёта"
            self._running[job_id] = _Running(process, receiver, cancelled)
            active_modules[module.id] += 1
            self._emit(record)
        events = list(self._events)
        self._events.clear()
        return events

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for record in self.records():
            self.cancel(record.id)
        deadline = time.monotonic() + self.cancel_grace + 2.0
        while self._running and time.monotonic() < deadline:
            self.poll()
            time.sleep(0.01)
        for job_id, running in self._running.items():
            self._terminate(running, hard=True)
            running.process.join(timeout=1)
            running.receiver.close()
            if not running.process.is_alive():
                running.process.close()
            self._finish(self.record(job_id), JobState.CANCELLED, "Расчёт отменён")
        self._running.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
