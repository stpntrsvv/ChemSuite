import json
from pathlib import Path
import multiprocessing
import sys

from PySide6.QtCore import Qt, QTimer, QLibraryInfo, QTranslator
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QToolBar, QWidget,
)

from chem_suite.bootstrap import builtins
from chem_suite.core.jobs import JobService
from chem_suite.desktop.frontends import create_panel
from chem_suite.desktop.panel import ModulePanel, STATES  # public fallback panel
from chem_suite.desktop.theme import apply_theme
from chem_suite.desktop.i18n import set_text, translate_widgets
from chem_suite.locale import text
from chem_suite.core.artifacts import write_json


class MainWindow(QMainWindow):
    def __init__(self, workspace, *, registry=None):
        super().__init__()
        self.setWindowTitle("Chem Suite")
        self.resize(1280, 820)
        # One calculation at a time is the ordinary desktop workflow. Process
        # isolation still protects the UI; other clients can use core concurrency.
        self.jobs = JobService(registry or builtins(), workspace, max_active=1)
        from chem_suite.desktop.makie import MakieBridge

        self.makie = MakieBridge(self)
        self.language = "ru"
        self.preferences_path = Path(workspace) / "ui-preferences.json"
        try:
            self.language = json.loads(self.preferences_path.read_text()).get("language", "ru")
        except (OSError, ValueError):
            pass
        self.session_job = None
        self.qt_translator = None
        self.module_stack = QStackedWidget()
        self.setCentralWidget(self.module_stack)
        self.module_selector = QComboBox()
        self.module_selector.setObjectName("moduleSelector")
        self.module_selector.setMinimumWidth(180)
        self.module_selector.setAccessibleName("Рабочий модуль")
        self.panels = []
        for spec in self.jobs.registry.modules():
            if not spec.visible:
                continue
            try:
                panel = create_panel(spec, self.jobs, self.makie)
            except Exception as exc:
                panel = ModulePanel(spec, self.jobs, self.makie)
                panel.set_status(f"Рабочая панель недоступна: {exc}")
            self.module_stack.addWidget(panel)
            self.module_selector.addItem(spec.title, spec.id)
            self.panels.append(panel)
        self.build_toolbar()
        self.build_history()
        file_menu = self.menuBar().addMenu("Файл")
        self.save_session_action = QAction('Save session…', self)
        self.save_session_action.setShortcut('Ctrl+Shift+S')
        self.save_session_action.triggered.connect(self.save_session)
        self.open_session_action = QAction('Open session…', self)
        self.open_session_action.triggered.connect(self.open_session)
        self.open_result_action = QAction('Open saved result…', self)
        self.open_result_action.triggered.connect(self.open_result)
        file_menu.addActions([self.open_action, self.folder_action, self.clear_action, self.open_result_action, self.open_session_action, self.save_session_action, self.export_action, self.batch_export_action])
        analysis_menu = self.menuBar().addMenu("Анализ")
        analysis_menu.addActions([self.run_action, self.run_all_action, self.cancel_action, self.makie_action])
        self.menuBar().addMenu("Вид").addAction(self.history_dock.toggleViewAction())
        self.language_menu = self.menuBar().addMenu("Язык")
        self.language_actions = {}
        for key, label in (("ru", "Русский"), ("en", "English")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, language=key: self.set_language(language))
            self.language_menu.addAction(action)
            self.language_actions[key] = action
        self.about_menu = self.menuBar().addMenu("Справка")
        self.about_action = QAction("О программе", self)
        self.about_action.triggered.connect(self.show_about)
        self.about_menu.addAction(self.about_action)
        self.module_selector.currentIndexChanged.connect(self.select_module)
        for panel in self.panels:
            panel.state_changed.connect(self.sync_actions)
            panel.action.currentIndexChanged.connect(self.sync_actions)
        self.select_module(self.module_selector.currentIndex())
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.set_language(self.language, persist=False)

    @property
    def active_panel(self):
        return self.module_stack.currentWidget()

    def build_toolbar(self):
        self.toolbar = QToolBar("Рабочая панель", self)
        self.toolbar.setObjectName("workspaceToolbar")
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        self.toolbar.addWidget(QLabel(" Модуль "))
        self.toolbar.addWidget(self.module_selector)
        self.toolbar.addSeparator()
        self.open_action = QAction("Открыть…", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(lambda: self.invoke("browse"))
        self.folder_action = QAction("Папка…", self)
        self.folder_action.triggered.connect(lambda: self.invoke("browse_folder"))
        self.clear_action = QAction("Очистить", self)
        self.clear_action.setToolTip("Убрать данные и результаты текущего модуля из окна. Файлы сохраняются.")
        self.clear_action.triggered.connect(lambda: self.invoke("clear_workspace"))
        self.run_action = QAction("Рассчитать", self)
        self.run_action.triggered.connect(lambda: self.invoke("submit"))
        self.run_all_action = QAction("Рассчитать все", self)
        self.run_all_action.triggered.connect(lambda: self.invoke("submit_all"))
        self.export_action = QAction("Экспорт…", self)
        self.export_action.triggered.connect(lambda: self.invoke("export_results"))
        self.batch_export_action = QAction("Пакетный экспорт…", self)
        self.batch_export_action.triggered.connect(lambda: self.active_panel.export_results(batch=True))
        self.cancel_action = QAction("Отменить", self)
        self.cancel_action.triggered.connect(self.cancel_active)
        self.makie_action = QAction("Makie", self)
        self.makie_action.triggered.connect(lambda: self.invoke("render_makie"))
        self.toolbar.addActions([self.open_action, self.folder_action, self.clear_action])
        self.toolbar.addSeparator()
        self.toolbar.addActions([self.run_action, self.run_all_action, self.cancel_action])
        self.toolbar.addSeparator()
        self.toolbar.addActions([self.export_action, self.batch_export_action, self.makie_action])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)

    def build_history(self):
        self.task_table = QTableWidget(0, 4)
        self.task_table.setHorizontalHeaderLabels(["Модуль", "Состояние", "Прогресс", "Сообщение"])
        self.task_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.task_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.task_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.task_table.setAlternatingRowColors(True)
        self.history_dock = QDockWidget("Журнал задач", self)
        self.history_dock.setObjectName("jobHistoryDock")
        self.history_dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.history_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.history_dock.setWidget(self.task_table)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.history_dock)
        self.history_dock.hide()
        self.toolbar.addAction(self.history_dock.toggleViewAction())

    def select_module(self, index):
        if index >= 0:
            self.module_stack.setCurrentIndex(index)
            self.setWindowTitle(f"Chem Suite · {self.module_selector.itemText(index)}")
        self.sync_actions()

    def invoke(self, method):
        if self.active_panel is not None:
            try:
                getattr(self.active_panel, method)()
            except Exception as exc:
                self.active_panel.set_status(str(exc))
            self.sync_actions()

    def sync_actions(self, *_):
        panel = self.active_panel
        if hasattr(self, "save_session_action"):
            available = not self.session_job and not any(p._busy for p in self.panels)
            for action in (self.save_session_action, self.open_session_action, self.open_result_action):
                action.setEnabled(available)
        self.open_action.setEnabled(panel is not None and not panel._busy)
        self.clear_action.setEnabled(panel is not None and not panel._busy and not self.session_job)
        self.folder_action.setVisible(panel is not None and panel.spec.supports_directory)
        self.folder_action.setEnabled(panel is not None and not panel._busy)
        self.run_action.setEnabled(panel is not None and panel.run_button.isEnabled())
        self.cancel_action.setEnabled(bool(self.session_job) or (panel is not None and panel.cancel_button.isEnabled()))
        self.makie_action.setEnabled(panel is not None and panel.makie_button.isEnabled())
        self.run_all_action.setVisible(panel is not None and hasattr(panel, "submit_all"))
        self.run_all_action.setEnabled(panel is not None and not panel._busy and bool(getattr(panel, "datasets", {})) and panel.action.currentData() not in {"series", "joint", "resolution"})
        self.export_action.setEnabled(panel is not None and not panel._busy and panel.result_path is not None)
        self.batch_export_action.setEnabled(panel is not None and not panel._busy and bool(panel.export_paths))
        self.batch_export_action.setVisible(panel is not None and hasattr(panel, "submit_all"))
        set_text(self.run_action, getattr(panel, "run_title", "Рассчитать"), self.language)
        status = panel.status.text() if panel is not None else text("Нет доступных модулей", self.language)
        self.statusBar().showMessage(status)
        self.statusBar().setToolTip(text("Проект", self.language) + f": {self.jobs.workspace}")

    def tick(self):
        events = self.jobs.poll()
        if not events:
            return
        for event in events:
            if event.job_id == self.session_job:
                self.session_event(event)
                continue
            for panel in self.panels:
                try:
                    panel.handle_event(event)
                except Exception as exc:
                    panel.set_status(f"Ошибка отображения: {exc}")
        records = self.jobs.records()[-30:]
        self.task_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [text(self.jobs.registry.module(record.request.module).title, self.language),
                      text(STATES[record.state.value], self.language), f"{record.progress:.0%}", text(record.error or record.message, self.language)]
            for col, value in enumerate(values):
                self.task_table.setItem(row, col, QTableWidgetItem(value))
        self.sync_actions()

    def set_language(self, language, *, persist=True):
        self.language = language if language in {"ru", "en"} else "ru"
        self.about_menu.setTitle("Справка" if self.language == "ru" else "Help")
        self.about_action.setText("О программе" if self.language == "ru" else "About Chem Suite")
        app = QApplication.instance()
        if self.qt_translator is not None:
            app.removeTranslator(self.qt_translator)
            self.qt_translator = None
        if self.language == "ru":
            translator = QTranslator(self)
            if translator.load("qtbase_ru", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
                app.installTranslator(translator)
                self.qt_translator = translator
        for panel in self.panels:
            panel.set_language(self.language)
        translate_widgets(self, self.language)
        for key, action in self.language_actions.items():
            action.setChecked(key == self.language)
        self.select_module(self.module_selector.currentIndex())
        if persist:
            write_json(self.preferences_path, {"language": self.language})

    def show_about(self):
        from chem_suite import __version__
        description = ("Анализ электрохимических данных: импеданс и циклирование."
                       if self.language == "ru" else
                       "Electrochemical data analysis: impedance and battery cycling.")
        QMessageBox.about(self, "Chem Suite", f"<h2>Chem Suite {__version__}</h2><p>{description}</p>"
                          "<p>GNU GPL v3 or later · github.com/stpntrsvv/ChemSuite</p>")

    def closeEvent(self, event):
        if self.qt_translator is not None:
            QApplication.instance().removeTranslator(self.qt_translator)
            self.qt_translator = None
        self.timer.stop()
        self.makie.close()
        for panel in self.panels:
            if panel.makie_view is not None:
                panel.makie_view.close()
        self.jobs.close()
        event.accept()



    def cancel_active(self):
        if self.session_job:
            self.jobs.cancel(self.session_job)
        else:
            self.invoke('cancel')

    def save_session(self):
        from datetime import datetime
        from uuid import uuid4
        from chem_suite.desktop.sessions import capture_panel
        if self.session_job or any(p._busy for p in self.panels):
            return
        parent = QFileDialog.getExistingDirectory(self, text('Session folder', self.language))
        if not parent:
            return
        state = {'language': self.language, 'active_module': self.module_selector.currentData(),
            'panels': [capture_panel(p) for p in self.panels], 'size': [self.width(), self.height()]}
        target = Path(parent) / ('Chem-Suite-session-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])
        self.start_session('save', (), {'state': state, 'destination': str(target)})

    def open_session(self):
        if self.session_job or any(p._busy for p in self.panels):
            return
        path, _ = QFileDialog.getOpenFileName(self, text('Open session…', self.language), '', 'Session (session.json)')
        if path:
            self.start_session('restore', (path,), {})

    def open_result(self):
        if self.session_job or any(p._busy for p in self.panels):
            return
        path, _ = QFileDialog.getOpenFileName(self, text('Open saved result…', self.language), '', 'Scientific result (result.json)')
        if path:
            self.start_session('result', (path,), {})

    def start_session(self, action, inputs, config):
        from chem_suite.core.contracts import JobRequest
        if self.session_job or any(p._busy for p in self.panels):
            return
        record = self.jobs.submit(JobRequest('sessions', action, tuple(inputs), config))
        self.session_job = record.id
        for panel in self.panels:
            panel.set_busy(True)
            panel.set_status('Saving session' if action == 'save' else 'Opening saved analysis')
        self.sync_actions()

    def session_event(self, event):
        from chem_suite.core.contracts import JobState
        from chem_suite.desktop.sessions import prepare_state, apply_panel, attach_result
        if not event.state.terminal:
            self.statusBar().showMessage(text(event.message, self.language))
            return
        record = self.jobs.record(event.job_id)
        self.session_job = None
        for panel in self.panels:
            panel.set_busy(False)
        try:
            if record.state != JobState.SUCCEEDED:
                raise ValueError(record.error or record.message)
            payload = json.loads(Path(record.result_path).read_text())['payload']
            if record.request.action == 'restore':
                state, results = prepare_state(payload['state'])
                panels = {p.spec.id: p for p in self.panels}
                if any(item['module'] not in panels for item in state['panels']):
                    raise ValueError('Session module is unavailable')
                for item in state['panels']:
                    apply_panel(panels[item['module']], item, results)
                self.set_language(state['language'])
                self.module_selector.setCurrentIndex(self.module_selector.findData(state['active_module']))
                size = state.get('size', [1280, 820])
                self.resize(*size)
            elif record.request.action == 'result':
                panel = next(p for p in self.panels if p.spec.id == payload['module'])
                attach_result(panel, payload['result_path'])
                self.module_selector.setCurrentIndex(self.panels.index(panel))
            else:
                self.active_panel.set_status('Session saved to ' + payload['destination'])
        except Exception as exc:
            self.active_panel.set_status(str(exc))
        self.sync_actions()


def create_application():
    """Create the same themed application for the launcher and desktop checks."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from chem_suite import __version__
    app.setApplicationName("Chem Suite")
    app.setApplicationDisplayName("Chem Suite")
    app.setOrganizationName("ChemSuite")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "chemsuite.png")))
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChemSuite.Desktop")
    apply_theme(app)
    return app


def launch(workspace):
    multiprocessing.freeze_support()
    app = create_application()
    window = MainWindow(workspace)
    window.show()
    return app.exec()
