"""Completed-process messages must still be drained without blocking the UI."""

from collections import deque
import time
from types import SimpleNamespace
import pytest
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService, _Running
from tests.test_isolation import registry


@pytest.mark.parametrize("terminal", ["success", "error", "crash"])
def test_dead_worker_with_buffered_progress_preserves_terminal_message(tmp_path, terminal):
    with JobService(registry(), tmp_path) as jobs:
        record = jobs.submit(JobRequest("cycling", "run", ()))
        jobs._pending.remove(record.id)
        record.state, record.started_at = JobState.RUNNING, time.monotonic()
        directory = tmp_path / "jobs" / record.id
        directory.mkdir()
        path = directory / "result.json"
        path.write_text('{"schema_version":1}')
        messages = deque({"type": "progress", "progress": 0.1, "message": "sample"} for _ in range(350))
        if terminal == "success":
            messages.append({"type": "success", "result_path": str(path)})
        elif terminal == "error":
            messages.append({"type": "error", "message": "expected failure"})
        receiver = SimpleNamespace(poll=lambda: bool(messages), recv=messages.popleft, close=lambda: None)
        process = SimpleNamespace(
            is_alive=lambda: False, join=lambda **_: None, close=lambda: None, exitcode=0, pid=987654321
        )
        running = _Running(process, receiver, SimpleNamespace(set=lambda: None))
        jobs._running[record.id] = running
        jobs.poll()
        assert not record.state.terminal  # bounded 200-message drain, still has a backlog
        jobs.poll()
        assert record.state == (JobState.SUCCEEDED if terminal == "success" else JobState.FAILED)
        assert bool(record.result_path) == (terminal == "success")
        assert path.exists() == (terminal == "success")
