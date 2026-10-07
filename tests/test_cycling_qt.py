import json
import time
from PySide6.QtCore import QTimer
from chem_suite.desktop.app import MainWindow, create_application
from chem_suite.desktop.sessions import capture_panel, attach_result
from chem_suite.modules.cycling.desktop import CyclingPanel
from tests.cycling_fixtures import mpt, ROWS


def finish(app, window):
    deadline = time.monotonic() + 60
    while any(panel._busy for panel in window.panels) or window.session_job:
        app.processEvents()
        time.sleep(0.002)
        assert time.monotonic() < deadline
    app.processEvents()


def test_many_pulse_steps_preview_limit_is_visible_in_both_languages(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    panel = window.panels[1]
    rows = [
        [1, 0, 2 * ns + point, 1.7, 1.5, 10, 0, ns]
        for ns in range(151)
        for point in (0, 1)
    ]
    source = mpt(tmp_path / "pulses.mpt", rows)
    try:
        panel.load_sources([source])
        finish(app, window)
        assert "На графиках показано шагов: 120/151" in panel.view_note.text()
        assert "Полные данные доступны в экспорте" in panel.view_note.text()
        window.set_language("en")
        assert "Steps shown in plots: 120/151" in panel.view_note.text()
        assert "Full data available in export" in panel.view_note.text()
        window.set_language("ru")
        assert "На графиках показано шагов: 120/151" in panel.view_note.text()
    finally:
        window.close()


def test_cycling_workspace_bad_experiment_selection_worker_views_batch_export_and_portable_session(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    window.show()
    panel = window.panels[1]
    assert isinstance(panel, CyclingPanel)
    first = mpt(tmp_path / "one.mpt")
    second = mpt(tmp_path / "two.mpt")
    bad = tmp_path / "bad.mpt"
    bad.write_text("corrupted file")
    beats = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    try:
        window.module_selector.setCurrentIndex(1)
        panel.load_sources([first, bad, second])
        finish(app, window)
        assert len(panel.datasets) == 3 and panel.batch_failures == 1
        assert len(panel.export_paths) == 2
        assert panel.datasets[str(bad)]["state"] == "failed"
        assert panel.result_path == panel.datasets[str(first)]["result_path"]
        assert panel.results.count() == 11
        assert "пар" in panel.summary.text().lower()
        panel.cases_table.setCurrentCell(panel.source_rows[str(second)], 0)
        app.processEvents()
        assert panel.result_path == panel.datasets[str(second)]["result_path"]
        panel.options["selected_cycles"][1].setText("0")
        panel.apply_view()
        finish(app, window)
        assert panel.datasets[str(second)]["view"]["selected_cycles"] == [0]
        assert panel.result_path == panel.datasets[str(second)]["result_path"]
        window.set_language("en")
        assert "Charge/discharge pairs" in panel.summary.text()
        assert panel.pro_toggle.text() == "Advanced settings"
        assert "Voltage vs time" in [panel.results.tabText(i) for i in range(panel.results.count())]
        assert panel.options["voltage_channel"][1].currentData() == "auto"
        panel.start_export(panel.export_paths, str(tmp_path / "batch"), {"excel": False}, [])
        finish(app, window)
        assert panel.result_path == panel.datasets[str(second)]["result_path"]
        assert len(json.loads((tmp_path / "batch" / "manifest.json").read_text())["experiments"]) == 2
        state = {
            "language": "en",
            "active_module": "cycling",
            "panels": [capture_panel(p) for p in window.panels],
        }
        window.start_session("save", (), {"state": state, "destination": str(tmp_path / "session")})
        finish(app, window)
        assert (tmp_path / "session" / "session.json").exists(), panel.status.text()
        first.unlink()
        second.unlink()
        panel.datasets.clear()
        panel.selected_source = None
        window.start_session("restore", (str(tmp_path / "session" / "session.json"),), {})
        finish(app, window)
        assert panel.selected_source == str(second)
        assert panel.result_path and panel.datasets[str(second)]["archived"]
        assert panel.datasets[str(second)]["view"]["selected_cycles"] == [0]
        panel.submit()
        finish(app, window)
        assert panel.batch_failures == 0
        payload = panel.datasets[str(second)]["result"]["payload"]
        assert payload["sources"][0]["path"] == str(second)
        assert payload["cycle_count"] == 1
        assert len(beats) > 50
        assert max(b - a for a, b in zip(beats, beats[1:])) < 0.4
    finally:
        timer.stop()
        window.close()


def test_cycling_selected_last_cycle_and_paged_metrics_are_not_limited_to_first_20_steps(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    panel = window.panels[1]
    rows = []
    for cycle in range(251):
        for source in ROWS:
            row = list(source)
            row[6] = cycle
            row[2] += cycle * 10900
            rows.append(row)
    source = mpt(tmp_path / "251cycles.mpt", rows)
    try:
        panel.path.setText(str(source))
        panel.submit()
        finish(app, window)
        data = panel.datasets[str(source)]
        assert data.get("result"), data.get("error")
        assert data["result"]["payload"]["cycle_count"] == 251
        assert data["result"]["payload"]["selected_cycles"][-1] == 250
        canonical = panel.result_path
        panel.options["selected_cycles"][1].setText("250")
        panel.options["table_page"][1].setValue(2)
        panel.apply_view()
        finish(app, window)
        view = data["view"]
        assert view["selected_cycles"] == [250]
        assert view["tables"][0]["rows"][0]["cycle"] == 200
        assert len(view["tables"][0]["rows"]) == 51
        assert all("250" in curve["label"] for curve in view["plots"][0]["series"])
        assert panel.result_path == canonical
        panel.options["selected_cycles"][1].setText("999")
        panel.apply_view()
        finish(app, window)
        assert panel.result_path == canonical and data["view"] == view
        assert "absent" in panel.status.property("source_text")
        assert "отсутствуют" in panel.status.text()
        assert window.panels[0].run_button.isEnabled() is False
    finally:
        window.close()


def test_open_archived_flowbat_configuration_and_recalculate_without_originals(tmp_path):
    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    panel = window.panels[1]
    folder = tmp_path / "flowbat"
    raw = folder / "01_Raw_data" / "potentiostat"
    raw.mkdir(parents=True)
    source = mpt(raw / "run.mpt")
    configuration = folder / "config.json"
    configuration.write_text(
        json.dumps(
            {
                "experiment_info": {"potentiostat": "BioLogic"},
                "electrolyte": {"concentration_M": 1, "volume_ml": 10},
            }
        )
    )
    try:
        panel.path.setText(str(folder))
        panel.submit()
        finish(app, window)
        canonical = panel.result_path
        source.unlink()
        configuration.unlink()
        attach_result(panel, canonical)
        assert panel.datasets[str(folder)]["archived"]
        panel.submit()
        finish(app, window)
        assert panel.batch_failures == 0
        payload = panel.datasets[str(folder)]["result"]["payload"]
        assert payload["nominal_capacity_origin"] == "electrolyte_nFcV"
        assert payload["sources"][0]["path"] == str(configuration)
    finally:
        window.close()


def test_legacy_generic_cycling_session_with_flowbat_config_can_be_reanalyzed(tmp_path):
    import copy
    from chem_suite.desktop.sessions import apply_panel

    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    panel = window.panels[1]
    folder = tmp_path / "legacy"
    raw = folder / "01_Raw_data" / "potentiostat"
    raw.mkdir(parents=True)
    source = mpt(raw / "run.mpt")
    config = folder / "config.json"
    config.write_text('{"experiment_info":{"potentiostat":"BioLogic"}}')
    try:
        panel.path.setText(str(folder))
        panel.submit()
        finish(app, window)
        path = panel.result_path
        legacy = copy.deepcopy(panel.datasets[str(folder)]["result"])
        legacy["payload"].pop("experiment")
        for provenance in legacy["payload"]["sources"]:
            provenance.pop("role")
        state = {
            "module": "cycling",
            "action": "analyze",
            "path": str(folder),
            "controls": {"format": "auto", "nominal_capacity_mAh": ""},
            "result": path,
        }
        apply_panel(panel, state, {path: legacy})
        assert panel.selected_source == str(folder)
        assert panel.result_path == path
        source.unlink()
        config.unlink()
        panel.submit()
        finish(app, window)
        assert panel.batch_failures == 0
        assert panel.datasets[str(folder)]["result"]["payload"]["cycle_count"] == 1
    finally:
        window.close()


def test_reset_cycling_results_does_not_render_unopened_plot_pages(tmp_path):
    from chem_suite.desktop.plots import LazyPlotPage

    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    panel = window.panels[1]
    try:
        panel.path.setText(str(mpt(tmp_path / "cycle.mpt")))
        panel.submit()
        finish(app, window)
        unopened = [
            panel.results.widget(i)
            for i in range(panel.results.count())
            if isinstance(panel.results.widget(i), LazyPlotPage) and not panel.results.widget(i).loaded
        ]
        assert len(unopened) == 6
        panel.reset_results()
        assert all(not page.loaded for page in unopened)
    finally:
        window.close()
