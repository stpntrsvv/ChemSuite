import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from chem_suite.core.contracts import JobEvent, JobRequest, JobState
from chem_suite.desktop.app import MainWindow, create_application
from tests.test_isolation import registry
from chem_suite.locale import text


def contrast(first, second):
    def luminance(color):
        values = (color.redF(), color.greenF(), color.blueF())
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("system_dark", [False, True])
def test_desktop_text_remains_readable_with_system_appearance(tmp_path, system_dark):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet("")
    system_palette = QPalette()
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        system_palette.setColor(role, QColor("#202020" if system_dark else "#ffffff"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        system_palette.setColor(role, QColor("#ffffff" if system_dark else "#202020"))
    app.setPalette(system_palette)

    # Exercise the launcher's setup, including inherited widget palettes and QSS.
    app = create_application()
    window = MainWindow(tmp_path)
    window.show()
    app.processEvents()
    try:
        for group in (QPalette.Active, QPalette.Inactive):
            assert app.palette().color(group, QPalette.Window).lightness() > 200
            pairs = [(window.task_table, QPalette.Text, QPalette.Base)]
            pairs.append((window.task_table.horizontalHeader(), QPalette.ButtonText, QPalette.Button))
            for panel in window.panels:
                pairs.extend([
                    (panel.status, QPalette.WindowText, QPalette.Window),
                    (panel.path, QPalette.Text, QPalette.Base),
                    (panel.path, QPalette.PlaceholderText, QPalette.Base),
                    (panel.action, QPalette.Text, QPalette.Base),
                    (panel.action.view(), QPalette.Text, QPalette.Base),
                    (panel.run_button, QPalette.ButtonText, QPalette.Button),
                ])
            for widget, foreground, background in pairs:
                palette = widget.palette()
                assert contrast(palette.color(group, foreground), palette.color(group, background)) >= 4.5
            for widget in (window.task_table, window.panels[0].action.view(), window.panels[0].path):
                palette = widget.palette()
                assert contrast(
                    palette.color(group, QPalette.HighlightedText),
                    palette.color(group, QPalette.Highlight),
                ) >= 4.5
        for panel in window.panels:
            palette = panel.cancel_button.palette()
            assert contrast(
                palette.color(QPalette.Disabled, QPalette.ButtonText),
                palette.color(QPalette.Disabled, QPalette.Button),
            ) >= 3
    finally:
        window.close()


def test_completed_job_with_batched_progress_displays_result_once(tmp_path, monkeypatch):
    create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    result = {"payload": {"summary": {"status": "ready"}}}
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result))
    record = SimpleNamespace(state=JobState.SUCCEEDED, result_path=str(result_path))
    monkeypatch.setattr(window.jobs, "record", lambda _: record)
    rendered = []
    monkeypatch.setattr(panel, "show_result", rendered.append)
    panel.current_job = "finished-job"
    panel.path.setText("spectrum.txt")
    try:
        # The supervisor already knows the final state when a whole batch is drained.
        for state, progress, message in (
            (JobState.RUNNING, 0.2, "Reading"),
            (JobState.RUNNING, 0.8, "Fitting"),
            (JobState.SUCCEEDED, 1.0, "Ready"),
        ):
            panel.handle_event(JobEvent(panel.current_job, state, progress, message))
        assert rendered == [result]
        assert panel.progress.value() == 100
        assert panel.status.text() == text("Ready", panel.language)
        assert panel.run_button.isEnabled()
        assert not panel.cancel_button.isEnabled()
    finally:
        window.close()


def test_qt_heartbeat_survives_cpu_bound_job_and_crash(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path, registry=registry())
    long = window.jobs.submit(JobRequest("eis", "run", (), {"seconds": 1.2}))
    peer = window.jobs.submit(JobRequest("cycling", "run", (), {"mode": "crash"}))
    heartbeats = []
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    heartbeat.start()
    try:
        deadline = time.monotonic() + 10
        while not (long.state.terminal and peer.state.terminal):
            app.processEvents()
            time.sleep(0.002)
            assert time.monotonic() < deadline
        assert long.state == JobState.SUCCEEDED
        assert peer.state == JobState.FAILED
        assert len(heartbeats) >= 20
        assert max(b - a for a, b in zip(heartbeats, heartbeats[1:])) < 0.25
    finally:
        heartbeat.stop()
        window.close()


