"""Shared UI services and fallback panel. Scientific code stays in job workers."""

import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHeaderView, QLabel,
    QLineEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.desktop.plots import PlotWidget
from chem_suite.desktop.i18n import set_text, translate_widgets
from chem_suite.locale import text

STATES = {
    "queued": "В очереди", "running": "Расчёт", "cancelling": "Остановка",
    "succeeded": "Готово", "failed": "Ошибка", "cancelled": "Отменено", "timed_out": "Таймаут",
}


def fill_table(table, rows, columns=None, *, language="ru"):
    if columns is None:
        columns = [(key, key) for key in rows[0]] if rows else []
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels([text(title, language) for _, title in columns])
    for index, (_, title) in enumerate(columns):
        table.horizontalHeaderItem(index).setData(Qt.UserRole + 17, title)
    table.setRowCount(len(rows))
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setAlternatingRowColors(True)
    for i, row in enumerate(rows):
        for j, (key, _) in enumerate(columns):
            value = row.get(key)
            if isinstance(value, float):
                cell_text = f"{value:.6g}"
            elif isinstance(value, list):
                cell_text = ", ".join(str(item) for item in value)
            else:
                cell_text = str(value) if value is not None else "—"
            item = QTableWidgetItem(text(cell_text, language))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setData(Qt.UserRole + 17, cell_text)
            table.setItem(i, j, item)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    if columns:
        table.horizontalHeader().setStretchLastSection(True)


