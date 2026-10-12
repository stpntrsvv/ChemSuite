import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from chem_suite.desktop.app import MainWindow, create_application
from chem_suite.desktop.session_recovery import SessionRecovery
from chem_suite.locale import text
from tests.cycling_fixtures import mpr_intervals
from tests.test_updates import offer_fixture

MESSAGE_EXEC = QMessageBox.exec


def wait(app, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        time.sleep(.002)
        assert time.monotonic() < deadline
    app.processEvents()


def choose(monkeypatch, label):
    def execute(box):
        button = next(b for b in box.buttons() if b.text() == label)
        QTimer.singleShot(0, button.click)
        return MESSAGE_EXEC(box)
    monkeypatch.setattr(QMessageBox, 'exec', execute)


@pytest.fixture
def desktop(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path / 'workspace', auto_update_check=False)
    window.updates._restore_startup.stop()
    window.show()
    yield app, window
    window.close()
    app.processEvents()


def ready(window, tmp_path, monkeypatch):
    import chem_suite.desktop.updates as updates
    # Keep multiprocessing's real sys.frozen unchanged for source worker tests.
    monkeypatch.setattr(updates, 'sys', SimpleNamespace(frozen=True, platform='darwin'))
    ctrl = window.updates
    ctrl.client.offer = offer_fixture()
    ctrl.client.path = tmp_path / ctrl.client.offer.name
    ctrl.client.path.write_bytes(b'verified installer')
    ctrl.client.state = 'ready'
    return ctrl


def test_save_update_restart_restores_results_without_original_or_recalculation(desktop, tmp_path, monkeypatch):
    import chem_suite.desktop.updates as updates
    app, window = desktop
    panel = window.panels[1]
    panel._export_legend = 'hidden'
    source = mpr_intervals(tmp_path / 'intervals.mpr')
    panel.load_sources([source])
    wait(app, lambda: not panel._busy)
    assert panel.result_path
    window.module_selector.setCurrentIndex(1)
    window.set_language('en')
    panel.options['selected_cycles'][1].setText('1')
    panel.apply_view()
    wait(app, lambda: not panel._busy)
    assert panel.datasets[str(source)]['view']['selected_cycles'] == [1]
    ctrl = ready(window, tmp_path, monkeypatch)
    choose(monkeypatch, 'Save and update')
    launched = []
    def launch(*args):
        saved = ctrl.recovery.pending()
        assert saved.is_file()
        assert not window.session_job and not panel._busy
        launched.append(saved)
        return True
    monkeypatch.setattr(updates, 'launch_installer', launch)
    closed = []
    original_close = window.close
    monkeypatch.setattr(window, 'close', lambda: closed.append(True))
    ctrl.install()
    assert window.session_job and ctrl._saving and not launched and not closed
    wait(app, lambda: bool(closed))
    assert len(launched) == 1 and not ctrl.installing
    source.unlink()
    monkeypatch.setattr(window, 'close', original_close)
    window.close()
    monkeypatch.setattr(updates, 'sys', SimpleNamespace(frozen=False, platform='darwin'))
    choose(monkeypatch, 'Restore session')
    reopened = MainWindow(tmp_path / 'workspace', auto_update_check=False)
    try:
        wait(app, lambda: bool(reopened.jobs.records()) and not reopened.session_job)
        restored = reopened.panels[1]
        assert restored.selected_source == str(source)
        assert restored.datasets[str(source)]['view']['selected_cycles'] == [1]
        assert restored.datasets[str(source)]['result']['payload']['cycle_count'] == 1
        assert restored.result_path and reopened.module_selector.currentData() == 'cycling'
        assert restored._export_legend == 'hidden'
        assert reopened.language == 'en'
        assert [r.request.action for r in reopened.jobs.records()] == ['restore']
        assert reopened.updates.recovery.pending() is None
        assert launched[0].is_file()  # The portable backup remains available.
    finally:
        reopened.close()


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_cancel_install_leaves_data_and_download_in_place(desktop, tmp_path, monkeypatch, language):
    import chem_suite.desktop.updates as updates
    _, window = desktop
    window.set_language(language, persist=False)
    panel = window.panels[1]
    panel.path.setText('retained.mpr')
    ctrl = ready(window, tmp_path, monkeypatch)
    choose(monkeypatch, text('Cancel', language))
    monkeypatch.setattr(updates, 'launch_installer', lambda *args: pytest.fail('Cancelled'))
    ctrl.install()
    assert not ctrl.installing and not window.session_job
    assert panel.path.text() == 'retained.mpr'
    assert ctrl.client.state == 'ready' and ctrl.client.path.is_file()
    assert ctrl.recovery.pending() is None


@pytest.mark.parametrize('failure', ['failed', 'cancelled'])
def test_failed_or_cancelled_save_never_launches_installer(desktop, tmp_path, monkeypatch, failure):
    import chem_suite.desktop.updates as updates
    _, window = desktop
    ctrl = ready(window, tmp_path, monkeypatch)
    monkeypatch.setattr(ctrl, 'confirm_install', lambda: 'save')
    callback = []
    def save(target, *, on_finished):
        callback.append(on_finished)
        return 'job-id'
    monkeypatch.setattr(window, 'save_session_to', save)
    monkeypatch.setattr(updates, 'launch_installer', lambda *args: pytest.fail('Save did not succeed'))
    ctrl.install()
    assert ctrl._saving and ctrl.installing
    callback[0](None, failure)
    assert not ctrl.installing and not ctrl._saving
    assert ctrl.client.state == 'error' and failure in ctrl.client.error
    assert ctrl.recovery.pending() is None and ctrl.client.path.is_file()


def test_restore_later_discard_and_corrupted_backup_remain_recoverable(desktop, tmp_path, monkeypatch):
    app, window = desktop
    ctrl = window.updates
    target = ctrl.recovery.destination()
    window.save_session_to(target)
    wait(app, lambda: not window.session_job)
    ctrl.recovery.remember(target, '0.1.3')
    saved = ctrl.recovery.pending()
    choose(monkeypatch, text('Later', window.language))
    ctrl.offer_restore()
    assert ctrl.recovery.pending() == saved and not window.session_job
    manifest = json.loads(saved.read_text())
    manifest['state']['active_module'] = 'unavailable'
    saved.write_text(json.dumps(manifest))
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    choose(monkeypatch, text('Restore session', window.language))
    ctrl.offer_restore()
    wait(app, lambda: not window.session_job)
    assert warnings and ctrl.recovery.pending() == saved
    assert not any(p._busy for p in window.panels)
    choose(monkeypatch, text('Start without restoring', window.language))
    ctrl.offer_restore()
    assert ctrl.recovery.pending() is None and saved.is_file()


@pytest.mark.parametrize('relative', ['/tmp/session.json', '../session.json', 'sessions/../session.json', 'elsewhere/one/session.json'])
def test_handoff_rejects_paths_outside_recovery(tmp_path, relative):
    recovery = SessionRecovery(tmp_path)
    recovery.pointer.write_text(json.dumps({'schema_version': 1, 'session': relative}))
    with pytest.raises((OSError, ValueError)):
        recovery.pending()


def test_handoff_rejects_symlink_escape_and_missing_manifest(tmp_path):
    recovery = SessionRecovery(tmp_path / 'recovery')
    target = recovery.destination()
    target.mkdir(parents=True)
    with pytest.raises(ValueError, match='missing'):
        recovery.remember(target, '0.1.3')
    external = tmp_path / 'session.json'
    external.write_text('{}')
    try:
        (target / 'session.json').symlink_to(external)
    except OSError:
        if os.name == 'nt':
            pytest.skip('Windows account cannot create symlinks')
        raise
    recovery.pointer.write_text(json.dumps({'schema_version': 1, 'session': str(Path('sessions') / target.name / 'session.json')}))
    with pytest.raises(ValueError):
        recovery.pending()
    with pytest.raises(ValueError):
        recovery.remember(target, '0.1.3')


@pytest.mark.parametrize('mode', ['cancel', 'existing-destination'])
def test_actual_save_worker_cancel_or_publication_failure_keeps_app_open(desktop, tmp_path, monkeypatch, mode):
    import chem_suite.desktop.updates as updates
    app, window = desktop
    ctrl = ready(window, tmp_path, monkeypatch)
    monkeypatch.setattr(ctrl, 'confirm_install', lambda: 'save')
    monkeypatch.setattr(updates, 'launch_installer', lambda *args: pytest.fail('Unsuccessful save'))
    target = ctrl.recovery.destination()
    monkeypatch.setattr(ctrl.recovery, 'destination', lambda: target)
    if mode == 'existing-destination':
        target.mkdir(parents=True)
    ctrl.install()
    if mode == 'cancel':
        window.cancel_active()
    wait(app, lambda: not ctrl.installing)
    assert ctrl.client.state == 'error' and not window.session_job
    assert not (target / 'session.json').exists()
    assert ctrl.recovery.pending() is None
    assert window.isVisible() and not any(p._busy for p in window.panels)


def test_cannot_publish_handoff_never_launches_installer(desktop, tmp_path, monkeypatch):
    import chem_suite.desktop.updates as updates
    app, window = desktop
    ctrl = ready(window, tmp_path, monkeypatch)
    monkeypatch.setattr(ctrl, 'confirm_install', lambda: 'save')
    monkeypatch.setattr(updates, 'launch_installer', lambda *args: pytest.fail('Handoff write failed'))
    def fail(*args):
        raise OSError('read-only recovery record')
    monkeypatch.setattr(ctrl.recovery, 'remember', fail)
    ctrl.install()
    wait(app, lambda: not ctrl.installing)
    assert 'read-only' in ctrl.client.error and window.isVisible()
    assert list(ctrl.recovery.directory.rglob('session.json'))