def test_real_modules_display_graphs_without_blocking_ui(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path)
    window.show()
    root = Path(__file__).parents[1]
    eis, cycling = window.panels
    eis.path.setText(str(root / "examples/eis_double_cpe.txt"))
    eis.options["circuit"][1].setCurrentText("R0-p(R1,CPE0)-p(R2,CPE1)")
    cycling.path.setText(str(root / "examples/cycling.csv"))
    heartbeats = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    timer.start()
    try:
        eis.submit()
        window.module_selector.setCurrentIndex(1)
        cycling.submit()
        window.module_selector.setCurrentIndex(0)
        window.tick()
        assert window.jobs.record(cycling.current_job).state == JobState.QUEUED
        assert window.jobs.record(eis.current_job).state == JobState.RUNNING
        assert window.active_panel is eis
        window.module_selector.setCurrentIndex(1)
        assert window.active_panel is cycling
        assert window.module_selector.isEnabled()
        assert eis._busy
        deadline = time.monotonic() + 45
        while not all(window.jobs.record(panel.current_job).state.terminal for panel in (eis, cycling)):
            app.processEvents()
            time.sleep(0.002)
            assert time.monotonic() < deadline
        for panel in (eis, cycling):
            assert window.jobs.record(panel.current_job).state == JobState.SUCCEEDED
            assert panel.result_path is not None
            assert panel.results.count() >= 4
            assert "ошибка отображения" not in panel.status.text().lower()
        assert eis.models_table.rowCount() >= 1
        assert eis.cases_table.rowCount() == 1
        saved_result = eis.result_path
        saved_plot = eis.results.widget(0)
        window.module_selector.setCurrentIndex(1)
        assert window.active_panel is cycling
        window.module_selector.setCurrentIndex(0)
        assert eis.result_path == saved_result
        assert eis.results.widget(0) is saved_plot
        assert max(b - a for a, b in zip(heartbeats, heartbeats[1:])) < 0.4
    finally:
        timer.stop()
        window.close()


def test_toolbar_switches_context_and_cancels_only_selected_module(tmp_path, monkeypatch):
    app = create_application()
    window = MainWindow(tmp_path, registry=registry())
    window.show()
    eis, cycling = window.panels
    eis.path.setText("eis.txt")
    cycling.path.setText("cycling.csv")
    opened = []
    monkeypatch.setattr(eis, "browse", lambda: opened.append("eis"))
    monkeypatch.setattr(cycling, "browse", lambda: opened.append("cycling"))
    try:
        assert window.history_dock.isHidden()
        assert window.active_panel is eis
        window.open_action.trigger()
        window.module_selector.setCurrentIndex(1)
        window.open_action.trigger()
        assert opened == ["eis", "cycling"]
        window.module_selector.setCurrentIndex(0)
        window.run_action.trigger()
        job = window.jobs.record(eis.current_job)
        assert window.cancel_action.isEnabled()
        window.module_selector.setCurrentIndex(1)
        app.processEvents()
        assert not window.cancel_action.isEnabled()
        window.cancel_action.trigger()
        assert not job.state.terminal
        assert cycling.current_job is None
        window.module_selector.setCurrentIndex(0)
        assert eis.path.text() == "eis.txt"
        assert window.cancel_action.isEnabled()
        window.cancel_action.trigger()
        window.tick()
        assert job.state == JobState.CANCELLED
        assert window.run_action.isEnabled()
    finally:
        window.close()


