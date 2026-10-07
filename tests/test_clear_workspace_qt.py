import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt

from chem_suite.core.contracts import JobEvent, JobState
from chem_suite.desktop.app import MainWindow, create_application
from chem_suite.desktop.sessions import capture_panel
from tests.cycling_fixtures import mpt
from tests.test_cycling_qt import finish


def test_clear_current_module_preserves_other_module_files_settings_and_history(tmp_path, monkeypatch):
    app = create_application()
    window = MainWindow(tmp_path / "workspace")
    eis, cycling = window.panels
    source = mpt(tmp_path / "cycling.mpt")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        cycling.options["nominal_capacity_mAh"][1].setText("20")
        cycling.load_sources([source])
        finish(app, window)
        cycling_result = cycling.result_path
        eis.load_sources([Path(__file__).parents[1] / "examples/eis_double_cpe.txt"])
        finish(app, window)
        assert eis.datasets and eis.results.count() > 1
        eis_source = eis.selected_source
        preview_path = eis.datasets[eis_source]["preview_path"]
        # Existing completed references are forgotten along with aggregates.
        eis.datasets[eis_source]["fit_result_path"] = preview_path
        eis.result_path = preview_path
        eis.aggregate_results["series"] = {"result_path": preview_path}
        eis.runtime_sources["previous source"] = {"snapshot_path": preview_path}
        # Session restoration can give both panels the same snapshot lookup.
        cycling.runtime_sources = eis.runtime_sources
        eis.options["restarts"][1].setValue(3)
        eis.append_log("Previous analysis")
        window.sync_actions()
        records = [record.id for record in window.jobs.records()]
        # Clearing must not instantiate lazy plots that are being discarded.
        from chem_suite.desktop.plots import LazyPlotPage

        loaded = []
        monkeypatch.setattr(LazyPlotPage, "ensure_loaded", lambda page: loaded.append(page))
        window.clear_action.trigger()
        assert loaded == []
        assert not eis.datasets and not eis.source_rows and not eis.batch_jobs
        assert not eis.aggregate_results and not eis.runtime_sources
        assert not eis.log_messages and not eis.log.toPlainText()
        assert eis.selected_source is eis.result_path is eis.current_job is None
        assert eis.displayed_result is None and eis.view_circuit is None
        assert eis.models_table.rowCount() == eis.cases_table.rowCount() == 0
        assert eis.progress.value() == 0 and eis.path.text() == ""
        assert eis.options["restarts"][1].value() == 3
        assert not window.export_action.isEnabled() and not window.makie_action.isEnabled()
        assert not window.run_action.isEnabled()
        assert capture_panel(eis)["datasets"] == {}
        assert capture_panel(eis)["aggregates"] == {}
        assert cycling.result_path == cycling_result
        assert cycling.runtime_sources["previous source"]["snapshot_path"] == preview_path
        assert Path(preview_path).is_file() and Path(cycling_result).is_file()
        assert [record.id for record in window.jobs.records()] == records
        window.module_selector.setCurrentIndex(1)
        cycling.options["selected_cycles"][1].setText("0")
        cycling.options["table_page"][1].setValue(2)
        window.clear_action.trigger()
        assert loaded == []
        assert cycling.selected_source is cycling.result_path is cycling.current_job is None
        assert not cycling.datasets and not cycling.batch_jobs and not cycling.source_rows
        assert not cycling._completed_jobs and cycling.batch_failures == 0
        assert not cycling.runtime_sources
        assert cycling.cases_table.rowCount() == 0 and cycling.results.count() == 1
        assert cycling.progress.value() == 0 and cycling.path.text() == ""
        assert cycling.options["nominal_capacity_mAh"][1].text() == "20"
        assert cycling.options["selected_cycles"][1].text() == ""
        assert cycling.options["table_page"][1].value() == 1
        assert not cycling.apply_button.isEnabled()
        assert capture_panel(cycling)["datasets"] == {}
        assert capture_panel(cycling)["result"] is None
        assert Path(cycling_result).is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        assert not window.export_action.isEnabled() and not window.batch_export_action.isEnabled()
        assert not window.makie_action.isEnabled() and not window.run_action.isEnabled()
        # Late notifications cannot repopulate a cleared module.
        cycling.handle_event(JobEvent(records[0], JobState.SUCCEEDED, 1.0, "Ready"))
        assert not cycling.datasets and cycling.results.count() == 1
        # The same file can be processed again after clearing.
        monkeypatch.undo()
        cycling.load_sources([source])
        finish(app, window)
        assert cycling.result_path and cycling.datasets[str(source)]["cycles"] == 1
    finally:
        window.close()


def test_clear_disabled_during_jobs_and_session_then_translates_to_english(tmp_path):
    create_application()
    window = MainWindow(tmp_path / "workspace")
    try:
        for index, panel in enumerate(window.panels):
            window.module_selector.setCurrentIndex(index)
            panel.path.setText("current input.mpt")
            panel.set_busy(True)
            assert not window.clear_action.isEnabled()
            panel.clear_workspace()
            assert panel.path.text() == "current input.mpt"
            panel.set_busy(False)
            assert window.clear_action.isEnabled()
        window.session_job = "pending-session"
        window.sync_actions()
        assert not window.clear_action.isEnabled()
        window.session_job = None
        window.set_language("en", persist=False)
        assert window.clear_action.text() == "Clear"
        assert "Files are preserved" in window.clear_action.toolTip()
        window.clear_action.trigger()
        assert window.active_panel.status.text() == "Ready"
    finally:
        window.session_job = None
        window.close()


def test_fallback_panel_clear_ignores_old_result_and_makie_callbacks(tmp_path):
    from tests.test_isolation import registry

    create_application()
    window = MainWindow(tmp_path / "workspace", registry=registry())
    panel = window.active_panel
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"payload": {"summary": {"test": "ready"}}}))
    try:
        panel.result_path = str(result_path)
        panel.current_job, panel.makie_job = "old-job", "old-makie"
        panel.path.setText("old-file")
        window.sync_actions()
        window.clear_action.trigger()
        assert panel.current_job is panel.result_path is panel.makie_job is None
        panel.handle_event(JobEvent("old-job", JobState.SUCCEEDED, 1.0, "Ready"))
        panel.show_makie("old-makie", "http://unused")
        assert panel.results.count() == 1
        assert panel.summary.alignment() & Qt.AlignTop
        assert result_path.is_file()
    finally:
        window.close()
