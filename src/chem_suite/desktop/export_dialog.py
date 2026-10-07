from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout
from chem_suite.locale import text


class ExportDialog(QDialog):
    def __init__(self, parent, language, module, count, action="fit"):
        super().__init__(parent)
        self.setWindowTitle(text('Состав экспорта', language))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(text('Результаты для экспорта', language) + f': {count}'))
        fields = [('summary', 'Сводная таблица CSV'), ('excel', 'Книга XLSX'), ('source', 'Копии исходных файлов'),
                  ('json', 'Результат JSON'), ('report', 'Текстовый отчёт')]
        if module == 'eis':
            if action in {'fit', 'reliable', 'statistics'}:
                layout.addWidget(QLabel(text('Export uses the statistical winner.', language)))
            fields += [('spectrum', 'Спектр и модель CSV'), ('parameters', 'Параметры CSV'),
                       ('models', 'Кандидаты схем CSV'), ('parser', 'Метаданные парсера CSV'), ('kk', 'Проверка КК CSV'),
                       ('nyquist', 'Графики Найквиста'), ('bode', 'Графики Боде'),
                       ('residuals', 'Графики остатков'), ('kk_plot', 'Графики КК')]
            if action == 'drt':
                fields = [(key, title) for key, title in fields if key not in {'parameters', 'models'}]
                fields += [('drt_distribution', 'DRT distribution CSV'), ('drt_peaks', 'DRT peaks CSV'),
                           ('drt_regularization', 'DRT regularization CSV'), ('drt_stability', 'DRT stability CSV'),
                           ('drt_plot', 'DRT plot')]
            elif action == 'fit':
                fields += [('spice', 'Validated SPICE package'), ('controller', 'Validated controller C package')]
            elif action == 'reliable':
                fields += [('reliability_json', 'Reliability JSON'), ('reliability_tables', 'Circuit reliability tables'),
                           ('drt_plot', 'DRT plot')]
            elif action == 'statistics':
                fields += [('statistics_tables', 'Parameter diagnostic tables'), ('profile_plot', 'Profile plot')]
        else:
            fields += [('cycles', 'Метрики циклов CSV'), ('steps', 'Метрики шагов CSV'), ('rests', 'Паузы CSV'),
                       ('waveforms', 'Измерения и кривые CSV'), ('cycling_plots', 'Графики циклирования')]
        if module == 'eis' and action in {'series', 'joint', 'resolution', 'research'}:
            fields = fields[:5] + [('study_plots', 'Study plots')]
        grid = QGridLayout()
        self.boxes = {}
        for index, (key, title) in enumerate(fields):
            box = QCheckBox(text(title, language))
            box.setChecked(key not in {"spice", "controller"})
            self.boxes[key] = box
            grid.addWidget(box, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addWidget(QLabel(text('Форматы графиков', language)))
        formats = QGridLayout()
        self.format_boxes = {}
        for index, extension in enumerate(('png', 'svg', 'pdf')):
            box = QCheckBox(extension.upper())
            box.setChecked(extension == 'png')
            self.format_boxes[extension] = box
            formats.addWidget(box, 0, index)
        layout.addLayout(formats)
        self.message = QLabel()
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Cancel).setText(text('Отменить', language))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.language = language

    def outputs(self):
        return {key: box.isChecked() for key, box in self.boxes.items()}

    def formats(self):
        return [key for key, box in self.format_boxes.items() if box.isChecked()]

    def accept(self):
        outputs = self.outputs()
        plot_keys = {'nyquist', 'bode', 'residuals', 'kk_plot', 'cycling_plots', 'drt_plot', 'profile_plot', 'study_plots'}
        data_selected = any(value for key, value in outputs.items() if key not in plot_keys)
        plots_selected = bool(self.formats()) and any(outputs.get(key) for key in plot_keys)
        if not (data_selected or plots_selected):
            self.message.setText(text('Выберите хотя бы один формат экспорта.', self.language))
            return
        super().accept()