def test_eis_keeps_results_for_each_loaded_spectrum(tmp_path):
    create_application()
    window = MainWindow(tmp_path)
    eis = window.panels[0]
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    result = {"payload": {"summary": {"Схема": "R0"}, "fits": [], "kk": {"status": "PASS"}}}
    try:
        eis.add_sources([str(first), str(second), str(first)])
        assert eis.cases_table.rowCount() == 2
        eis.result_path = "saved-result.json"
        eis.show_result(result)
        eis.cases_table.setCurrentCell(1, 0)
        assert eis.path.text() == str(second)
        assert eis.result_path is None
        assert not eis.makie_button.isEnabled()
        eis.cases_table.setCurrentCell(0, 0)
        assert eis.result_path == "saved-result.json"
        assert "R0" in eis.summary.text()
        assert eis.datasets[str(first)]["kk"] == "PASS"
        assert eis.pro_panel.isHidden()
        eis.pro_toggle.setChecked(True)
        assert not eis.pro_panel.isHidden()
    finally:
        window.close()


def test_language_changes_labels_preserving_config_result_and_zoom(tmp_path):
    import numpy as np
    from chem_suite.modules.eis.views import spectrum_plots
    from chem_suite.desktop.plots import PlotGridWidget
    from chem_suite.desktop.export_dialog import ExportDialog
    app = create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    try:
        panel.options['circuit'][1].setEditText('R0-p(R1,C1)')
        panel.display_result({'payload': {'summary': {'circuit': 'R0-p(R1,C1)'},
                             'plots': spectrum_plots(np.array([1., 10., 100.]), np.array([3-4j]*3))}})
        panel.results.setCurrentIndex(1)
        app.processEvents()
        grid = panel.results.widget(1).findChild(PlotGridWidget)
        assert len(grid.plots) == 2
        grid.plots[0].chart.setXRange(0, 1, padding=0)
        zoom = grid.plots[0].chart.viewRange()
        before = panel.get_config()
        window.set_language('en')
        assert window.open_action.text() == 'Open…'
        assert window.history_dock.windowTitle() == 'Job history'
        assert grid.plots[1].chart.getPlotItem().titleLabel.text == 'Bode phase'
        assert panel.get_config() == before
        assert grid.plots[0].chart.viewRange() == zoom
        dialog = ExportDialog(panel, 'en', 'eis', 2)
        assert 'Export' in dialog.windowTitle()
        window.set_language('ru')
        assert window.open_action.text() == 'Открыть…'
        assert 'ФЧХ' in grid.plots[1].chart.getPlotItem().titleLabel.text
        assert panel.get_config()['circuit'] == 'R0-p(R1,C1)'
        assert grid.plots[0].chart.viewRange() == zoom
        window.set_language('en')
    finally:
        window.close()
    reopened = MainWindow(tmp_path)
    try:
        assert reopened.language == 'en'
    finally:
        reopened.close()


def test_desktop_folder_batch_export_keeps_selected_analysis(tmp_path):
    import shutil
    app = create_application()
    window = MainWindow(tmp_path / 'workspace')
    window.show()
    panel = window.panels[0]
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    source = Path(__file__).parents[1] / 'examples/eis_double_cpe.txt'
    shutil.copy(source, inputs / 'one.txt')
    shutil.copy(source, inputs / 'two.txt')
    (inputs / 'bad.csv').write_text('bad file\n')
    beats = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    def finished():
        deadline = time.monotonic() + 80
        while panel._busy:
            app.processEvents()
            time.sleep(.002)
            assert time.monotonic() < deadline
    try:
        panel.load_sources([inputs])
        finished()
        assert panel.cases_table.rowCount() == 3
        assert sum(d.get('success', False) for d in panel.datasets.values()) == 2
        good = next(s for s, d in panel.datasets.items() if d['success'])
        panel.cases_table.setCurrentCell(panel.source_rows[good], 0)
        assert panel.results.count() >= 5
        panel.options['circuit'][1].setCurrentText('R0-p(R1,CPE0)-p(R2,CPE1)')
        panel.submit_all()
        finished()
        assert panel.batch_failures == 0
        assert len(panel.batch_jobs) == 2
        assert any(d["state"] == "Load failed" for d in panel.datasets.values())
        assert len(panel.export_paths) == 2
        panel.cases_table.setCurrentCell(panel.source_rows[good], 0)
        selected = panel.result_path
        assert selected
        destination = tmp_path / 'export'
        panel.start_export(panel.export_paths, str(destination), {'excel': False}, [])
        finished()
        assert destination.exists()
        assert panel.result_path == selected
        assert window.batch_export_action.isEnabled()
        assert len(beats) > 20
        assert max(b-a for a, b in zip(beats, beats[1:])) < .4
    finally:
        timer.stop()
        window.close()


