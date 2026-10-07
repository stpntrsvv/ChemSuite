"""Fault injection only; never registered by the application."""

import os
import subprocess
import sys
import time


def run(request, context):
    context.progress(0.1, "entered")
    mode = request.config.get("mode", "ok")
    if request.config.get("publication"):
        staging = context.publication(request.config["publication"])
        (staging / "data.txt").write_text("complete")
        context.artifact("prepared").touch()
    if mode == "crash":
        os._exit(23)
    if mode == "error":
        raise ValueError("intentional solver error")
    if mode == "subprocess":
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        context.artifact("child.pid").write_text(str(process.pid))
    deadline = time.monotonic() + request.config.get("seconds", 0)
    value = 0
    while time.monotonic() < deadline:
        value = (value * 17 + 1) % 99991  # CPU-bound Python, holds the job's GIL
        if request.config.get("cooperative", False):
            context.check_cancelled()
    return {"pid": os.getpid(), "value": value, "summary": {"Результат": "Готово"}, "plots": [], "tables": []}
