import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMainWindow, QMessageBox

from chem_suite.core.contracts import JobState
from chem_suite.desktop.app import create_application
from chem_suite.desktop.update_client import UpdateClient
from chem_suite.desktop.updates import UpdatesController
from tests.test_updates import offer_fixture, release_fixture


class Reply(QObject):
    readyRead = Signal()
    finished = Signal()
    redirected = Signal(QUrl)
    redirectAllowed = Signal()

    def __init__(self, request, content, status=200, error=QNetworkReply.NoError, *, delay=1, chunk=128*1024):
        super().__init__()
        self.request = request
        self.content, self.status, self.failure = content, status, error
        self.delay, self.chunk = delay, chunk
        self.buffer = bytearray()
        self.aborted = False
        self.allowed = False
        self.redirectAllowed.connect(lambda: setattr(self, 'allowed', True))
        QTimer.singleShot(delay, self.deliver)

    def deliver(self):
        if self.aborted:
            return
        self.buffer.extend(self.content[:self.chunk])
        self.content = self.content[self.chunk:]
        self.readyRead.emit()
        if self.content:
            QTimer.singleShot(self.delay, self.deliver)
        else:
            self.finished.emit()

    def bytesAvailable(self):
        return len(self.buffer)

    def read(self, count):
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def setReadBufferSize(self, value):
        self.buffer_limit = value

    def attribute(self, attr):
        assert attr == QNetworkRequest.HttpStatusCodeAttribute
        return self.status

    def error(self):
        return self.failure

    def errorString(self):
        return 'offline' if self.failure != QNetworkReply.NoError else 'OK'

    def url(self):
        return self.request.url()

    def abort(self):
        self.aborted = True
        self.finished.emit()  # abort() can synchronously emit finished in real Qt.

    def deleteLater(self):
        pass  # Keep a reference to inject hostile late signals after cancellation.


class Manager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.replies = []

    def get(self, request):
        reply = Reply(request, **self.responses.pop(0))
        self.replies.append(reply)
        return reply


@pytest.fixture
def app():
    return create_application()