def test_batch_cancel_cancels_each_eis_job_without_cancelling_cycling(tmp_path):
    create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    try:
        panel.add_sources([tmp_path / 'one.txt', tmp_path / 'two.txt'])
        panel.submit_all()
        peer = window.jobs.submit(JobRequest('cycling', 'analyze', ('cycling.csv',)))
        assert len(panel.batch_jobs) == 2
        panel.cancel()
        assert all(window.jobs.record(job).state == JobState.CANCELLED for job in panel.batch_jobs)
        assert peer.state == JobState.QUEUED
    finally:
        window.close()


def test_parameter_editor_worker_units_language_and_validation(tmp_path):
    from PySide6.QtCore import Qt
    from tests.test_advanced import rc_spectrum
    app = create_application()
    window = MainWindow(tmp_path / 'workspace')
    panel = window.panels[0]
    source = rc_spectrum(tmp_path / 'rc.csv')
    panel.path.setText(str(source))
    panel.options['circuit'][1].setCurrentText('R0-p(R1,C1)')
    window.show()
    try:
        panel.edit_parameters()
        dialog = panel.parameter_dialog
        deadline = time.monotonic() + 30
        while panel._busy:
            app.processEvents()
            time.sleep(.002)
            assert time.monotonic() < deadline
        assert dialog.table.rowCount() == 3
        assert dialog.table.item(0, 1).text() == 'Ω'
        assert not dialog.table.item(0, 0).flags() & Qt.ItemIsEditable
        assert dialog.table.item(0, 2).flags() & Qt.ItemIsEditable
        dialog.table.item(0, 2).setText('2,0')
        dialog.table.item(0, 3).setText('3')
        dialog.table.item(0, 4).setText('2.5')
        window.set_language('en')
        assert dialog.table.item(0, 2).text() == '2,0'
        dialog.accept()
        assert dialog.isVisible()
        assert dialog.message.text() == 'Lower bound must be below upper bound'
        dialog.table.item(0, 3).setText('1.5')
        dialog.accept()
        assert not dialog.isVisible()
        assert panel.get_config()['parameter_overrides_by_circuit']['R0-p(R1,C1)']['R0']['initial'] == 2
        panel.submit()
        assert window.jobs.record(panel.current_job).request.config['parameter_overrides_by_circuit']['R0-p(R1,C1)']['R0']['lower'] == 1.5
        panel.cancel()
    finally:
        window.close()


def test_eis_fit_and_drt_caches_modes_and_export_controls(tmp_path):
    from chem_suite.desktop.export_dialog import ExportDialog
    create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    source = str(tmp_path / 'one.csv')
    panel.add_sources([source])
    fit = {'payload': {'summary': {'circuit': 'R0'}, 'best': {'parameters': [], 'success': True}}}
    drt = {'payload': {'summary': {'analysis': 'DRT'}, 'drt': {
        'fit': {'peaks': []}, 'selection': {'ranking': []}, 'stability': None}}}
    panel.datasets[source].update(fit_result=fit, fit_result_path='fit.json', drt_result=drt, drt_result_path='drt.json')
    try:
        panel.select_dataset(0)
        assert panel.result_path == 'fit.json'
        assert panel.export_paths == ['fit.json']
        panel.action.setCurrentIndex(panel.action.findData('drt'))
        assert panel.result_path == 'drt.json'
        assert panel.export_paths == ['drt.json']
        assert not panel.spice_button.isEnabled()
        assert 'DRT' in window.run_action.text()
        dialog = ExportDialog(panel, 'en', 'eis', 1, 'drt')
        assert 'drt_distribution' in dialog.boxes and 'spice' not in dialog.boxes
        panel.action.setCurrentIndex(panel.action.findData('fit'))
        assert panel.result_path == 'fit.json'
        assert panel.spice_button.isEnabled()
        dialog = ExportDialog(panel, 'ru', 'eis', 1, 'fit')
        assert 'spice' in dialog.boxes and not dialog.boxes['spice'].isChecked()
    finally:
        window.close()


