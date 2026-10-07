import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chem_suite.core.contracts import ActionSpec, JobRequest, JobState, ModuleSpec
from chem_suite.core.jobs import JobService
from chem_suite.core.registry import ModuleRegistry


def registry():
    registry = ModuleRegistry()
    for name in ("eis", "cycling"):
        registry.register(
            ModuleSpec(name, name, "test", (ActionSpec("run", "run", "tests.support_jobs:run"),))
        )
    return registry


def wait(jobs, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        jobs.poll()
        if time.monotonic() > deadline:
            raise AssertionError([(r.state, r.error) for r in jobs.records()])
        time.sleep(0.01)


@pytest.mark.parametrize("failure", ["error", "crash"])
def test_failed_eis_does_not_stop_cycling_or_next_eis(tmp_path, failure):
    with JobService(registry(), tmp_path) as jobs:
        broken = jobs.submit(JobRequest("eis", "run", (), {"mode": failure}))
        peer = jobs.submit(JobRequest("cycling", "run", (), {"seconds": 0.6}))
        wait(jobs, lambda: broken.state.terminal and peer.state.terminal)
        assert broken.state == JobState.FAILED
        assert broken.result_path is None
        assert not (tmp_path / "jobs" / broken.id / "result.json").exists()
        assert peer.state == JobState.SUCCEEDED
        assert broken.pid != peer.pid
        again = jobs.submit(JobRequest("eis", "run", ()))
        wait(jobs, lambda: again.state.terminal)
        assert again.state == JobState.SUCCEEDED


def test_hard_cancellation_preserves_peer(tmp_path):
    with JobService(registry(), tmp_path, cancel_grace=0.1) as jobs:
        long = jobs.submit(JobRequest("eis", "run", (), {"seconds": 30}))
        peer = jobs.submit(JobRequest("cycling", "run", (), {"seconds": 0.8}))
        wait(jobs, lambda: long.message == "entered")
        started = time.monotonic()
        jobs.cancel(long.id)
        wait(jobs, lambda: long.state.terminal and peer.state.terminal)
        assert time.monotonic() - started < 3
        assert long.state == JobState.CANCELLED
        assert peer.state == JobState.SUCCEEDED


def test_timeout_and_module_fairness(tmp_path):
    with JobService(registry(), tmp_path, cancel_grace=0.1) as jobs:
        long = jobs.submit(JobRequest("eis", "run", (), {"seconds": 30}, 0.7))
        queued = jobs.submit(JobRequest("eis", "run", ()))
        peer = jobs.submit(JobRequest("cycling", "run", ()))
        jobs.poll()
        assert long.state == JobState.RUNNING
        assert queued.state == JobState.QUEUED
        assert peer.state == JobState.RUNNING
        jobs.cancel(queued.id)
        assert queued.state == JobState.CANCELLED and queued.pid is None
        wait(jobs, lambda: long.state.terminal and peer.state.terminal)
        assert long.state == JobState.TIMED_OUT
        assert peer.state == JobState.SUCCEEDED


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process-group acceptance")
def test_cancellation_stops_renderer_subprocess(tmp_path):
    with JobService(registry(), tmp_path, cancel_grace=0.1) as jobs:
        record = jobs.submit(JobRequest("eis", "run", (), {"mode": "subprocess", "seconds": 30}))
        marker = tmp_path / "jobs" / record.id / "child.pid"
        wait(jobs, marker.exists)
        child_pid = int(marker.read_text())
        jobs.cancel(record.id)
        wait(jobs, lambda: record.state.terminal)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True
            )
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Renderer subprocess survived cancellation")


def test_core_and_cycling_work_without_scientific_or_gui_packages(tmp_path):
    source = Path(__file__).parents[1] / "src"
    example = Path(__file__).parents[1] / "examples/cycling.csv"
    script = tmp_path / "no_extras.py"
    script.write_text(f"""
import sys,time,json
sys.path.insert(0,{str(source)!r})
from chem_suite.bootstrap import builtins
from chem_suite.core.jobs import JobService
from chem_suite.core.contracts import JobRequest
if __name__ == '__main__':
    with JobService(builtins(),{str(tmp_path / "workspace")!r}) as jobs:
        record=jobs.submit(JobRequest('cycling','analyze',({str(example)!r},)))
        while not record.state.terminal:
            jobs.poll(); time.sleep(.02)
        assert record.state=='succeeded', record.error
        assert not any(name in sys.modules for name in ('numpy','scipy','impedance','PySide6','matplotlib'))
        result_path=record.result_path
        exported=jobs.submit(JobRequest('exports','export',(result_path,),
            {{'destination':{str(tmp_path / 'stdlib-export')!r},'outputs':{{'excel':False}},'formats':[]}}))
        while not exported.state.terminal:
            jobs.poll(); time.sleep(.02)
        assert exported.state=='succeeded', exported.error
        assert not any(name in sys.modules for name in ('numpy','scipy','PySide6','matplotlib','openpyxl'))
        print(json.loads(open(result_path).read())['payload']['cycle_count'])
""")
    result = subprocess.run([sys.executable, "-S", str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2"


def test_settings_are_detached_and_invalid_requests_fail_early(tmp_path):
    with JobService(registry(), tmp_path) as jobs:
        config = {"seconds": 1}
        record = jobs.submit(JobRequest("eis", "run", (), config))
        config["seconds"] = 99
        assert record.request.config["seconds"] == 1
        with pytest.raises(ValueError):
            jobs.submit(JobRequest("missing", "run", ()))
        with pytest.raises(ValueError):
            jobs.submit(JobRequest("eis", "run", (), {}, float("nan")))
        with pytest.raises(ValueError):
            jobs.submit(JobRequest("eis", "run", (), {"x": float("nan")}))
