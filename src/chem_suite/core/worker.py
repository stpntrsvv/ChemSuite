import importlib
import os
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from chem_suite import __version__
from chem_suite.core.artifacts import JobCancelled, JobContext, write_json


def execute_job(request, handler: str, version: str, directory: str, cancel_event, connection) -> None:
    # Own a process group on POSIX, including renderer subprocesses. Hard cancellation
    # must stop the entire job, without touching peers or the desktop process.
    # Set before importing numerical libraries. Parallelism belongs to the supervisor.
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["MPLCONFIGDIR"] = str(Path(directory).parent.parent / ".cache" / "matplotlib")
    context = JobContext(directory, cancel_event, connection)
    started = time.monotonic()
    try:
        from chem_suite.core.process_tree import own_process_tree
        own_process_tree()
        context.check_cancelled()
        module, function = handler.split(":", 1)
        action = getattr(importlib.import_module(module), function)
        payload = action(request, context)
        context.check_cancelled()
        envelope = {
            "schema_version": 1,
            "platform_version": __version__,
            "module": request.module,
            "module_version": version,
            "action": request.action,
            "request": asdict(request),
            "elapsed_seconds": time.monotonic() - started,
            "payload": payload,
        }
        result_path = Path(directory) / "result.json"
        write_json(result_path, envelope)
        context.check_cancelled()
        connection.send({"type": "success", "result_path": str(result_path)})
    except JobCancelled:
        connection.send({"type": "cancelled"})
    except BaseException as exc:
        details = traceback.format_exc()
        (Path(directory) / "error.log").write_text(details, encoding="utf-8")
        connection.send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        connection.close()