def test_custom_channel_survives_submit_and_automatic_reloads(tmp_path, monkeypatch):
    create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    source = str(tmp_path / 'channels.csv')
    try:
        panel.add_sources([source])
        panel.channel.setEditText('Z2')
        panel.submit()
        assert window.jobs.record(panel.current_job).request.config['channel'] == 'Z2'
        panel.cancel()
        # Drain cancellation without starting the missing input file.
        window.tick()
        assert not panel._busy
        panel.datasets[source]['requested_channel'] = 'Z2'
        panel.channel.setCurrentIndex(0)
        loaded = []
        monkeypatch.setattr(panel, 'load_sources', lambda paths, channel=None: loaded.append((paths, channel)))
        panel.change_channel()
        assert loaded == [([source], '')]
        panel.append_log('Checking Kramers–Kronig consistency')
        window.set_language('en')
        assert 'Checking Kramers' in panel.log.toPlainText()
        assert 'Analysis' in [label.text() for label in panel.findChildren(__import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel)]
    finally:
        window.close()


def test_candidate_selection_preserves_winner_and_per_spectrum_view(tmp_path, monkeypatch):
    import copy
    from PySide6.QtCore import Qt
    create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    first, second = str(tmp_path / 'one.csv'), str(tmp_path / 'two.csv')
    panel.add_sources([first, second])
    plot = {'schema_version': 1, 'title': 'Nyquist', 'xlabel': 'Re(Z), Ω', 'ylabel': '−Im(Z), Ω', 'xscale': 'linear', 'yscale': 'linear',
            'equal_aspect': True, 'series': [{'label': 'Model', 'kind': 'line', 'x': [1, 2], 'y': [0, 1]}]}
    fits = [{'circuit': 'R0', 'success': True, 'is_best': True, 'status': 'OK', 'parameters': [
                {'name': 'R0', 'value': 2, 'confidence': .1, 'relative_error_percent': 5, 'unit': 'Ohm'}], 'plots': [plot]},
            {'circuit': 'R1', 'success': True, 'is_best': False, 'status': 'WARN', 'parameters': [
                {'name': 'R1', 'value': 7, 'confidence': .2, 'relative_error_percent': 3, 'unit': 'Ohm'}], 'plots': [copy.deepcopy(plot)]}]
    fits[1]['plots'][0]['series'][0]['x'] = [7, 8]
    result = {'payload': {'best': fits[0], 'fits': fits, 'plots': [plot], 'summary': {'circuit': 'R0'}}}
    for source in (first, second):
        panel.datasets[source].update(fit_result=copy.deepcopy(result), fit_result_path=source + '.json')
    submitted = []
    monkeypatch.setattr(window.jobs, 'submit', lambda request: submitted.append(request))
    try:
        panel.options['statistics_noise_fraction'][1].setText('bad inactive setting')
        assert 'statistics_noise_fraction' not in panel.get_config()
        panel.select_dataset(0)
        panel.models_table.setCurrentCell(1, 1)
        assert panel.view_circuit == 'R1'
        assert panel.parameters_table.item(0, 0).text() == 'R1'
        assert panel.parameters_table.item(0, 1).text() == '7'
        assert panel.displayed_result['payload']['best']['circuit'] == 'R0'
        assert panel.result_path == first + '.json'
        assert submitted == []
        assert not panel.parameters_table.item(0, 1).flags() & Qt.ItemIsEditable
        panel.select_dataset(1)
        assert panel.view_circuit == 'R0'
        panel.select_dataset(0)
        assert panel.view_circuit == 'R1'
        window.set_language('en')
        assert 'Viewing circuit: R1' in panel.view_label.text()
        assert 'Export uses the statistical winner.' in panel.view_label.text()
    finally:
        window.close()


