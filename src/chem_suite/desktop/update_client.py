"""Asynchronous Qt transport; never uses the calculation queue or a GitHub token."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from chem_suite import __version__
from chem_suite.core.artifacts import write_json
from chem_suite.updates.protocol import API_URL, DownloadSink, UpdateError, asset, parse_manifest, parse_release, platform_key, trusted_url


class UpdateClient(QObject):
    checked = Signal(str)
    failed = Signal(str)
    changed = Signal()
    progress = Signal(int, int)
    downloaded = Signal(str)

    def __init__(self, directory, parent=None, *, manager=None, version=__version__, target=None):
        super().__init__(parent)
        self.directory = Path(directory)
        self.preferences_path = self.directory / "preferences.json"
        try:
            prefs = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            self.preferences = prefs if isinstance(prefs, dict) else {}
        except (OSError, ValueError):
            self.preferences = {}
        self.manager = manager or QNetworkAccessManager(self)
        self.version = version
        self.target = target or platform_key()
        self.state = "idle"
        self.manual = False
        self.error = ""
        self.offer = None
        self.path = None
        self._reply = None
        self._buffer = bytearray()
        self._sink = None
        self._release = None
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(lambda: self._fail("Update request timed out"))

    @property
    def busy(self):
        return self.state in {"checking", "manifest", "downloading"}

    @property
    def automatic(self):
        return self.preferences.get("automatic", True) is True

    def set_automatic(self, enabled):
        self.preferences['automatic'] = bool(enabled)
        self._save()

    def _save(self):
        try:
            write_json(self.preferences_path, self.preferences)
        except OSError:
            # Settings failure must not break analysis or enable an invalid download.
            pass

    def check(self, *, manual=True):
        if self.busy:
            return
        self.manual = manual
        if not manual:
            previous = self.preferences.get("last_attempt", 0)
            if not self.automatic or (type(previous) in {int, float} and 0 <= time.time() - previous < 24*3600):
                return
        if self.target is None:
            self.checked.emit("unsupported")
            return
        self.preferences['last_attempt'] = time.time()
        self._save()
        self.offer = None
        self.path = None
        self.error = ""
        self.state = "checking"
        self._get(API_URL)

    def _get(self, url):
        if not trusted_url(url):
            self._fail("Invalid update URL")
            return
        self._buffer = bytearray()
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"ChemSuite/{self.version}".encode("ascii"))
        request.setRawHeader(b"Accept", b"application/vnd.github+json" if url == API_URL else b"application/octet-stream")
        request.setRawHeader(b"Accept-Encoding", b"identity")
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.UserVerifiedRedirectPolicy)
        request.setMaximumRedirectsAllowed(5)
        request.setTransferTimeout(60000 if self.state == "downloading" else 15000)
        reply = self.manager.get(request)
        self._reply = reply
        reply.setReadBufferSize(1024*1024)
        reply.redirected.connect(lambda url, r=reply: self._redirect(r, url))
        reply.readyRead.connect(lambda r=reply: self._read(r))
        reply.finished.connect(lambda r=reply: self._finish(r))
        if self.state != "downloading":
            self._deadline.start(30000)
        self.changed.emit()

    def _redirect(self, reply, url):
        if reply is not self._reply:
            return
        resolved = reply.url().resolved(url).toString()
        if not trusted_url(resolved, cdn=True):
            self._fail("Invalid update redirect")
        else:
            reply.redirectAllowed()

    def _read(self, reply):
        if reply is not self._reply:
            return
        try:
            budget = 1024*1024
            while reply.bytesAvailable() and budget > 0:
                raw = bytes(reply.read(min(256*1024, budget)))
                if not raw:
                    break
                budget -= len(raw)
                if self.state == "downloading":
                    self._sink.write(raw)
                else:
                    self._buffer.extend(raw)
                    limit = 64*1024 if self.state == "manifest" else 2*1024*1024
                    if len(self._buffer) > limit:
                        raise UpdateError("Update metadata is too large")
            if self.state == "downloading":
                self.progress.emit(self._sink.received, self.offer.size)
            if reply.bytesAvailable():
                QTimer.singleShot(0, lambda r=reply: self._read(r))
        except (OSError, UpdateError) as exc:
            self._fail(str(exc))

    def _finish(self, reply):
        if reply is not self._reply:
            return
        self._read(reply)
        if reply is not self._reply:
            return
        if reply.bytesAvailable():
            # Qt buffer limits are approximate: drain the final bytes over more
            # event-loop turns before validating, without blocking the UI.
            QTimer.singleShot(0, lambda r=reply: self._finish(r))
            return
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        error = reply.error()
        detail = reply.errorString()
        self._reply = None
        self._deadline.stop()
        reply.deleteLater()
        if self.state == "checking" and status == 404:
            self.state = "idle"
            self.changed.emit()
            self.checked.emit("none")
            return
        if error != QNetworkReply.NoError or status != 200:
            self._fail("GitHub request limit reached" if status in {403, 429} else "Update connection failed: " + detail)
            return
        try:
            if self.state == "checking":
                self._release = parse_release(json.loads(self._buffer), self.version)
                if self._release is None:
                    self.state = "idle"
                    self.changed.emit()
                    self.checked.emit("current")
                    return
                self.state = "manifest"
                self._get(asset(self._release, "ChemSuite-update.json")['browser_download_url'])
            elif self.state == "manifest":
                self.offer = parse_manifest(bytes(self._buffer), self._release, self.target)
                self.state = "available"
                self.changed.emit()
                self.checked.emit("available")
            elif self.state == "downloading":
                self.path = self._sink.finish()
                self._sink = None
                self.state = "ready"
                self.changed.emit()
                self.downloaded.emit(str(self.path))
        except RecursionError:
            self._fail("Invalid update manifest")
        except (OSError, ValueError, TypeError) as exc:
            self._fail(str(exc))

    def download(self):
        if self.offer is None or self.busy:
            return
        try:
            self.error = ""
            self._sink = DownloadSink(self.directory / "downloads", self.offer)
            self.state = "downloading"
            self._get(self.offer.url)
        except (OSError, ValueError) as exc:
            self._fail(str(exc))

    def _fail(self, message):
        self.cancel()
        self.error = message
        self.state = "error"
        self.changed.emit()
        self.failed.emit(message)

    def cancel(self):
        self._deadline.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if self._sink is not None:
            self._sink.abort()
            self._sink = None
        self.state = "ready" if self.path else "available" if self.offer else "idle"
        self.changed.emit()

    def mark_notified(self):
        if self.offer:
            self.preferences['notified_version'] = self.offer.version
            self._save()
