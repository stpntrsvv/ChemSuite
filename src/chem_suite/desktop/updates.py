"""Update UI: explicit download and installation, without interrupting jobs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from chem_suite.core.paths import user_data_directory
from chem_suite.desktop.update_client import UpdateClient
from chem_suite.locale import text


def verify_installer(path, offer):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024*1024):
            digest.update(block)
    return Path(path).stat().st_size == offer.size and digest.hexdigest() == offer.sha256


def launch_installer(path, language):
    if sys.platform == "win32":
        directory = str(Path(sys.executable).resolve().parent)
        # Normal per-user wizard, same location as the running app, no silent
        # privilege escalation or forced termination of other application instances.
        return QProcess.startDetached(str(path), [f"/DIR={directory}", f"/LANG={'russian' if language == 'ru' else 'english'}"])[0]
    if sys.platform == "darwin":
        return QProcess.startDetached("/usr/bin/open", [str(path)])[0]
    return False


class UpdateDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlainText(controller.client.offer.notes)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        layout.addWidget(self.heading)
        layout.addWidget(self.notes)
        layout.addWidget(self.status)
        layout.addWidget(self.bar)
        row = QHBoxLayout()
        self.release_button = QPushButton()
        self.release_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(controller.client.offer.release_url)))
        self.later = QPushButton()
        self.later.clicked.connect(self.close)
        self.primary = QPushButton()
        self.primary.clicked.connect(self.proceed)
        row.addWidget(self.release_button)
        row.addStretch()
        row.addWidget(self.later)
        row.addWidget(self.primary)
        layout.addLayout(row)
        controller.client.changed.connect(self.refresh)
        controller.client.progress.connect(self.progress)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def tr_text(self, value):
        return text(value, self.controller.window.language)

    def refresh(self):
        client = self.controller.client
        self.setWindowTitle(self.tr_text('Software update'))
        self.heading.setText(self.tr_text('New version available') + f": Chem Suite {client.offer.version}")
        self.release_button.setText(self.tr_text('Release page'))
        self.later.setText(self.tr_text('Cancel download') if client.state == 'downloading' else self.tr_text('Later'))
        ready = client.state == 'ready'
        source = not getattr(sys, 'frozen', False)
        self.primary.setText(self.tr_text('Open release page') if source else self.tr_text('Install update') if ready else self.tr_text('Download update'))
        self.primary.setEnabled(not client.busy and not self.controller.installing and (not ready or self.controller.can_install()))
        self.bar.setVisible(client.state == 'downloading')
        if source:
            message = 'Install the packaged application to receive updates here.'
        elif client.state == 'downloading':
            message = 'Downloading update…'
        elif self.controller.installing:
            message = 'Verifying downloaded update…'
        elif ready and not self.controller.can_install():
            message = 'The update is ready. Finish the current analysis before installing.'
        elif ready:
            message = 'The update is ready. Save your session before restarting.'
        elif client.state == 'error':
            message = 'Update failed'
        else:
            message = 'Your data and analysis results stay in their current folders.'
        self.status.setText(self.tr_text(message) + (": " + self.tr_text(client.error) if client.state == "error" else ""))

    def progress(self, received, total):
        self.bar.setValue(round(1000*received/max(1,total)))
        self.status.setText(self.tr_text('Downloading update…') + f' {received/1e6:.1f} / {total/1e6:.1f} MB')

    def proceed(self):
        client = self.controller.client
        if not getattr(sys, 'frozen', False):
            QDesktopServices.openUrl(QUrl(client.offer.release_url))
        elif client.state == 'ready':
            self.controller.install()
        else:
            client.download()

    def closeEvent(self, event):
        if self.controller.installing:
            event.ignore()
            return
        self.timer.stop()
        self.controller.client.changed.disconnect(self.refresh)
        self.controller.client.progress.disconnect(self.progress)
        self.controller.client.cancel()
        self.controller.dialog = None
        event.accept()


class UpdatesController:
    def __init__(self, window, *, schedule=True):
        self.window = window
        directory = user_data_directory() / 'updates' if getattr(sys, 'frozen', False) else window.jobs.workspace / '.updates'
        self.client = UpdateClient(directory, window)
        self.dialog = None
        self.installing = False
        self._executor = None
        self._future = None
        self.check_action = QAction('Check for updates…', window)
        self.check_action.triggered.connect(self.check)
        self.auto_action = QAction('Automatically check for updates', window)
        self.auto_action.setCheckable(True)
        self.auto_action.setChecked(self.client.automatic)
        self.auto_action.toggled.connect(self.client.set_automatic)
        window.about_menu.addSeparator()
        window.about_menu.addActions([self.check_action, self.auto_action])
        self.client.checked.connect(self.checked)
        self.client.failed.connect(self.failed)
        self.client.changed.connect(lambda: self.check_action.setEnabled(not self.client.busy and not self.installing))
        self._timer = QTimer(window)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._verified)
        if schedule and getattr(sys, 'frozen', False):
            self._startup = QTimer(window)
            self._startup.setSingleShot(True)
            self._startup.timeout.connect(lambda: self.client.check(manual=False))
            self._startup.start(5000)

    def check(self):
        if self.installing or self.client.busy:
            return
        if self.dialog:
            self.dialog.close()
        self.client.check(manual=True)

    def checked(self, outcome):
        if outcome == 'available':
            version = self.client.offer.version
            if self.client.manual or version != self.client.preferences.get('notified_version'):
                self.client.mark_notified()
                if self.dialog:
                    self.dialog.close()
                self.dialog = UpdateDialog(self)
                self.dialog.show()
        elif self.client.manual:
            messages = {'none': 'No releases have been published yet.', 'current': 'You are using the latest version.',
                        'unsupported': 'No update package is available for this platform.'}
            QMessageBox.information(self.window, text('Software update', self.window.language), text(messages[outcome], self.window.language))

    def failed(self, detail):
        if self.dialog:
            self.dialog.status.setText(text('Update failed', self.window.language) + ': ' + text(detail, self.window.language))
        elif self.client.manual:
            QMessageBox.warning(self.window, text('Software update', self.window.language), text('Update check failed', self.window.language) + ': ' + text(detail, self.window.language))

    def can_install(self):
        return not self.window.session_job and not any(not r.state.terminal for r in self.window.jobs.records()) and not any(p._busy for p in self.window.panels)

    def install(self):
        if self.installing or self.client.state != 'ready' or not getattr(sys, 'frozen', False) or not self.can_install():
            return
        message = ('The installer will open and Chem Suite will close. Save your session first.' if sys.platform == 'win32' else
                   'The disk image will open and Chem Suite will close. Drag Chem Suite to Applications and confirm replacement. Save your session first.')
        answer = QMessageBox.question(self.window, text('Install update', self.window.language), text(message, self.window.language))
        if answer != QMessageBox.Yes or not self.can_install():
            return
        self.installing = True
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='update-integrity')
        self._future = self._executor.submit(verify_installer, self.client.path, self.client.offer)
        self._timer.start()
        self.client.changed.emit()
        if self.dialog:
            self.dialog.refresh()

    def _verified(self):
        if self._future is None or not self._future.done():
            return
        self._timer.stop()
        try:
            if not self._future.result():
                raise ValueError('Update download failed integrity verification')
            # A calculation may have started during verification.
            if not self.can_install():
                return
            if not launch_installer(self.client.path, self.window.language):
                raise OSError('Could not open the update installer')
            self.installing = False
            self.dialog = None
            self.window.close()
        except (OSError, ValueError) as exc:
            self.client.path = None
            self.client._fail(str(exc))
        finally:
            self.installing = False
            self._executor.shutdown(wait=False)
            self._executor = None
            self._future = None
            self.client.changed.emit()
            if self.dialog:
                self.dialog.refresh()

    def close(self):
        if hasattr(self, '_startup'):
            self._startup.stop()
        self.client.cancel()
        self._timer.stop()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