class ModulePanel(QWidget):
    state_changed = Signal()

    def __init__(self, spec, jobs, makie, parent=None):
        super().__init__(parent)
        self.spec, self.jobs, self.makie = spec, jobs, makie
        self.language = "ru"
        self._operation = "analysis"
        self.current_job = self.result_path = self.makie_job = self.makie_view = None
        self._busy = False
        self.makie.plot_ready.connect(self.show_makie)
        self.makie.failed.connect(self.makie_failed)
        self.path = QLineEdit(self)
        self.path.setPlaceholderText("Выберите исходные данные")
        self.action = QComboBox(self)
        for action in spec.actions:
            if action.visible:
                self.action.addItem(action.title, action.id)
        self.options = {}
        for option in spec.options:
            if option.kind == "choice":
                control = QComboBox(self)
                for choice in option.choices:
                    control.addItem(choice, choice)
                control.setCurrentText(str(option.default))
            elif option.kind == "integer":
                control = QSpinBox(self)
                control.setRange(option.minimum, option.maximum)
                control.setValue(int(option.default))
            else:
                control = QLineEdit(str(option.default), self)
            self.options[option.id] = (option, control)
        # These commands also provide a consistent interface for the common toolbar.
        self.run_button = QPushButton("Рассчитать", self)
        self.run_button.setObjectName("primary")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.submit)
        self.cancel_button = QPushButton("Отменить", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.makie_button = QPushButton("Makie", self)
        self.makie_button.setEnabled(False)
        self.makie_button.clicked.connect(self.render_makie)
        for button in (self.run_button, self.cancel_button, self.makie_button):
            button.hide()
        self.status = QLabel("Готов к работе", self)
        self.status.setWordWrap(True)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.results = QTabWidget(self)
        self.summary = QLabel("Откройте данные и запустите анализ.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.summary.setMargin(12)
        self.summary_tab = QScrollArea()
        self.summary_tab.setWidgetResizable(True)
        self.summary_tab.setWidget(self.summary)
        self.results.addTab(self.summary_tab, "Сводка")
        self.build_layout()
        self.path.textChanged.connect(self.inputs_changed)
        self.set_language(self.language)

    def build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        controls = QWidget()
        controls.setMinimumWidth(260)
        left = QVBoxLayout(controls)
        left.setContentsMargins(0, 0, 6, 0)
        data = QGroupBox("Исходные данные")
        data_layout = QVBoxLayout(data)
        data_layout.addWidget(self.path)
        left.addWidget(data)
        settings = QGroupBox("Настройки анализа")
        form = QFormLayout(settings)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.addRow("Анализ", self.action)
        for option, control in self.options.values():
            form.addRow(option.title, control)
        left.addWidget(settings)
        left.addWidget(self.status)
        left.addWidget(self.progress)
        left.addStretch()
        self.splitter.addWidget(controls)
        self.splitter.addWidget(self.results)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([330, 850])
        layout.addWidget(self.splitter)

    def inputs_changed(self):
        self.run_button.setEnabled(bool(self.path.text().strip()) and not self._busy)
        self.state_changed.emit()

    def set_busy(self, busy):
        self._busy = busy
        self.path.setEnabled(not busy)
        self.action.setEnabled(not busy)
        for _, control in self.options.values():
            control.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.makie_button.setEnabled(not busy and self.result_path is not None)
        self.inputs_changed()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, text("Исходные данные", self.language), "", text("Данные (*.txt *.csv *.dat *.mpt *.mpr);;Все файлы (*)", self.language)
        )
        if path:
            self.path.setText(path)

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, text("Папка эксперимента", self.language))
        if path:
            self.path.setText(path)

    def submit(self):
        if self._busy:
            return
        try:
            if not self.path.text().strip():
                raise ValueError("Выберите исходные данные")
            config = self.get_config()
            self._operation = "analysis"
            record = self.jobs.submit(
                JobRequest(self.spec.id, self.action.currentData(), (self.path.text().strip(),), config)
            )
            self.current_job = record.id
            self.set_status("В очереди")
            self.progress.setValue(0)
            self.set_busy(True)
        except Exception as exc:
            self.set_status(str(exc))
            self.state_changed.emit()

    def cancel(self):
        if self.current_job and self._busy:
            self.jobs.cancel(self.current_job)

    def handle_event(self, event):
        if event.job_id != self.current_job:
            return
        self.set_status(event.message)
        self.progress.setValue(round(100 * event.progress))
        record = self.jobs.record(event.job_id)
        if event.state.terminal:
            if record.state == JobState.SUCCEEDED:
                if self._operation == "export":
                    payload = json.loads(Path(record.result_path).read_text())["payload"]
                    self.set_status("Exported to " + payload["destination"])
                    self.last_export = payload["destination"]
                    self.set_busy(False)
                    return
                self.result_path = record.result_path
                try:
                    self.show_result(json.loads(Path(record.result_path).read_text(encoding="utf-8")))
                except Exception as exc:
                    self.set_status(f"Результат сохранён; ошибка отображения: {exc}")
            else:
                self.set_status(f"{record.message}: {record.error}" if record.error else record.message)
            self.set_busy(False)
        self.state_changed.emit()

    def reset_results(self):
        self.makie_job = self.makie_view = None
        while self.results.count():
            widget = self.results.widget(0)
            self.results.removeTab(0)
            if widget is not self.summary_tab:
                widget.deleteLater()
        self.results.addTab(self.summary_tab, "Сводка")

    def clear_workspace(self):
        """Forget the displayed analysis, preserving disk artifacts and settings."""
        if self._busy:
            return
        self.current_job = self.result_path = None
        self._operation = "analysis"
        self.path.clear()
        self.reset_results()
        set_text(self.summary, "Откройте данные и запустите анализ.", self.language)
        self.progress.setValue(0)
        self.makie_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.set_status("Готов к работе")
        self.inputs_changed()
        self.state_changed.emit()

    def show_result(self, result):
        self.reset_results()
        payload = result["payload"]
        summary_text = "\n".join(f"{key}: {value}" for key, value in payload.get("summary", {}).items())
        diagnostics = payload.get("diagnostics", [])
        if diagnostics:
            summary_text += "\n\nДиагностика:\n" + "\n".join(str(d) for d in diagnostics[:20])
        set_text(self.summary, summary_text, self.language)
        for spec in payload.get("plots", []):
            self.results.addTab(PlotWidget(spec, language=self.language), spec["title"])
        for table in payload.get("tables", []):
            widget = QTableWidget()
            fill_table(widget, table["rows"], language=self.language)
            self.results.addTab(widget, table["title"])
        if self.results.count() > 1:
            self.results.setCurrentIndex(1)
        self.set_language(self.language)

    def render_makie(self):
        if self.result_path and not self._busy:
            self.makie_job = self.makie.submit(self.result_path, self.language)
            self.makie_button.setEnabled(False)
            self.set_status("Подготовка графиков Makie…")
            self.state_changed.emit()

    def show_makie(self, request_id, url):
        if request_id != self.makie_job:
            return
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView

            view = QWebEngineView()
            view.loadFinished.connect(lambda ok: None if ok else self.web_failed())
            view.renderProcessTerminated.connect(lambda *_: self.web_failed())
            view.load(QUrl(url))
            self.results.addTab(view, "Makie")
            self.results.setCurrentWidget(view)
            self.makie_view = view
            self.set_status("Графики Makie готовы")
        except Exception as exc:
            self.set_status(f"Не удалось открыть Makie: {exc}")
        self.makie_button.setEnabled(True)
        self.state_changed.emit()

    def web_failed(self):
        self.set_status("Не удалось отобразить графики Makie. Результаты расчёта сохранены.")
        self.makie_button.setEnabled(self.result_path is not None and not self._busy)
        self.state_changed.emit()

    def makie_failed(self, request_id, message):
        if request_id == self.makie_job:
            self.set_status(message)
            self.makie_button.setEnabled(self.result_path is not None and not self._busy)
            self.state_changed.emit()

    def set_status(self, message):
        set_text(self.status, message, self.language)

    def set_language(self, language):
        self.language = language
        translate_widgets(self, language)

    def get_config(self, option_ids=None):
        config = {}
        for name, (option, control) in self.options.items():
            if option_ids is not None and name not in option_ids:
                continue
            if option.kind == "integer":
                value = control.value()
            elif isinstance(control, QComboBox):
                value = control.currentData()
                if control.isEditable() and control.currentText() != control.itemText(control.currentIndex()):
                    value = control.currentText().strip()
                if value is None:
                    value = control.currentText().strip()
            else:
                value = control.text().strip()
                if value and option.kind == "number":
                    value = float(value.replace(",", "."))
            if value != "":
                config[name] = value
        return config

    @property
    def export_paths(self):
        return [self.result_path] if self.result_path else []

    def export_results(self, batch=False):
        from chem_suite.desktop.export_dialog import ExportDialog
        paths = self.export_paths if batch else ([self.result_path] if self.result_path else [])
        if not paths:
            self.set_status("Нет готовых результатов для экспорта.")
            return
        dialog = ExportDialog(self, self.language, self.spec.id, len(paths), getattr(self, "export_action", self.action.currentData()))
        if not dialog.exec():
            return
        parent = QFileDialog.getExistingDirectory(self, text("Папка для экспорта", self.language))
        if not parent:
            return
        destination = Path(parent) / ("Chem-Suite-export-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
        self.start_export(paths, str(destination), dialog.outputs(), dialog.formats())

    def get_export_config(self, outputs):
        return self.get_config()

    def start_export(self, paths, destination, outputs, formats):
        if self._busy:
            return
        record = self.jobs.submit(JobRequest("exports", "export", tuple(paths), {
            "destination": destination, "outputs": outputs, "formats": formats, "language": self.language, "module_config": self.get_export_config(outputs),
        }, timeout_seconds=600))
        self.current_job = record.id
        self._operation = "export"
        self.set_status("В очереди")
        self.progress.setValue(0)
        self.set_busy(True)
