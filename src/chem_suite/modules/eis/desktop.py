"""EIS workspace; imports, fitting and exports are supervised process jobs."""
import json
from html import escape
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QLabel, QPushButton,
                               QSplitter, QTableWidget, QTextEdit, QVBoxLayout, QHeaderView, QWidget, QScrollArea)
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.desktop.panel import ModulePanel, STATES, fill_table
from chem_suite.desktop.i18n import set_text
from chem_suite.desktop.plots import PlotWidget, PlotGridWidget, LazyPlotPage
from chem_suite.locale import text


class EisPanel(ModulePanel):
    @property
    def run_title(self):
        return {'drt': 'DRT analysis', 'reliable': 'Circuit reliability',
                'statistics': 'Parameter diagnostics', 'series': 'Series analysis',
                'joint': 'Joint SOC fit', 'resolution': 'Synthetic resolution map'}.get(self.action.currentData(), 'Подбор схемы')

    def build_layout(self):
        self.datasets, self.source_rows, self.batch_jobs = {}, {}, {}
        self.selected_source = self.job_source = None
        self.batch_finished = set()
        self.batch_failures = 0
        self.parameter_overrides = {}
        self.aggregate_results = {}
        self.runtime_sources = {}
        self.log_messages = []
        self.parameter_dialog = None
        self.preset_dialog = None
        self.displayed_result = None
        self.view_circuit = None
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QSplitter(Qt.Vertical)
        left.setChildrenCollapsible(False)
        left.setMinimumWidth(320)
        self.data_box = QGroupBox('Данные и расчёт')
        controls = QVBoxLayout(self.data_box)
        controls.addWidget(self.path)
        basic = QFormLayout()
        basic.addRow('Анализ', self.action)
        old = self.options['channel'][1]
        old.deleteLater()
        self.channel = QComboBox()
        self.channel.setEditable(True)
        self.channel.addItem('Автоматически', '')
        self.options['channel'] = (self.options['channel'][0], self.channel)
        basic.addRow('Канал', self.channel)
        controls.addLayout(basic)
        self.pro_toggle = QPushButton('Расширенный режим', self)
        self.pro_toggle.setCheckable(True)
        controls.addWidget(self.pro_toggle)
        self.pro_panel = QGroupBox('Расширенные настройки')
        advanced = QFormLayout(self.pro_panel)
        advanced.setRowWrapPolicy(QFormLayout.WrapLongRows)
        advanced.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for _, control in self.options.values():
            control.setMinimumWidth(140)
        self.options['candidate_circuits'][1].setMinimumWidth(220)
        self.options['candidate_circuits'][1].setPlaceholderText('Empty = adaptive pool or selected circuit')
        self.options['circuit'][1].setEditable(True)
        self.fit_controls = []
        for name in ('circuit', 'candidate_circuits', 'restarts', 'max_evaluations', 'seed'):
            option, control = self.options[name]
            advanced.addRow(option.title, control)
            if name != 'seed':
                self.fit_controls.extend([advanced.labelForField(control), control])
        self.parameter_button = QPushButton('Parameter values and bounds…')
        self.parameter_button.clicked.connect(self.edit_parameters)
        advanced.addRow(self.parameter_button)
        self.fit_controls.append(self.parameter_button)
        self.presets_button = QPushButton('User presets…')
        self.presets_button.clicked.connect(self.edit_presets)
        advanced.addRow(self.presets_button)
        self.reliable_controls = []
        for name in ('reliability_restarts', 'reliability_samples', 'reliability_drt_samples'):
            option, control = self.options[name]
            advanced.addRow(option.title, control)
            self.reliable_controls.extend([advanced.labelForField(control), control])
        self.import_button = QPushButton('Import reliable result…')
        self.import_button.clicked.connect(self.import_reliable)
        advanced.addRow(self.import_button)
        self.reliable_controls.append(self.import_button)
        self.statistics_controls = []
        for name in ('statistics_method', 'statistics_samples', 'statistics_noise_fraction', 'profile_parameter',
                     'profile_points', 'profile_span_decades', 'window_check'):
            option, control = self.options[name]
            advanced.addRow(option.title, control)
            self.statistics_controls.extend([advanced.labelForField(control), control])
        self.drt_controls = []
        for name in ('drt_lambda_grid', 'drt_tau_points', 'drt_folds', 'drt_stability_samples'):
            option, control = self.options[name]
            advanced.addRow(option.title, control)
            self.drt_controls.extend([advanced.labelForField(control), control])
        self.spice_button = QPushButton('SPICE package…')
        self.spice_button.clicked.connect(self.export_spice)
        advanced.addRow(self.spice_button)
        self.spice_controls = [self.spice_button]
        option, control = self.options['ngspice_executable']
        advanced.addRow(option.title, control)
        self.spice_controls.extend([advanced.labelForField(control), control])
        self.controller_button = QPushButton('Controller C package…')
        self.controller_button.clicked.connect(self.export_controller)
        advanced.addRow(self.controller_button)
        self.spice_controls.append(self.controller_button)
        for name in ('controller_sample_period_s', 'controller_current_full_scale_a', 'controller_max_frequency_hz'):
            option, control = self.options[name]
            advanced.addRow(option.title, control)
            self.spice_controls.extend([advanced.labelForField(control), control])
        self.series_controls, self.joint_controls, self.resolution_controls = [], [], []
        for names, widgets in [
            (('series_manifest',), self.series_controls),
            (('joint_smoothness', 'joint_cv_grid', 'joint_cv_folds'), self.joint_controls),
            (('resolution_min_frequencies', 'resolution_noise_fractions', 'resolution_max_frequency',
              'resolution_replicates', 'resolution_points'), self.resolution_controls),
        ]:
            for name in names:
                option, control = self.options[name]
                advanced.addRow(option.title, control)
                widgets.extend([advanced.labelForField(control), control])
        self.manifest_button = QPushButton('Choose SOC manifest…')
        self.manifest_button.clicked.connect(self.browse_manifest)
        advanced.addRow(self.manifest_button)
        self.series_controls.append(self.manifest_button)
        self.options['series_manifest'][1].textChanged.connect(self.inputs_changed)
        self.pro_scroll = QScrollArea()
        self.pro_scroll.setWidgetResizable(True)
        self.pro_scroll.setWidget(self.pro_panel)
        self.pro_scroll.setMinimumHeight(120)
        self.pro_scroll.setMaximumHeight(340)
        controls.addWidget(self.pro_scroll)
        self.pro_scroll.hide()
        self.pro_panel.hide()
        self.pro_toggle.toggled.connect(self.pro_panel.setVisible)
        self.pro_toggle.toggled.connect(self.pro_scroll.setVisible)
        controls.addWidget(self.status)
        controls.addWidget(self.progress)
        left.addWidget(self.data_box)
        self.cases_table = QTableWidget()
        self.cases_table.setMinimumHeight(90)
        self.refresh_datasets()
        self.cases_table.currentCellChanged.connect(self.select_dataset)
        left.addWidget(self.cases_table)
        self.models_table = QTableWidget()
        self.models_table.setMinimumHeight(100)
        self.models_table.setToolTip('Select a fitted circuit to inspect its graphs and parameters.')
        self.fill_models([])
        self.models_table.currentCellChanged.connect(self.select_model)
        left.addWidget(self.models_table)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(200)
        self.log.setPlaceholderText('Журнал EIS')
        self.log.setMinimumHeight(70)
        left.addWidget(self.log)
        left.setSizes([230, 150, 240, 100])
        left.setStretchFactor(2, 2)
        self.splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.view_label = QLabel()
        self.view_label.setWordWrap(True)
        right_layout.addWidget(self.view_label)
        right_layout.addWidget(self.results)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([420, 800])
        layout.addWidget(self.splitter)
        self.empty_results()
        self.results.currentChanged.connect(self.activate_tab)
        self.channel.activated.connect(self.change_channel)
        self.channel.lineEdit().editingFinished.connect(self.change_channel)
        self.action.currentIndexChanged.connect(self.change_mode)
        self.options['statistics_method'][1].currentIndexChanged.connect(self.update_noise_control)
        self.change_mode()

    def tab(self, widget, title):
        widget.setProperty('source_tab', title)
        self.results.addTab(widget, text(title, self.language))

    def empty_results(self):
        self.displayed_result = None
        self.view_circuit = None
        if hasattr(self, 'view_label'):
            self.view_label.clear()
            self.view_label.setProperty('source_text', '')
        self.reset_results()
        self.results.removeTab(0)
        for title in ('Nyquist', 'Bode', 'Parameters', 'KK', 'Parser'):
            label = QLabel()
            set_text(label, 'Откройте спектр и запустите подбор схемы.', self.language)
            label.setAlignment(Qt.AlignCenter)
            self.tab(label, title)
        self.tab(self.summary_tab, 'Summary')
        self.results.setCurrentIndex(0)
        self.fill_models([])
        set_text(self.summary, 'Откройте спектры через кнопку «Открыть…» в верхней панели.', self.language)

    def clear_workspace(self):
        if self._busy:
            return
        for name in ('parameter_dialog', 'preset_dialog'):
            dialog = getattr(self, name)
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
                setattr(self, name, None)
        self.datasets.clear()
        self.source_rows.clear()
        self.batch_jobs.clear()
        self.batch_finished.clear()
        self.aggregate_results.clear()
        # Restored panels can share the session's snapshot lookup.
        self.runtime_sources = {}
        self.log_messages.clear()
        self.batch_failures = 0
        self.selected_source = self.job_source = None
        self.refresh_datasets()
        self.log.clear()
        blocked = self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItem(text('Automatic', self.language), '')
        self.channel.blockSignals(blocked)
        self.options['series_manifest'][1].clear()
        # Removing tabs must not build every lazy plot on the way out.
        blocked = self.results.blockSignals(True)
        try:
            super().clear_workspace()
            self.empty_results()
        finally:
            self.results.blockSignals(blocked)
        self.update_engineering_buttons()
        self.state_changed.emit()

    def add_sources(self, paths):
        if self._busy:
            return
        for path in paths:
            source = str(Path(path).expanduser().resolve())
            self.datasets.setdefault(source, {'file': Path(source).name, 'state': 'Not fitted', 'kk': '—'})
        self.refresh_datasets()
        if paths:
            source = str(Path(paths[0]).expanduser().resolve())
            self.cases_table.blockSignals(True)
            self.cases_table.setCurrentCell(self.source_rows[source], 0)
            self.cases_table.blockSignals(False)
            self.select_dataset(self.cases_table.currentRow())

    def refresh_datasets(self):
        self.source_rows = {source: row for row, source in enumerate(self.datasets)}
        self.cases_table.blockSignals(True)
        action = self.action.currentData()
        rows = [{**dataset, 'state': text(dataset.get(action + '_state', dataset['state']), self.language)}
                for dataset in self.datasets.values()]
        fill_table(self.cases_table, rows, [('file', 'File'), ('state', 'State'), ('point_count', 'Points'),
                   ('channel', 'Channel'), ('kk', 'KK')], language=self.language)
        self.cases_table.horizontalHeader().setStretchLastSection(False)
        self.cases_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for row, source in enumerate(self.datasets):
            self.cases_table.item(row, 0).setToolTip(source)
        self.cases_table.blockSignals(False)

    def select_dataset(self, row, *_):
        if row < 0 or row >= len(self.datasets) or (self._busy and self._operation != 'analysis'):
            return
        source = list(self.datasets)[row]
        self.selected_source = source
        self.path.setText(source)
        if self.action.currentData() in {'series', 'joint', 'resolution'}:
            self.display_aggregate()
            return
        dataset = self.datasets[source]
        action = self.action.currentData()
        dataset['result'] = dataset.get(action + '_result') or (dataset.get('result') if not any(k.endswith('_result') for k in dataset) else None)
        dataset['result_path'] = dataset.get(action + '_result_path') or (dataset.get('result_path') if not any(k.endswith('_result_path') for k in dataset) else None)
        self.result_path = dataset.get('result_path')
        self.makie_button.setEnabled(self.result_path is not None and not self._busy)
        self.channel.blockSignals(True)
        previous = self.get_channel()
        self.channel.clear()
        self.channel.addItem(text('Automatic', self.language), '')
        for channel in dataset.get('channels', []):
            self.channel.addItem(channel, channel)
        selected = self.channel.findData(previous)
        self.channel.setCurrentIndex(max(0, selected))
        if selected < 0 and previous:
            self.channel.setEditText(previous)
        self.channel.blockSignals(False)
        if dataset.get('result'):
            self.display_result(dataset['result'])
        elif dataset.get('preview_path'):
            self.display_result(json.loads(Path(dataset['preview_path']).read_text()))
        else:
            self.empty_results()
        if not self._busy:
            self.set_status('Completed' if self.result_path else dataset.get(action + '_state', 'Loaded' if dataset.get('preview_path') else dataset['state']))
            self.progress.setValue(100 if self.result_path else 0)
        self.update_engineering_buttons()
        self.state_changed.emit()

    def load_sources(self, paths, channel=None):
        if self._busy or not paths:
            return
        if channel is None:
            channel = self.get_channel() or None
        aliases = {data['runtime_source']: data['source_identity'] for source, data in self.datasets.items() if source in [str(p) for p in paths] and data.get('runtime_source') and data.get('source_identity')}
        inputs = tuple(self.datasets.get(str(p), {}).get('runtime_source', str(p)) for p in paths)
        record = self.jobs.submit(JobRequest('eis', 'load', inputs, {
            'recursive': True, 'channel': channel, 'source_aliases': aliases,
        }))
        self.current_job = record.id
        self._operation = 'load'
        self.set_status('Чтение данных')
        self.progress.setValue(0)
        self.set_busy(True)

    def browse(self):
        if self.action.currentData() == 'joint':
            self.browse_manifest()
            return
        paths, _ = QFileDialog.getOpenFileNames(self, text('EIS spectra', self.language), '',
                     text('EIS data (*.txt *.csv *.dat *.mpt *.mpr);;All files (*)', self.language))
        self.load_sources(paths)

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, text('Open EIS folder', self.language))
        if path:
            self.load_sources([path])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self._busy:
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.load_sources([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def change_channel(self, *_):
        if self.selected_source and not self._busy:
            channel = self.get_channel()
            if (channel or '') != (self.datasets[self.selected_source].get('requested_channel') or ''):
                self.load_sources([self.selected_source], channel or '')

    def submit(self):
        if self._busy:
            return
        if self.action.currentData() in {'series', 'joint', 'resolution'}:
            self.submit_aggregate()
            return
        if not self.path.text().strip():
            self.set_status('Выберите исходные данные')
            return
        self.add_sources([self.path.text().strip()])
        self.start_fits([self.selected_source])

    def submit_all(self):
        if self.action.currentData() in {'series', 'joint', 'resolution'}:
            self.submit()
            return
        if not self._busy:
            self.start_fits([source for source, data in self.datasets.items() if data['state'] != 'Load failed'])

    def start_fits(self, paths):
        if not paths:
            return
        try:
            config = self.get_config()
            if config.get('circuit') in ('Automatic', 'Автоматически', ''):
                config['circuit'] = 'adaptive'
            records = []
            for source in paths:
                action = self.action.currentData()
                item_config = dict(config)
                if self.datasets[source].get('source_identity'):
                    item_config['source_identity'] = self.datasets[source]['source_identity']
                if action == 'statistics' and config.get('circuit') == 'adaptive':
                    saved = self.datasets[source].get('fit_result', {}).get('payload', {})
                    item_config['circuit'] = self.datasets[source].get('view_circuits', {}).get('fit') or saved.get('best', {}).get('circuit', 'adaptive')
                    item_config['candidate_circuits'] = ''
                record = self.jobs.submit(JobRequest('eis', action, (self.datasets[source].get('runtime_source', source),), item_config))
                self.datasets[source].pop(action + '_result', None)
                self.datasets[source].pop(action + '_result_path', None)
                records.append(record)
                self.datasets[source].pop('result', None)
                self.datasets[source].pop('result_path', None)
                self.datasets[source]['state'] = 'Queued'
                self.datasets[source][action + '_state'] = 'Queued'
            self.batch_jobs = {record.id: source for record, source in zip(records, paths)}
            self.batch_finished = set()
            self.batch_failures = 0
            self.current_job = records[-1].id
            self._operation = 'analysis'
            self.refresh_datasets()
            self.result_path = None
            self.set_status('В очереди')
            self.progress.setValue(0)
            self.set_busy(True)
        except Exception as exc:
            self.set_status(str(exc))

    def set_busy(self, busy):
        self.cases_table.setEnabled(not busy or self._operation == 'analysis')
        self.models_table.setEnabled(not busy)
        self.presets_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.parameter_button.setEnabled(not busy and self.action.currentData() != 'drt')
        self.manifest_button.setEnabled(not busy)
        super().set_busy(busy)
        self.update_engineering_buttons()

    def cancel(self):
        if self._busy and self._operation == 'analysis' and self.batch_jobs:
            for job_id in self.batch_jobs:
                self.jobs.cancel(job_id)
        else:
            super().cancel()

    def handle_event(self, event):
        if self._operation == 'aggregate' and event.job_id == self.current_job:
            self.set_status(event.message)
            self.progress.setValue(round(event.progress*100))
            if event.state.terminal:
                record = self.jobs.record(event.job_id)
                if record.state == JobState.SUCCEEDED:
                    result = json.loads(Path(record.result_path).read_text())
                    self.aggregate_results[record.request.action] = {'result': result, 'result_path': record.result_path}
                    self.display_aggregate()
                else:
                    self.set_status(record.error or record.message)
                self.set_busy(False)
            self.state_changed.emit()
            return
        if self._operation == 'presets' and event.job_id == self.current_job:
            if event.state.terminal:
                record = self.jobs.record(event.job_id)
                self.set_busy(False)
                if record.state == JobState.SUCCEEDED:
                    payload = json.loads(Path(record.result_path).read_text())['payload']
                    if payload.get('selected'):
                        self.apply_preset(payload['selected'])
                    if self.preset_dialog is not None:
                        self.preset_dialog.load_result(payload)
                    self.set_status('Preset loaded' if payload.get('selected') else 'Preset list updated')
                else:
                    self.set_status(record.error or record.message)
                    if self.preset_dialog is not None:
                        self.preset_dialog.set_busy(False)
                        set_text(self.preset_dialog.message, record.error or record.message, self.language)
            self.state_changed.emit()
            return
        if self._operation == 'parameters' and event.job_id == self.current_job:
            self.set_status(event.message)
            if event.state.terminal:
                record = self.jobs.record(event.job_id)
                self.set_busy(False)
                dialog = self.parameter_dialog
                if dialog is not None and dialog.isVisible():
                    dialog.set_busy(False)
                    if record.state == JobState.SUCCEEDED:
                        dialog.load_parameters(json.loads(Path(record.result_path).read_text())['payload'])
                    else:
                        dialog.message.setText(text(record.error or record.message, self.language))
                self.set_status(record.error or record.message)
            self.state_changed.emit()
            return
        if self._operation == 'load' and event.job_id == self.current_job:
            self.set_status(event.message)
            self.progress.setValue(round(event.progress * 100))
            if event.state.terminal:
                record = self.jobs.record(event.job_id)
                self.set_busy(False)
                if record.state == JobState.SUCCEEDED:
                    payload = json.loads(Path(record.result_path).read_text())['payload']
                    entries = payload['entries']
                    self.add_sources([entry['path'] for entry in entries])
                    for entry in entries:
                        dataset = self.datasets[entry['path']]
                        dataset.pop('result', None)
                        dataset.pop('result_path', None)
                        dataset.pop('preview_path', None)
                        for key in [key for key in dataset if key.endswith(('_result', '_result_path', '_state'))] + ['view_circuits']:
                            dataset.pop(key, None)
                        dataset.update(entry)
                        dataset['requested_channel'] = record.request.config.get('channel')
                        dataset['state'] = 'Loaded' if entry['success'] else 'Load failed'
                        if not entry['success']:
                            dataset.update(kk='—', point_count=None, channel='—')
                            self.append_log(entry['path'] + ': ' + entry['error'])
                    self.refresh_datasets()
                    if entries:
                        self.select_dataset(self.source_rows[entries[0]['path']])
                    self.set_status(f"Loaded {payload['loaded_count']} spectra; {len(entries) - payload['loaded_count']} failed")
                    self.progress.setValue(100)
                else:
                    self.set_status(record.error or STATES[record.state.value])
            self.state_changed.emit()
            return
        if event.job_id in self.batch_jobs and self._operation == 'analysis':
            source = self.batch_jobs[event.job_id]
            dataset = self.datasets[source]
            dataset['state'] = STATES[event.state.value]
            dataset[self.jobs.record(event.job_id).request.action + '_state'] = dataset['state']
            self.append_log(event.message)
            if event.state.terminal and event.job_id not in self.batch_finished:
                self.batch_finished.add(event.job_id)
                record = self.jobs.record(event.job_id)
                if record.state == JobState.SUCCEEDED:
                    result = json.loads(Path(record.result_path).read_text())
                    cache_action = 'reliable' if record.request.action == 'import_reliable' else record.request.action
                    dataset[cache_action + '_result'] = result
                    dataset[cache_action + '_result_path'] = record.result_path
                    dataset[cache_action + '_state'] = 'Completed'
                    dataset.setdefault('view_circuits', {}).pop(cache_action, None)
                    dataset.update(result=result, result_path=record.result_path,
                                   kk=result['payload']['kk']['status'], point_count=result['payload']['point_count'],
                                   channel=result['payload']['selected_channel'], requested_channel=record.request.config.get('channel'),
                                   channels=result['payload']['metadata'].get('available_channels', [result['payload']['selected_channel']]))
                    if source == self.selected_source:
                        self.result_path = record.result_path
                        self.display_result(result)
                else:
                    self.batch_failures += 1
                    dataset['error'] = record.error
                    self.append_log(record.error)
            row = self.source_rows[source]
            self.cases_table.item(row, 1).setText(text(dataset['state'], self.language))
            self.cases_table.item(row, 1).setData(Qt.UserRole + 17, dataset['state'])
            self.cases_table.item(row, 4).setText(dataset['kk'])
            self.cases_table.item(row, 4).setData(Qt.UserRole + 17, dataset['kk'])
            for column, key in ((2, 'point_count'), (3, 'channel')):
                value = str(dataset.get(key) or '—')
                self.cases_table.item(row, column).setText(value)
                self.cases_table.item(row, column).setData(Qt.UserRole + 17, value)
            done = len(self.batch_finished)
            self.progress.setValue(round(100 * (done + (event.progress if not event.state.terminal else 0)) / len(self.batch_jobs)))
            self.set_status(f'Batch {min(done + 1, len(self.batch_jobs))}/{len(self.batch_jobs)}: {event.message}')
            if done == len(self.batch_jobs):
                self.set_status(f'Batch completed: {done - self.batch_failures} succeeded, {self.batch_failures} failed')
                self.progress.setValue(100)
                self.set_busy(False)
                self.set_status(f'Batch completed: {done - self.batch_failures} succeeded, {self.batch_failures} failed')
            self.state_changed.emit()
            return
        super().handle_event(event)

    def fill_models(self, fits):
        self.models_table.blockSignals(True)
        fill_table(self.models_table, fits, [('status', 'Status'), ('circuit', 'Circuit'),
                   ('mean_fit_error_percent', 'Fit error, %'), ('bic', 'BIC'), ('flags', 'Flags')], language=self.language)
        colors = {'OK': ('#dff3e4', '#102015'), 'WARN': ('#fff4cc', '#332400'), 'BAD': ('#fde2e2', '#3b0a0a')}
        for row, fit in enumerate(fits):
            if fit.get('status') in colors:
                background, foreground = colors[fit['status']]
                self.models_table.item(row, 0).setBackground(QColor(background))
                self.models_table.item(row, 0).setForeground(QColor(foreground))
            if fit.get('is_best'):
                item = self.models_table.item(row, 1)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        self.models_table.blockSignals(False)

    def show_result(self, result):
        if result['payload'].get('dataset_type') in {'eis.study.v1', 'eis.research.v1'}:
            self.display_result(result)
            return
        source = self.job_source or self.selected_source
        if source in self.datasets:
            self.datasets[source].update(result=result, result_path=self.result_path,
                 kk=(result['payload'].get('kk') or {}).get('status', '—'))
        self.display_result(result)

    def display_result(self, result, circuit=None):
        previous_title = self.results.currentWidget().property('source_tab') if self.results.currentWidget() else None
        self.displayed_result = result
        payload = result['payload']
        if payload.get('dataset_type') in {'eis.study.v1', 'eis.research.v1'}:
            self.display_study(result)
            return
        stored = self.datasets.get(self.selected_source, {}).get('view_circuits', {}).get(self.action.currentData())
        circuit = circuit or stored
        chosen = next((row for row in payload.get('fits', []) if row.get('success') and row['circuit'] == circuit), None) or payload.get('best', {})
        self.view_circuit = chosen.get('circuit')
        self.reset_results()
        self.results.removeTab(0)
        specs = chosen.get('plots', payload.get('plots', []))
        specs = list(specs) + [p for p in payload.get('plots', []) if p['title'] in ('DRT distribution', 'Profile likelihood') and p not in specs]
        groups = [('Nyquist', ('Nyquist',)), ('Bode', ('Bode magnitude', 'Bode phase', 'Bode — модуль')),
                  ('Residuals', ('Fit residuals', 'Relative residuals')), ('KK', ('KK Nyquist', 'KK residuals')),
                  ('DRT', ('DRT distribution',)), ('Profile likelihood', ('Profile likelihood',))]
        for title, names in groups:
            plots = [spec for spec in specs if spec['title'] in names]
            if plots:
                widget = (PlotWidget(plots[0], language=self.language) if title == "Nyquist" else
                          LazyPlotPage(lambda specs=plots: PlotGridWidget(specs, language=self.language)))
                self.tab(widget, title)
        self.fill_models(payload.get('fits', []))
        self.models_table.blockSignals(True)
        for index, row in enumerate(payload.get('fits', [])):
            if row['circuit'] == self.view_circuit:
                self.models_table.setCurrentCell(index, 1)
        self.models_table.blockSignals(False)
        self.parameters_table = QTableWidget()
        fill_table(self.parameters_table, chosen.get('parameters', []), [
            ('name', 'Parameter'), ('value', 'Value'), ('confidence', 'Standard error, 1σ'),
            ('relative_error_percent', 'Relative error, %'), ('unit', 'Unit'),
        ], language=self.language)
        tooltip = text('Standard error of the parameter estimate (1σ), derived from the fit covariance. Relative error = 100 × standard error / absolute parameter value.', self.language)
        self.parameters_table.horizontalHeaderItem(2).setToolTip(tooltip)
        self.parameters_table.horizontalHeaderItem(3).setToolTip(tooltip)
        if 'best' in payload:
            self.tab(self.parameters_table, 'Parameters')
        else:
            self.parameters_table.deleteLater()
        if 'drt' in payload:
            for title, rows in [('DRT peaks', payload['drt']['fit']['peaks']),
                                ('DRT regularization', payload['drt']['selection']['ranking'])]:
                table = QTableWidget()
                fill_table(table, rows, language=self.language)
                self.tab(table, title)
            if payload['drt'].get('stability'):
                table = QTableWidget()
                fill_table(table, payload['drt']['stability']['reference_peaks'], language=self.language)
                self.tab(table, 'DRT peak stability')
        if 'inference' in payload:
            self.decision_label = QLabel()
            self.decision_label.setWordWrap(True)
            self.decision_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.decision_label.setTextFormat(Qt.PlainText)
            self.decision_label.setMargin(12)
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setWidget(self.decision_label)
            self.tab(page, 'Circuit reliability')
            topology = payload['inference'].get('topology_bootstrap') or {}
            for title, key in [('Circuit selection stability', 'ranking'), ('Family stability', 'family_ranking')]:
                table = QTableWidget()
                fill_table(table, topology.get(key, []), language=self.language)
                self.tab(table, title)
        if 'statistics' in payload:
            from .presentation import statistics_tables
            for title, rows in statistics_tables(payload['statistics']).items():
                table = QTableWidget()
                fill_table(table, rows, language=self.language)
                self.tab(table, title)
        parser = QTableWidget()
        rows = [{'key': key, 'value': str(value)} for key, value in payload.get('metadata', {}).items()]
        fill_table(parser, rows, [('key', 'Field'), ('value', 'Value')], language=self.language)
        self.tab(parser, 'Parser')
        kk = payload.get('kk') or {}
        kk_table = QTableWidget()
        fill_table(kk_table, [{'metric': title, 'value': kk.get(key)} for key, title in (
            ('status', 'Status'), ('rmse_percent', 'RMSE, %'), ('max_error_percent', 'Maximum error, %'),
            ('mu', 'μ'), ('n_rc', 'RC elements'), ('flags', 'Flags'), ('error_message', 'Message'),
        )], [('metric', 'KK metric'), ('value', 'Value')], language=self.language)
        self.tab(kk_table, 'KK results')
        summary = '\n'.join(f'{key}: {value}' for key, value in payload.get('summary', {}).items())
        flags = payload.get('diagnostics', [])
        if flags:
            summary += '\n\nDiagnostics:\n' + '\n'.join(flags)
        set_text(self.summary, summary, self.language)
        self.tab(self.summary_tab, 'Summary')
        self.results.setCurrentIndex(0)
        for index in range(self.results.count()):
            if self.results.widget(index).property('source_tab') == previous_title:
                self.results.setCurrentIndex(index)
                break
        self.set_language(self.language)

    def activate_tab(self, index):
        page = self.results.widget(index)
        if isinstance(page, LazyPlotPage):
            page.ensure_loaded()

    @property
    def export_paths(self):
        action = self.action.currentData()
        if self.export_action == 'research' and self.result_path:
            return [self.result_path]
        if action in self.aggregate_results:
            return [self.aggregate_results[action]['result_path']]
        return [data[action + '_result_path'] for data in self.datasets.values() if data.get(action + '_result_path')]

    def set_language(self, language):
        super().set_language(language)
        if getattr(self, 'parameter_dialog', None) is not None:
            self.parameter_dialog.set_language(language)
        if getattr(self, 'preset_dialog', None) is not None:
            self.preset_dialog.set_language(language)
        if hasattr(self, 'cases_table'):
            self.refresh_datasets()
        if hasattr(self, 'log'):
            self.log.setHtml('<br/>'.join(escape(text(message, language)) for message in self.log_messages))
            self.log.moveCursor(QTextCursor.End)
        if getattr(self, 'displayed_result', None):
            payload = self.displayed_result['payload']
            if self.view_circuit:
                self.view_label.setText(text('Viewing circuit', language) + ': ' + self.view_circuit + '\n' +
                    text('Statistical winner', language) + ': ' + str(payload.get('best', {}).get('circuit', '—')) + '. ' +
                    text('Export uses the statistical winner.', language))
                self.view_label.setProperty('source_text', self.view_label.text())
            if 'inference' in payload:
                from .presentation import decision_text
                source = decision_text(payload['inference']['decision'], 'en')
                source += '\n\nDiagnostics:\n' + '\n'.join(payload.get('diagnostics', []))
                set_text(self.decision_label, source, language)

    def change_mode(self, *_):
        action = self.action.currentData()
        drt = action == 'drt'
        aggregate = action in {'series', 'joint', 'resolution'}
        for widgets, enabled in ((self.series_controls, action in {'series', 'joint'}),
                                 (self.joint_controls, action == 'joint'),
                                 (self.resolution_controls, action == 'resolution')):
            for widget in widgets:
                widget.setVisible(enabled)
        for widget in self.fit_controls:
            widget.setVisible(not drt and action not in {'series', 'resolution'})
        for widget in self.drt_controls:
            widget.setVisible(drt)
        for widget in self.reliable_controls:
            widget.setVisible(action == 'reliable')
        for widget in self.statistics_controls:
            widget.setVisible(action == 'statistics')
        self.update_noise_control()
        for widget in self.spice_controls:
            widget.setVisible(action == 'fit')
        if aggregate:
            self.display_aggregate()
        elif self.selected_source:
            self.select_dataset(self.source_rows[self.selected_source])
        self.update_engineering_buttons()
        self.inputs_changed()
        self.state_changed.emit()

    def get_config(self):
        action = self.action.currentData()
        common = {'channel', 'seed'}
        fitting = {'circuit', 'candidate_circuits', 'restarts', 'max_evaluations'}
        specific = {
            'fit': {'ngspice_executable'},
            'series': {'series_manifest'},
            'joint': {'series_manifest', 'joint_smoothness', 'joint_cv_grid', 'joint_cv_folds'},
            'resolution': {'resolution_min_frequencies', 'resolution_noise_fractions', 'resolution_max_frequency', 'resolution_replicates', 'resolution_points'},
            'drt': {'drt_lambda_grid', 'drt_tau_points', 'drt_folds', 'drt_stability_samples'},
            'reliable': {'reliability_restarts', 'reliability_samples', 'reliability_drt_samples'},
            'statistics': {'statistics_method', 'statistics_samples', 'statistics_noise_fraction', 'profile_parameter',
                           'profile_points', 'profile_span_decades', 'window_check'},
        }
        option_ids = common | (fitting if action in {'fit', 'reliable', 'statistics', 'joint'} else set()) | specific.get(action, set())
        if action == 'statistics' and self.options['statistics_method'][1].currentData() != 'parametric':
            option_ids.discard('statistics_noise_fraction')
        config = super().get_config(option_ids)
        config['parameter_overrides_by_circuit'] = self.parameter_overrides
        return config

    def get_channel(self):
        if self.channel.currentText() == self.channel.itemText(self.channel.currentIndex()):
            return self.channel.currentData() or ''
        return self.channel.currentText().strip()

    def update_noise_control(self, *_):
        visible = self.action.currentData() == 'statistics' and self.options['statistics_method'][1].currentData() == 'parametric'
        control = self.options['statistics_noise_fraction'][1]
        control.setVisible(visible)
        self.pro_panel.layout().labelForField(control).setVisible(visible)

    def edit_parameters(self):
        from .parameter_dialog import ParameterDialog
        if self._busy or not self.path.text().strip():
            self.set_status('Select source data')
            return
        circuits = [self.options['circuit'][1].itemData(i) for i in range(self.options['circuit'][1].count())
                    if self.options['circuit'][1].itemData(i) != 'adaptive']
        circuit = self.get_config()['circuit']
        if circuit == 'adaptive':
            dataset = self.datasets.get(self.selected_source, {})
            circuit = (dataset.get('fit_result', {}).get('payload', {}).get('best') or {}).get('circuit', circuits[0])
        dialog = ParameterDialog(self, circuit, circuits, self.language)
        self.parameter_dialog = dialog
        dialog.prepare_requested.connect(self.prepare_parameters)
        dialog.parameters_saved.connect(self.save_parameters)
        dialog.rejected.connect(self.cancel_parameter_loading)
        dialog.show()
        self.prepare_parameters(circuit)

    def prepare_parameters(self, circuit):
        if self._busy:
            return
        config = self.get_config()
        config['circuit'] = circuit
        record = self.jobs.submit(JobRequest('eis', 'parameters', (self.datasets.get(self.selected_source, {}).get('runtime_source', self.path.text().strip()),), config))
        self.current_job, self._operation = record.id, 'parameters'
        self.parameter_dialog.set_busy(True)
        self.set_busy(True)

    def save_parameters(self, circuit, parameters):
        self.parameter_overrides[circuit] = parameters
        self.options['circuit'][1].setCurrentText(circuit)
        self.set_status('Parameter settings saved')
        self.state_changed.emit()

    def cancel_parameter_loading(self):
        if self._operation == 'parameters' and self._busy:
            self.cancel()

    def export_spice(self):
        if self._busy or not self.result_path or self.action.currentData() != 'fit':
            return
        parent = QFileDialog.getExistingDirectory(self, text('Export folder', self.language))
        if not parent:
            return
        destination = Path(parent) / ('Chem-Suite-SPICE-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])
        config = {'destination': str(destination), 'ngspice_executable': self.get_config().get('ngspice_executable', '')}
        record = self.jobs.submit(JobRequest('eis', 'spice', (self.result_path,), config))
        self.current_job, self._operation = record.id, 'export'
        self.set_status('Validating SPICE package')
        self.progress.setValue(0)
        self.set_busy(True)

    def append_log(self, message):
        self.log_messages.append(str(message))
        self.log_messages = self.log_messages[-200:]
        self.log.append(escape(text(message, self.language)))

    def select_model(self, row, *_):
        if self._busy or not self.displayed_result or row < 0:
            return
        fits = self.displayed_result['payload'].get('fits', [])
        if row >= len(fits) or not fits[row].get('success'):
            self.set_status('This circuit did not converge; there is no fitted curve to display')
            return
        circuit = fits[row]['circuit']
        if not fits[row].get('plots') and not fits[row].get('is_best'):
            self.set_status('Refit this spectrum to enable candidate views')
            return
        self.datasets.get(self.selected_source, {}).setdefault('view_circuits', {})[self.action.currentData()] = circuit
        self.display_result(self.displayed_result, circuit)

    def edit_presets(self):
        from .preset_dialog import PresetDialog
        if self._busy:
            return
        self.preset_dialog = PresetDialog(self, self.language)
        self.preset_dialog.requested.connect(self.request_presets)
        self.preset_dialog.show()
        self.request_presets('list', '')

    def request_presets(self, operation, name):
        if self._busy:
            return
        from .presets import KEYS
        try:
            config = {'operation': 'save' if operation == 'replace' else operation, 'name': name, 'replace': operation == 'replace'}
            if operation in ('save', 'replace'):
                config['preset'] = {'action': self.action.currentData(), 'config': {key: value for key, value in self.get_config().items() if key in KEYS}}
            record = self.jobs.submit(JobRequest('eis', 'presets', (), config))
        except Exception as exc:
            self.set_status(str(exc))
            set_text(self.preset_dialog.message, str(exc), self.language)
            return
        self.current_job, self._operation = record.id, 'presets'
        self.preset_dialog.set_busy(True)
        self.set_busy(True)

    def apply_preset(self, preset):
        self.parameter_overrides = preset['config'].get('parameter_overrides_by_circuit', {})
        # Missing settings return to their declared defaults, rather than leaking the previous preset.
        for name, (option, control) in self.options.items():
            if name == 'ngspice_executable':
                continue
            value = preset['config'].get(name, option.default)
            if option.kind == 'integer':
                control.setValue(int(value))
            elif isinstance(control, QComboBox):
                index = control.findData(value)
                if index >= 0:
                    control.setCurrentIndex(index)
                elif control.isEditable():
                    control.setEditText(str(value))
            else:
                control.setText(str(value))
        self.action.setCurrentIndex(self.action.findData(preset['action']))
        self.set_status('Preset loaded')
        self.state_changed.emit()

    def import_reliable(self):
        if self._busy or not self.selected_source:
            self.set_status('Select source data')
            return
        path, _ = QFileDialog.getOpenFileName(self, text('Import reliable result', self.language), '', 'JSON (*.json)')
        if not path:
            return
        dataset = self.datasets[self.selected_source]
        config = self.get_config()
        if dataset.get('source_identity'):
            config['source_identity'] = dataset['source_identity']
        record = self.jobs.submit(JobRequest('eis', 'import_reliable', (dataset.get('runtime_source', self.selected_source), path), config))
        self.batch_jobs = {record.id: self.selected_source}
        self.batch_finished, self.batch_failures = set(), 0
        self.current_job, self._operation = record.id, 'analysis'
        self.set_busy(True)

    def inputs_changed(self):
        action = self.action.currentData()
        if action == 'resolution':
            ready = True
        elif action == 'series':
            ready = len(self.series_fit_paths()) >= 2
        elif action == 'joint':
            ready = bool(self.options['series_manifest'][1].text().strip())
        else:
            ready = bool(self.path.text().strip())
        self.run_button.setEnabled(ready and not self._busy)
        self.state_changed.emit()

    def series_fit_paths(self):
        return [data['fit_result_path'] for data in self.datasets.values() if data.get('fit_result_path')]

    def browse_manifest(self):
        path, _ = QFileDialog.getOpenFileName(self, text('SOC series manifest (CSV)', self.language), '', 'CSV (*.csv)')
        if path:
            self.options['series_manifest'][1].setText(path)

    def submit_aggregate(self):
        try:
            action = self.action.currentData()
            config = self.get_config()
            declared_manifest = config.get('series_manifest', '')
            stored_manifest = self.runtime_sources.get(declared_manifest)
            if stored_manifest:
                config['series_manifest'] = stored_manifest['snapshot_path']
                config['series_manifest_identity'] = stored_manifest['identity']
                config['series_source_map'] = self.runtime_sources
            inputs = tuple(self.series_fit_paths()) if action == 'series' else (() if action == 'resolution' else (config.get('series_manifest', ''),))
            if action == 'joint' and not inputs[0]:
                raise ValueError('Choose SOC manifest…')
            record = self.jobs.submit(JobRequest('eis', action, inputs, config, timeout_seconds=3600))
            self.current_job, self._operation = record.id, 'aggregate'
            self.set_status('В очереди')
            self.progress.setValue(0)
            self.set_busy(True)
        except Exception as exc:
            self.set_status(str(exc))

    def display_aggregate(self):
        saved = self.aggregate_results.get(self.action.currentData())
        self.result_path = saved['result_path'] if saved else None
        if saved:
            self.display_result(saved['result'])
        else:
            self.empty_results()
            set_text(self.summary, 'Choose study settings and run the analysis. Joint fitting requires an explicit SOC manifest.', self.language)
        self.makie_button.setEnabled(bool(self.result_path) and not self._busy)
        self.inputs_changed()
        self.state_changed.emit()

    def display_study(self, result):
        self.view_circuit = None
        set_text(self.view_label, {'resolution': 'Synthetic study', 'research': 'Research result'}.get(result['action'], 'Series analysis'), self.language)
        self.reset_results()
        self.results.removeTab(0)
        self.fill_models([])
        for spec in result['payload'].get('plots', []):
            self.tab(LazyPlotPage(lambda spec=spec: PlotWidget(spec, language=self.language)), spec['title'])
        for entry in result['payload'].get('tables', []):
            widget = QTableWidget()
            fill_table(widget, entry['rows'], language=self.language)
            self.tab(widget, entry['title'])
        payload = result['payload']
        set_text(self.summary, '\n'.join(f'{key}: {value}' for key, value in payload['summary'].items()) +
                 '\n\nDiagnostics:\n' + '\n'.join(payload.get('diagnostics', [])), self.language)
        self.tab(self.summary_tab, 'Summary')
        self.set_language(self.language)
        self.activate_tab(self.results.currentIndex())

    def export_controller(self):
        if self._busy or not self.result_path or self.action.currentData() != 'fit':
            return
        parent = QFileDialog.getExistingDirectory(self, text('Export folder', self.language))
        if not parent:
            return
        destination = Path(parent) / ('Chem-Suite-C-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])
        config = ModulePanel.get_config(self, {'controller_sample_period_s', 'controller_current_full_scale_a', 'controller_max_frequency_hz'})
        config['destination'] = str(destination)
        record = self.jobs.submit(JobRequest('eis', 'controller', (self.result_path,), config))
        self.current_job, self._operation = record.id, 'export'
        self.set_status('Validating controller package')
        self.progress.setValue(0)
        self.set_busy(True)

    def get_export_config(self, outputs):
        config = self.get_config()
        if outputs.get('controller'):
            config.update(ModulePanel.get_config(self, {'controller_sample_period_s', 'controller_current_full_scale_a', 'controller_max_frequency_hz'}))
        return config

    @property
    def export_action(self):
        return 'research' if self.displayed_result and self.displayed_result.get('action') == 'research' else self.action.currentData()

    def update_engineering_buttons(self):
        ready = not self._busy and self.action.currentData() == 'fit' and bool(self.result_path) and bool(
            (self.displayed_result or {}).get('payload', {}).get('best', {}).get('success'))
        self.spice_button.setEnabled(ready)
        self.controller_button.setEnabled(ready)
