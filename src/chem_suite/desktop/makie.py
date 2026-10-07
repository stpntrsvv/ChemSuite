"""Qt bridge to an independent, persistent WGLMakie process."""

import json
from pathlib import Path
from chem_suite.locale import text
import socket
import time
import uuid

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from chem_suite.visualization.makie import runtime


class MakieBridge(QObject):
    plot_ready = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_errors)
        self.process.finished.connect(self.exited)
        self.process.errorOccurred.connect(self.process_error)
        self.ready = False
        self.pending = {}
        self.active = set()
        self.buffer = b""
        self.errors = ""
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.check_timeout)

    def submit(self, result_path, language="en"):
        request_id = uuid.uuid4().hex
        plots = json.loads(Path(result_path).read_text())["payload"].get("plots", [])
        for spec in plots:
            for key in ("title", "xlabel", "ylabel"):
                spec[key] = text(spec[key], language)
            for curve in spec["series"]:
                curve["label"] = text(curve["label"], language)
        self.pending[request_id] = (str(result_path), time.monotonic(), plots)
        if self.ready:
            self.send_pending()
        elif self.process.state() == QProcess.NotRunning:
            try:
                executable, scripts, environment = runtime()
                from PySide6.QtCore import QProcessEnvironment

                env = QProcessEnvironment()
                for name, value in environment.items():
                    env.insert(name, value)
                self.process.setProcessEnvironment(env)
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                self.process.setProgram(executable)
                self.process.setArguments(
                    [f"--project={scripts}", "--startup-file=no", str(scripts / "serve.jl"), str(port)]
                )
                self.buffer, self.errors = b"", ""
                self.process.start()
                self.timer.start()
            except Exception as exc:
                QTimer.singleShot(0, lambda message=str(exc): self.fail_pending(message))
        return request_id

    def send_pending(self):
        for request_id, (path, _, plots) in self.pending.items():
            if request_id in self.active:
                continue
            self.active.add(request_id)
            message = {"type": "plot", "id": request_id, "result_path": path, "display_plots": plots}
            self.process.write((json.dumps(message) + "\n").encode())

    def read_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if message.get("type") == "ready":
                self.ready = True
                self.send_pending()
            elif message.get("type") in {"plot", "error"}:
                request_id = message["id"]
                self.pending.pop(request_id, None)
                if message["type"] == "plot":
                    self.plot_ready.emit(request_id, message["url"])
                else:
                    self.active.discard(request_id)
                    self.failed.emit(request_id, message["message"])

    def read_errors(self):
        self.errors = (self.errors + bytes(self.process.readAllStandardError()).decode(errors="replace"))[
            -8000:
        ]

    def fail_pending(self, message):
        for request_id in self.pending:
            self.failed.emit(request_id, message)
        self.pending.clear()

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.fail_pending("Не удалось запустить Julia")

    def exited(self, *_):
        self.ready = False
        self.timer.stop()
        self.fail_pending(f"Makie остановлен. {self.errors[-1500:]}")
        for request_id in self.active:
            self.failed.emit(request_id, "Процесс графиков Makie остановлен; расчёты модулей продолжаются")
        self.active.clear()

    def check_timeout(self):
        if any(time.monotonic() - started > 600 for _, started, _ in self.pending.values()):
            self.fail_pending("Истёк лимит запуска Makie")
            self.process.kill()

    def close(self):
        self.timer.stop()
        self.pending.clear()
        self.active.clear()
        if self.process.state() != QProcess.NotRunning:
            self.process.write(b'{"type":"shutdown"}\n')
            self.process.closeWriteChannel()
            if not self.process.waitForFinished(1000):
                self.process.kill()
                self.process.waitForFinished(500)
