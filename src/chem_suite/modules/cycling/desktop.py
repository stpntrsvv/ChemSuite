"""Cycling workspace; no instrument parsing or numerical work in the Qt thread."""

import copy
import json
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.desktop.panel import ModulePanel, fill_table
from chem_suite.desktop.plots import LazyPlotPage, PlotWidget
from chem_suite.desktop.i18n import set_text
from chem_suite.locale import text


class CyclingPanel(ModulePanel):
    EMPTY_VIEW_NOTE = (
        "Пустой выбор: до 10 циклов на графике; экспорт — все циклы. "
        "Время разных файлов соединено по длительности записи."
    )

    def __init__(self, *args, **kwargs):
        self.datasets, self.source_rows, self.batch_jobs = {}, {}, {}
        self.selected_source = None
        self.batch_failures = 0
        self._completed_jobs = set()
        super().__init__(*args, **kwargs)
        self.status.setTextFormat(Qt.PlainText)
        self.results.currentChanged.connect(self.load_tab)

    def build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        controls = QWidget()
        controls.setMinimumWidth(330)
        left = QVBoxLayout(controls)
        data = QGroupBox("Эксперименты")
        data_layout = QVBoxLayout(data)
        data_layout.addWidget(self.path)
        self.cases_table = QTableWidget()
        self.cases_table.setMinimumHeight(100)
        self.cases_table.setMaximumHeight(150)
        self.cases_table.currentCellChanged.connect(lambda row, *_: self.select_dataset(row))
        data_layout.addWidget(self.cases_table)
        buttons = QHBoxLayout()
        self.group_button = QPushButton("Объединить файлы опыта…")
        self.group_button.clicked.connect(self.browse_group)
        self.remove_button = QPushButton("Убрать из списка")
        self.remove_button.clicked.connect(self.remove_selected)
        buttons.addWidget(self.group_button)
        buttons.addWidget(self.remove_button)
        data_layout.addLayout(buttons)
        left.addWidget(data)
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        basic = QGroupBox("Настройки анализа")
        form = QFormLayout(basic)
        self.action.hide()  # this workspace has one scientific action
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        self.pro_toggle = QCheckBox("Расширенные настройки")
        self.pro_toggle.toggled.connect(advanced.setVisible)
        advanced.hide()
        view = QGroupBox("Просмотр и экспорт")
        view_form = QFormLayout(view)
        basic_ids = {"format", "nominal_capacity_mAh", "voltage_channel", "current_channel", "voltage_kind"}
        view_ids = {"selected_cycles", "table_page", "export_scope"}
        for option, control in self.options.values():
            label = QLabel(option.title)
            label.setWordWrap(True)
            label.setMaximumWidth(170)
            control.setMinimumWidth(90)
            target = form if option.id in basic_ids else view_form if option.id in view_ids else advanced_form
            target.addRow(label, control)
        self.options["selected_cycles"][1].setPlaceholderText("0, 1, 5-10")
        settings_layout.addWidget(basic)
        settings_layout.addWidget(self.pro_toggle)
        settings_layout.addWidget(advanced)
        settings_layout.addWidget(view)
        self.options["voltage_kind"][1].setToolTip(
            "Ewe может быть потенциалом электрода. Для VE/EE выберите напряжение ячейки."
        )
        self.options["file_cycle_policy"][1].setToolTip(
            "Объединять одинаковые номера можно только для продолжения одного опыта. Порядок файлов сохраняется."
        )
        self.apply_button = QPushButton("Показать выбранные циклы")
        self.apply_button.clicked.connect(self.apply_view)
        view_form.addRow(self.apply_button)
        settings_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(settings)
        left.addWidget(scroll, 1)
        self.view_note = QLabel(self.EMPTY_VIEW_NOTE)
        self.view_note.setWordWrap(True)
        left.addWidget(self.view_note)
        left.addWidget(self.status)
        left.addWidget(self.progress)
        self.splitter.addWidget(controls)
        self.splitter.addWidget(self.results)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([420, 860])
        layout.addWidget(self.splitter)

    def reset_results(self):
        # Removing an active tab must not instantiate every following lazy page.
        was_blocked = self.results.blockSignals(True)
        try:
            super().reset_results()
        finally:
            self.results.blockSignals(was_blocked)

    def clear_workspace(self):
        if self._busy:
            return
        self.datasets.clear()
        self.source_rows.clear()
        self.batch_jobs.clear()
        self._completed_jobs.clear()
        self.batch_failures = 0
        self.selected_source = None
        self.runtime_sources = {}
        self.refresh_datasets()
        self.options["selected_cycles"][1].clear()
        option, control = self.options["table_page"]
        control.setValue(int(option.default))
        set_text(self.view_note, self.EMPTY_VIEW_NOTE, self.language)
        super().clear_workspace()

    def load_tab(self, index):
        page = self.results.widget(index)
        if isinstance(page, LazyPlotPage):
            page.ensure_loaded()

    def inputs_changed(self):
        super().inputs_changed()
        if hasattr(self, "apply_button"):
            self.apply_button.setEnabled(bool(self.result_path) and not self._busy)

    def set_busy(self, busy):
        super().set_busy(busy)
        self.group_button.setEnabled(not busy)
        self.remove_button.setEnabled(not busy)
        self.cases_table.setEnabled(not busy)

    def refresh_datasets(self):
        self.source_rows = {source: index for index, source in enumerate(self.datasets)}
        rows = [
            {
                "file": data["file"],
                "state": data["state"],
                "cycles": data.get("cycles"),
                "error": data.get("error", ""),
            }
            for data in self.datasets.values()
        ]
        self.cases_table.blockSignals(True)
        fill_table(
            self.cases_table,
            rows,
            [("file", "Эксперимент"), ("state", "Состояние"), ("cycles", "Циклов"), ("error", "Сообщение")],
            language=self.language,
        )
        if self.selected_source in self.source_rows:
            self.cases_table.setCurrentCell(self.source_rows[self.selected_source], 0)
        self.cases_table.blockSignals(False)

    def add_sources(self, sources):
        for raw in sources:
            if isinstance(raw, dict):
                key, name, inputs = raw["path"], raw["file"], raw["inputs"]
            else:
                key = str(Path(raw).absolute())
                name, inputs = Path(key).name, [key]
            if key not in self.datasets:
                self.datasets[key] = {"file": name, "inputs": inputs, "state": "Not analyzed"}
        self.refresh_datasets()
        if self.selected_source is None and self.datasets:
            self.select_dataset(0)
        self.state_changed.emit()

    def select_dataset(self, row):
        if self._busy or not 0 <= row < len(self.datasets):
            return
        self.selected_source = list(self.datasets)[row]
        data = self.datasets[self.selected_source]
        self.path.setText(self.selected_source)
        self.result_path = data.get("result_path")
        if data.get("result"):
            self.show_result(data["result"], view=data.get("view"))
        else:
            self.reset_results()
            set_text(self.summary, "Откройте данные и запустите анализ.", self.language)
        self.inputs_changed()

    def remove_selected(self):
        if self._busy or self.selected_source not in self.datasets:
            return
        del self.datasets[self.selected_source]
        self.selected_source = self.result_path = None
        self.refresh_datasets()
        if self.datasets:
            self.select_dataset(0)
        else:
            self.path.clear()
            self.reset_results()
            set_text(self.summary, "Откройте данные и запустите анализ.", self.language)
        self.inputs_changed()

    def browse(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            text("Исходные данные", self.language),
            "",
            text("Данные (*.mpt *.mpr *.csv *.txt);;Все файлы (*)", self.language),
        )
        if paths:
            self.load_sources(paths)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, text("Папка эксперимента", self.language))
        if folder:
            self.load_sources([folder])

    def browse_group(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            text("Объединить файлы опыта…", self.language),
            "",
            text("Данные (*.mpt *.mpr *.csv *.txt);;Все файлы (*)", self.language),
        )
        if paths:
            self.add_sources([{"path": "files:" + uuid4().hex, "file": Path(paths[0]).stem, "inputs": paths}])
            self.select_dataset(len(self.datasets) - 1)
            self.submit()

    def load_sources(self, sources):
        if self._busy:
            return
        record = self.jobs.submit(JobRequest("cycling", "discover", tuple(str(s) for s in sources)))
        self.current_job, self._operation = record.id, "discover"
        self.set_busy(True)
        self.set_status("В очереди")

    def archived_request(self, data, config):
        if data.get("result_path") and data.get("archived", False):
            payload = data["result"]["payload"]
            root = Path(data["result_path"]).parent
            inputs, identities = [], {}
            for provenance in payload["sources"]:
                snapshot = str(root / provenance["snapshot"])
                identities[snapshot] = {"path": provenance["path"], "sha256": provenance["sha256"]}
                if provenance.get("role") == "configuration" or (
                    not provenance.get("role") and Path(provenance["path"]).name == "config.json"
                ):
                    config["config_path"] = snapshot
                else:
                    inputs.append(snapshot)
            config["source_identities"] = identities
            config["experiment_name"] = payload.get("experiment", {}).get("name", data["file"])
            return tuple(inputs)
        return tuple(data["inputs"])

    def queue_analysis(self, sources):
        config = self.get_config(set(self.options) - {"selected_cycles", "table_page", "export_scope"})
        self.batch_jobs, self.batch_failures = {}, 0
        self._operation = "analysis"
        for source in sources:
            data = self.datasets[source]
            settings = copy.deepcopy(config)
            inputs = self.archived_request(data, settings)
            settings["experiment_name"] = data["file"]
            record = self.jobs.submit(JobRequest("cycling", "analyze", inputs, settings))
            self.batch_jobs[record.id] = source
            data["state"] = "Queued"
            self.current_job = record.id
        self.refresh_datasets()
        self.progress.setValue(0)
        self.set_busy(True)
        self.set_status("В очереди")

    def submit(self):
        if self._busy:
            return
        try:
            key = self.path.text().strip()
            if not key:
                raise ValueError("Выберите исходные данные")
            if key not in self.datasets:
                self.add_sources([key])
                key = str(Path(key).absolute())
            self.selected_source = key
            self.queue_analysis([key])
        except Exception as exc:
            self.set_status(str(exc))

    def submit_all(self):
        if not self._busy and self.datasets:
            try:
                self.queue_analysis(list(self.datasets))
            except Exception as exc:
                self.set_status(str(exc))

    def cancel(self):
        if self._operation == "analysis":
            for job_id in self.batch_jobs:
                if not self.jobs.record(job_id).state.terminal:
                    self.jobs.cancel(job_id)
        else:
            super().cancel()

    def handle_event(self, event):
        if self._operation == "export":
            return super().handle_event(event)
        if event.job_id in self._completed_jobs:
            return
        if self._operation == "analysis" and event.job_id in self.batch_jobs:
            record = self.jobs.record(event.job_id)
            data = self.datasets[self.batch_jobs[event.job_id]]
            if event.state.terminal:
                self._completed_jobs.add(event.job_id)
                if record.state == JobState.SUCCEEDED:
                    result = json.loads(Path(record.result_path).read_text(encoding="utf-8"))
                    data.pop("view", None)
                    data.update(
                        state="Completed",
                        result_path=record.result_path,
                        result=result,
                        cycles=result["payload"]["cycle_count"],
                        error="",
                    )
                    if self.batch_jobs[event.job_id] == self.selected_source:
                        self.result_path = record.result_path
                        self.show_result(result)
                else:
                    data.update(state=record.state.value, error=record.error or record.message)
                    self.batch_failures += 1
                self.refresh_datasets()
                done = sum(job in self._completed_jobs for job in self.batch_jobs)
                self.progress.setValue(round(100 * done / len(self.batch_jobs)))
                if done == len(self.batch_jobs):
                    self.set_busy(False)
                    self.set_status(
                        f"Batch completed: {done - self.batch_failures} succeeded, {self.batch_failures} failed"
                    )
            else:
                data["state"] = event.state.value
                self.set_status(event.message)
            self.state_changed.emit()
            return
        if event.job_id != self.current_job:
            return
        self.set_status(event.message)
        self.progress.setValue(round(100 * event.progress))
        if not event.state.terminal:
            return
        self._completed_jobs.add(event.job_id)
        record = self.jobs.record(event.job_id)
        operation = self._operation
        self.set_busy(False)
        if record.state != JobState.SUCCEEDED:
            self.set_status(record.error or record.message)
            return
        payload = json.loads(Path(record.result_path).read_text())["payload"]
        if operation == "discover":
            self.add_sources(payload["experiments"])
            self.select_dataset(self.source_rows[payload["experiments"][0]["path"]])
            self.queue_analysis([item["path"] for item in payload["experiments"]])
        elif operation == "view":
            self.datasets[self.selected_source]["view"] = payload
            self.show_result(self.datasets[self.selected_source]["result"], view=payload)
            self.set_status("Графики обновлены")
        self.state_changed.emit()

    def apply_view(self):
        if self._busy or not self.result_path:
            return
        record = self.jobs.submit(
            JobRequest(
                "cycling", "view", (self.result_path,), self.get_config({"selected_cycles", "table_page"})
            )
        )
        self.current_job, self._operation = record.id, "view"
        self.set_busy(True)
        self.set_status("В очереди")

    def show_result(self, result, *, view=None):
        previous = (
            self.results.currentWidget().property("source_tab") if self.results.currentWidget() else None
        )
        self.reset_results()
        payload = result["payload"]
        display = view or payload
        lines = [f"{key}: {value}" for key, value in payload.get("summary", {}).items()]
        diagnostics = payload.get("diagnostics", [])
        if diagnostics:
            lines += ["", "Диагностика"] + [str(d) for d in diagnostics[:20]]
        set_text(self.summary, "\n".join(lines), self.language)
        for spec in display.get("plots", []):
            page = LazyPlotPage(lambda s=spec: PlotWidget(s, language=self.language))
            self.results.addTab(page, spec["title"])
        for table in display.get("tables", []):
            widget = QTableWidget()
            display_rows = [
                {k: ("Yes" if v else "No") if isinstance(v, bool) else v for k, v in row.items()}
                for row in table["rows"]
            ]
            fill_table(widget, display_rows, language=self.language)
            self.results.addTab(widget, table["title"])
        self.results.setCurrentIndex(0)
        self.set_language(self.language)
        for index in range(self.results.count()):
            if self.results.widget(index).property("source_tab") == previous:
                self.results.setCurrentIndex(index)
                break
        if self.results.currentIndex() == 0 and self.results.count() > 1:
            self.results.setCurrentIndex(1)
        selected = ", ".join(str(c) for c in display.get("selected_cycles", []))
        note = (
            "Показаны циклы: "
            + selected
            + " · Страница: "
            + f"{display.get('table_page', 1)}/{display.get('table_pages', 1)}\n"
            + payload.get("timeline_method", "concatenated_step_durations")
        )
        if display.get("preview_step_total", 0) > display.get("preview_step_count", 0):
            note += (
                "\nНа графиках показано шагов: "
                + f"{display['preview_step_count']}/{display['preview_step_total']}"
                + " · Полные данные доступны в экспорте"
            )
        set_text(self.view_note, note, self.language)

    def set_language(self, language):
        super().set_language(language)
        set_text(self.pro_toggle, "Расширенные настройки", language)
        if self.selected_source and self.datasets.get(self.selected_source, {}).get("result"):
            payload = self.datasets[self.selected_source]["result"]["payload"]
            lines = [f"{k}: {v}" for k, v in payload.get("summary", {}).items()]
            if payload.get("diagnostics"):
                lines += ["", "Диагностика"] + [str(d) for d in payload["diagnostics"][:20]]
            set_text(self.summary, "\n".join(lines), self.language)

    @property
    def export_paths(self):
        return [d["result_path"] for d in self.datasets.values() if d.get("result_path")]

    def capture_session(self):
        saved = {}
        for key, data in self.datasets.items():
            saved[key] = {k: copy.deepcopy(v) for k, v in data.items() if k not in {"result", "archived"}}
            if saved[key].get("result_path"):
                saved[key]["result_path"] = {"$bundle": saved[key]["result_path"]}
        return {
            "datasets": saved,
            "selected_source": self.selected_source,
            "pro": self.pro_toggle.isChecked(),
        }

    def restore_session(self, state, results):
        self.pro_toggle.setChecked(bool(state.get("pro", False)))
        self.datasets = state.get("datasets", {})
        if not self.datasets and state.get("result"):
            self.attach_saved_result(state["result"], results[state["result"]])
            return
        for data in self.datasets.values():
            if data.get("result_path"):
                data["result"] = results[data["result_path"]]
                data["archived"] = True
        self.selected_source = state.get("selected_source")
        self.refresh_datasets()
        if self.selected_source in self.source_rows:
            self.select_dataset(self.source_rows[self.selected_source])
        else:
            self.result_path = None
            self.path.setText(state.get("path", ""))

    def attach_saved_result(self, path, result):
        payload = result["payload"]
        key = payload.get("experiment", {}).get("folder") or payload["sources"][0]["path"]
        if not payload.get("experiment") and Path(key).name == "config.json":
            key = str(Path(key).parent)
        name = payload.get("experiment", {}).get("name", Path(key).name)
        self.datasets[key] = {
            "file": name,
            "state": "Completed",
            "inputs": [key],
            "cycles": payload["cycle_count"],
            "result_path": path,
            "result": result,
            "archived": True,
        }
        self.refresh_datasets()
        self.select_dataset(self.source_rows[key])