def wait(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        time.sleep(.001)
        assert time.monotonic() < deadline
    app.processEvents()


@pytest.fixture
def clients(app, tmp_path):
    created = []
    def make(responses, **kwargs):
        manager = Manager(responses)
        client = UpdateClient(tmp_path, manager=manager, version='0.1.1', target='macos-arm64', **kwargs)
        created.append(client)
        return client, manager
    yield make
    for client in created:
        client.cancel()


def responses(payload=b'verified installer', **download):
    release, raw, payload = release_fixture(payload=payload)
    return [{'content': json.dumps(release).encode()}, {'content': raw}, {'content': payload, **download}]


def test_async_download_keeps_qt_responsive_and_checks_file(app, clients):
    content = b'x'*(6*1024*1024)
    client, manager = clients(responses(content, delay=5, chunk=64*1024))
    outcomes, completed, beats = [], [], []
    client.checked.connect(outcomes.append)
    client.downloaded.connect(completed.append)
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    try:
        client.check()
        wait(app, lambda: client.state == 'available')
        assert outcomes == ['available']
        request = manager.replies[0].request
        assert request.attribute(QNetworkRequest.RedirectPolicyAttribute) == QNetworkRequest.UserVerifiedRedirectPolicy
        assert request.rawHeader('User-Agent') == b'ChemSuite/0.1.1'
        client.download()
        wait(app, lambda: client.state == 'ready')
        assert client.path.read_bytes() == content
        assert completed == [str(client.path)]
        assert len(beats) >= 20
        # Native acceptance allows two seconds for cold Qt/filesystem setup on
        # shared Windows runners; the many timer ticks still prove ongoing I/O
        # is asynchronous rather than a synchronous whole-file transfer.
        assert max(b-a for a,b in zip(beats, beats[1:])) < 2
    finally:
        timer.stop()


def test_cancel_removes_partial_and_ignores_late_completion(app, clients, tmp_path):
    client, manager = clients(responses(b'x'*1024*1024, delay=30, chunk=1024))
    completed = []
    client.downloaded.connect(completed.append)
    client.check()
    wait(app, lambda: client.state == 'available')
    client.download()
    wait(app, lambda: client._sink.received > 0)
    reply = manager.replies[-1]
    client.cancel()
    assert client.state == 'available'
    assert not list(tmp_path.rglob('*.part'))
    reply.readyRead.emit()
    reply.finished.emit()
    assert not completed and client.path is None


@pytest.mark.parametrize('status,error,outcome', [(404,QNetworkReply.ContentNotFoundError,'none'),
                                                   (403,QNetworkReply.ContentAccessDenied,'error'),
                                                   (0,QNetworkReply.HostNotFoundError,'error')])
def test_empty_feed_rate_limit_and_offline_are_recoverable(app, clients, status, error, outcome):
    client, _ = clients([{'content': b'{}', 'status': status, 'error': error}])
    failed, checked = [], []
    client.failed.connect(failed.append)
    client.checked.connect(checked.append)
    client.check()
    wait(app, lambda: not client.busy)
    assert (client.state == 'error') == (outcome == 'error')
    assert checked == (['none'] if outcome == 'none' else [])
    assert bool(failed) == (outcome == 'error')


def test_corrupt_download_never_ready(app, clients, tmp_path):
    data = responses()
    data[-1]['content'] = b'x'*len(data[-1]['content'])
    client, _ = clients(data)
    client.check()
    wait(app, lambda: client.state == 'available')
    client.download()
    wait(app, lambda: client.state == 'error')
    assert client.path is None
    assert 'integrity' in client.error
    assert not list(tmp_path.rglob('*.part'))


def test_redirect_policy_rejects_untrusted_and_allows_named_cdn(app, clients):
    client, manager = clients([{'content': b'{}', 'delay': 100}])
    client.check()
    reply = manager.replies[0]
    reply.redirected.emit(QUrl('https://release-assets.githubusercontent.com/x?sig=abc'))
    assert reply.allowed
    reply.redirected.emit(QUrl('https://attacker.com/installer'))
    assert client.state == 'error' and reply.aborted


def test_automatic_cooldown_and_persisted_optout(app, clients, tmp_path):
    client, manager = clients([{'content': b'{}', 'status': 404}])
    client.set_automatic(False)
    client.check(manual=False)
    assert not manager.replies
    client.check(manual=True)
    wait(app, lambda: not client.busy)
    client.set_automatic(True)
    client.check(manual=False)
    assert len(manager.replies) == 1
    client.set_automatic(False)
    reopened = UpdateClient(tmp_path, manager=Manager([]), target='macos-arm64')
    assert not reopened.automatic
    reopened.cancel()


def test_deadline_abort_cannot_finish_later(app, clients):
    client, manager = clients([{'content': b'{}', 'delay': 1000}])
    client.check()
    client._deadline.start(10)
    wait(app, lambda: client.state == 'error')
    assert manager.replies[0].aborted
    assert client.error == 'Update request timed out'


@pytest.fixture
def controller(app, tmp_path, monkeypatch):
    import chem_suite.desktop.updates as updates
    monkeypatch.setattr(updates, 'user_data_directory', lambda: tmp_path)
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    window = QMainWindow()
    window.language = 'ru'
    window.about_menu = window.menuBar().addMenu('Help')
    window.session_job = None
    window.panels = [SimpleNamespace(_busy=False)]
    window.jobs = SimpleNamespace(workspace=tmp_path, records=lambda: [])
    ctrl = UpdatesController(window, schedule=False)
    window.updates = ctrl
    ctrl.client.offer = offer_fixture()
    ctrl.client.state = 'available'
    yield ctrl
    ctrl.installing = False
    if ctrl.dialog:
        ctrl.dialog.close()
    ctrl.close()
    window.close()
    app.processEvents()


def test_dialog_languages_notes_and_manual_recheck(controller, monkeypatch):
    ctrl = controller
    ctrl.checked('available')
    assert ctrl.dialog.heading.text().startswith('Доступна новая версия')
    assert ctrl.dialog.notes.toPlainText() == ctrl.client.offer.notes
    ctrl.window.language = 'en'
    ctrl.dialog.refresh()
    assert ctrl.dialog.primary.text() == 'Download update'
    ctrl.client.manager = Manager([{'content': b'{}', 'delay': 1000}])
    ctrl.check()
    assert ctrl.dialog is None
    assert ctrl.client.state == 'checking'


@pytest.mark.parametrize('busy', ['session', 'panel', 'queued', 'running', 'cancelling'])
def test_active_work_blocks_install_before_confirmation(controller, monkeypatch, busy):
    ctrl = controller
    ctrl.client.state = 'ready'
    if busy == 'session':
        ctrl.window.session_job = 'session-id'
    elif busy == 'panel':
        ctrl.window.panels[0]._busy = True
    else:
        state = {'queued':JobState.QUEUED, 'running':JobState.RUNNING, 'cancelling':JobState.CANCELLING}[busy]
        ctrl.window.jobs.records = lambda: [SimpleNamespace(state=state)]
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('Must not interrupt active work'))
    ctrl.install()
    assert not ctrl.installing