def test_preset_dialog_persists_across_restart_and_preserves_literal_names(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    def drain():
        deadline = time.monotonic() + 30
        while panel._busy:
            app.processEvents()
            time.sleep(.002)
            assert time.monotonic() < deadline
    try:
        panel.options['circuit'][1].setEditText('R0-p(R1,C1)')
        panel.parameter_overrides = {'R0-p(R1,C1)': {'R1': {'initial': 5, 'lower': 4, 'upper': 6}}}
        panel.edit_presets()
        drain()
        dialog = panel.preset_dialog
        dialog.name.setText('Parameters')
        dialog.commands[1].click()
        drain()
        window.set_language('en')
        assert dialog.names.currentText() == 'Parameters'
        window.set_language('ru')
        assert dialog.names.currentText() == 'Parameters'
        dialog.reject()
        window.close()
        window = MainWindow(tmp_path)
        panel = window.panels[0]
        panel.edit_presets()
        drain()
        dialog = panel.preset_dialog
        assert dialog.names.currentText() == 'Parameters'
        panel.options['circuit'][1].setEditText('R0')
        panel.options['seed'][1].setValue(999)
        dialog.commands[0].click()
        drain()
        assert panel.get_config()['circuit'] == 'R0-p(R1,C1)'
        assert panel.get_config()['seed'] == 0
        assert panel.parameter_overrides['R0-p(R1,C1)']['R1']['lower'] == 4
    finally:
        window.close()


def test_reliability_and_statistics_views_and_exports_are_separate(tmp_path):
    from chem_suite.desktop.export_dialog import ExportDialog
    create_application()
    window = MainWindow(tmp_path)
    panel = window.panels[0]
    source = str(tmp_path / 'one.csv')
    panel.add_sources([source])
    reliable = {'payload': {'best': {'circuit': 'R0', 'parameters': []}, 'inference': {
        'decision': {'verdict': 'models_indistinguishable', 'reason': 'candidate topologies are not selection-stable',
                     'diffusion_gate': {'diffusion_family_delta_bic': 0.0, 'evaluated': True, 'passed': False,
                                        'family_stability_threshold': .9, 'family_delta_bic_threshold': 10.0}},
        'topology_bootstrap': {'ranking': [{'circuit': 'R0', 'wins': 5, 'fraction_of_accepted': .5}], 'family_ranking': []}},
        'summary': {'verdict': 'models_indistinguishable'}}}
    statistics = {'payload': {'best': {'circuit': 'R1', 'parameters': []}, 'summary': {'circuit': 'R1'}, 'statistics': {
        'bootstrap': {'parameters': [], 'requested': 10, 'accepted': 10}, 'characteristic_support': {}}}}
    panel.datasets[source].update(reliable_result=reliable, reliable_result_path='reliable.json',
                                  statistics_result=statistics, statistics_result_path='statistics.json')
    try:
        panel.action.setCurrentIndex(panel.action.findData('reliable'))
        assert panel.result_path == 'reliable.json'
        assert 'Схемы неразличимы' in panel.decision_label.text()
        assert 'Выбор топологии неустойчив' in panel.decision_label.text()
        assert panel.export_paths == ['reliable.json']
        window.set_language('en')
        assert 'Verdict: Models indistinguishable' in panel.decision_label.text()
        assert 'ΔBIC: 0.0' in panel.decision_label.text()
        assert 'Circuit reliability' in window.run_action.text()
        dialog = ExportDialog(panel, 'en', 'eis', 1, 'reliable')
        assert 'reliability_json' in dialog.boxes and 'spice' not in dialog.boxes
        panel.action.setCurrentIndex(panel.action.findData('statistics'))
        assert panel.result_path == 'statistics.json'
        assert panel.export_paths == ['statistics.json']
        assert any(panel.results.tabText(i) == 'Parameter intervals' for i in range(panel.results.count()))
        assert not panel.spice_button.isEnabled()
        dialog = ExportDialog(panel, 'en', 'eis', 1, 'statistics')
        assert 'statistics_tables' in dialog.boxes and 'profile_plot' in dialog.boxes
        panel.action.setCurrentIndex(panel.action.findData('reliable'))
        assert panel.result_path == 'reliable.json'
    finally:
        window.close()
