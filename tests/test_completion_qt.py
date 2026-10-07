import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer

from chem_suite.desktop.app import MainWindow, create_application
from chem_suite.desktop.sessions import capture_panel
from chem_suite.core.contracts import JobState
from tests.test_advanced import rc_spectrum


def until_idle(app, window, condition=None, timeout=70):
    deadline = time.monotonic()+timeout
    while any(p._busy for p in window.panels) or window.session_job or (condition and not condition()):
        app.processEvents()
        time.sleep(.002)
        assert time.monotonic() < deadline, [p.status.text() for p in window.panels]
    app.processEvents()


def test_desktop_studies_sessions_and_archived_refit_preserve_language_and_module(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path/'work')
    window.show()
    panel = window.panels[0]
    source = rc_spectrum(tmp_path/'source.csv', low=-.5, high=2)
    heartbeats = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    timer.start()
    try:
        panel.path.setText(str(source))
        panel.options['candidate_circuits'][1].setText('R0-p(R1,C1);R0')
        # Unused C export settings must never disable ordinary fitting.
        panel.options['controller_sample_period_s'][1].setText('bad-value')
        panel.submit()
        until_idle(app, window)
        assert panel.displayed_result['payload']['best']['circuit'] == 'R0-p(R1,C1)'
        panel.select_model(next(i for i, row in enumerate(panel.displayed_result['payload']['fits']) if row['circuit']=='R0'))
        assert panel.view_circuit == 'R0'
        fit_path = panel.result_path
        panel.action.setCurrentIndex(panel.action.findData('resolution'))
        assert panel.run_button.isEnabled()
        assert not panel.controller_button.isVisible()
        panel.options['resolution_min_frequencies'][1].setText('.1,.01')
        panel.options['resolution_noise_fractions'][1].setText('.01')
        panel.options['resolution_replicates'][1].setValue(1)
        panel.options['resolution_points'][1].setValue(31)
        panel.submit()
        until_idle(app, window)
        assert panel.result_path != fit_path
        assert panel.displayed_result['action'] == 'resolution'
        assert panel.results.count() >= 4
        window.set_language('en')
        assert panel.view_label.text() == 'Synthetic study'
        panel.action.setCurrentIndex(panel.action.findData('fit'))
        assert panel.view_circuit == 'R0' and panel.result_path == fit_path
        assert panel.controller_button.isEnabled() and panel.spice_button.isEnabled()
        window.module_selector.setCurrentIndex(1)
        state = {'language': 'en', 'active_module': 'cycling', 'panels': [capture_panel(p) for p in window.panels]}
        destination = tmp_path/'session'
        window.start_session('save', (), {'state': state, 'destination': str(destination)})
        until_idle(app, window)
        assert destination.exists()
        panel.parameter_overrides = {'garbage': {}}
        source.unlink()
        window.set_language('ru')
        window.start_session('restore', (str(destination/'session.json'),), {})
        until_idle(app, window)
        assert window.language == 'en' and window.module_selector.currentData() == 'cycling'
        assert panel.parameter_overrides == {}
        assert panel.view_circuit == 'R0'
        assert panel.get_config()['candidate_circuits'] == 'R0-p(R1,C1);R0'
        assert panel.options['controller_sample_period_s'][1].text() == 'bad-value'
        assert Path(panel.datasets[str(source)]['runtime_source']).is_file()
        window.module_selector.setCurrentIndex(0)
        panel.submit()
        until_idle(app, window)
        assert panel.displayed_result['payload']['sources'][0]['path'] == str(source)
        assert window.jobs.record(panel.current_job).state == JobState.SUCCEEDED
        panel.edit_parameters()
        until_idle(app, window)
        assert panel.parameter_dialog.table.rowCount() == 3
        panel.parameter_dialog.close()
        assert len(heartbeats) >= 25
        assert max(b-a for a,b in zip(heartbeats, heartbeats[1:])) < .6
        prior = panel.result_path
        manifest = destination/'session.json'
        document = json.loads(manifest.read_text())
        document['state']['panels'][0]['controls']['restarts'] = 'invalid'
        manifest.write_text(json.dumps(document))
        window.start_session('restore', (str(manifest),), {})
        until_idle(app, window)
        assert panel.result_path == prior
        assert not panel._busy and not window.panels[1]._busy
        assert 'Invalid integer' in panel.status.text()
    finally:
        timer.stop()
        window.close()


def test_desktop_joint_mode_requires_manifest_and_hides_batch_fitting(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    try:
        panel.action.setCurrentIndex(panel.action.findData('joint'))
        panel.pro_toggle.setChecked(True)
        window.sync_actions()
        assert not panel.run_button.isEnabled()
        assert not window.run_all_action.isEnabled()
        panel.options['series_manifest'][1].setText('/missing/series.csv')
        assert panel.run_button.isEnabled()
        panel.options['circuit'][1].setEditText('R0-p(R1,C1)')
        panel.submit()
        until_idle(app, window)
        assert not panel.result_path
        assert panel.status.text()
        assert window.panels[1].run_button.isEnabled() is False
    finally:
        window.close()


def test_research_result_opens_and_restores_with_matching_export_dialog(tmp_path):
    from chem_suite.desktop.sessions import attach_result
    from chem_suite.desktop.export_dialog import ExportDialog
    app = create_application()
    bundle = tmp_path/'research'
    bundle.mkdir()
    (bundle/'summary.json').write_text('{"count":1}')
    raw = bundle/'result.json'
    raw.write_text(json.dumps({'schema_version': 1, 'module': 'eis', 'module_version': '0.1.0',
        'action': 'research', 'request': {}, 'payload': {'dataset_type': 'eis.research.v1', 'sources': [],
        'summary': {'analysis': 'synthetic'}, 'plots': [], 'tables': [], 'artifacts': ['summary.json']}}))
    window = MainWindow(tmp_path/'work')
    panel = window.panels[0]
    try:
        attach_result(panel, str(raw))
        assert panel.export_action == 'research' and panel.export_paths == [str(raw)]
        assert not panel.spice_button.isEnabled() and not panel.controller_button.isEnabled()
        dialog = ExportDialog(panel, 'en', 'eis', 1, panel.export_action)
        assert 'study_plots' in dialog.boxes and 'spice' not in dialog.boxes
        dialog.close()
        state = {'language': 'ru', 'active_module': 'eis', 'panels': [capture_panel(p) for p in window.panels]}
        target = tmp_path/'session'
        window.start_session('save', (), {'state': state, 'destination': str(target)})
        until_idle(app, window)
        assert target.exists()
        window.start_session('restore', (str(target/'session.json'),), {})
        until_idle(app, window)
        assert panel.export_action == 'research'
        assert panel.displayed_result['action'] == 'research'
        panel.start_export([panel.result_path], str(tmp_path/'export'), {'excel': False}, [])
        until_idle(app, window)
        assert (tmp_path/'export/001_synthetic/result.json').is_file()
    finally:
        window.close()