def test_new_job_during_reverification_prevents_launch(app, controller, monkeypatch, tmp_path):
    import chem_suite.desktop.updates as updates
    ctrl = controller
    ctrl.client.state = 'ready'
    ctrl.client.path = tmp_path / ctrl.client.offer.name
    ctrl.client.path.write_bytes(b'verified installer')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(updates, 'launch_installer', lambda *args: pytest.fail('A new job is active'))
    ctrl.install()
    ctrl.window.panels[0]._busy = True
    wait(app, lambda: not ctrl.installing)
    assert ctrl.client.state == 'ready'
    assert ctrl.check_action.isEnabled()


def test_reverification_failure_keeps_app_open_and_reports_error(app, controller, monkeypatch, tmp_path):
    import chem_suite.desktop.updates as updates
    ctrl = controller
    ctrl.client.state = 'ready'
    ctrl.client.path = tmp_path / ctrl.client.offer.name
    ctrl.client.path.write_bytes(b'corrupted after download')
    ctrl.checked('available')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(updates, 'launch_installer', lambda *args: pytest.fail('Corrupt installer'))
    ctrl.install()
    wait(app, lambda: not ctrl.installing)
    assert ctrl.client.state == 'error' and ctrl.client.path is None
    assert 'проверку целостности' in ctrl.dialog.status.text()
    assert ctrl.check_action.isEnabled()


def test_windows_installer_uses_argv_and_current_install_folder(controller, monkeypatch, tmp_path):
    import chem_suite.desktop.updates as updates
    calls = []
    monkeypatch.setattr(updates.sys, 'platform', 'win32')
    monkeypatch.setattr(updates.sys, 'executable', str(tmp_path / 'Chem Suite' / 'ChemSuite.exe'))
    monkeypatch.setattr(updates.QProcess, 'startDetached', lambda *args: calls.append(args) or (True, 42))
    path = tmp_path / 'ChemSuite-setup.exe'
    assert updates.launch_installer(path, 'ru')
    assert calls == [(str(path), [f'/DIR={tmp_path / "Chem Suite"}', '/LANG=russian'])]


def test_deeply_nested_response_does_not_leave_client_busy(app, clients):
    client, _ = clients([{'content': b'['*2000 + b'0' + b']'*2000}])
    client.check()
    wait(app, lambda: not client.busy)
    assert client.state == 'error'


def test_final_reply_larger_than_read_buffer_is_drained_before_validation(app, clients):
    content = b'x'*(4*1024*1024)
    client, _ = clients(responses(content, chunk=len(content)))
    client.check()
    wait(app, lambda: client.state == 'available')
    client.download()
    wait(app, lambda: not client.busy)
    assert client.state == 'ready'
    assert client.path.read_bytes() == content
