"""Parameter editor consumes worker-provided defaults; no scientific imports in Qt."""
import math
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QPushButton, QTableWidget, QVBoxLayout)
from chem_suite.desktop.panel import fill_table
from chem_suite.desktop.i18n import translate_widgets, set_text
from chem_suite.locale import text


class ParameterDialog(QDialog):
    prepare_requested = Signal(str)
    parameters_saved = Signal(str, dict)

    def __init__(self, parent, circuit, circuits, language):
        super().__init__(parent)
        self.language, self.prepared_circuit = language, None
        self.setWindowTitle('Parameter values and bounds')
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('Circuit'))
        self.circuit = QComboBox()
        self.circuit.setEditable(True)
        self.circuit.addItems(circuits)
        self.circuit.setCurrentText(circuit)
        row.addWidget(self.circuit, 1)
        self.prepare_button = QPushButton('Load parameter defaults')
        self.prepare_button.clicked.connect(lambda: self.prepare_requested.emit(self.circuit.currentText().strip()))
        row.addWidget(self.prepare_button)
        layout.addLayout(row)
        self.table = QTableWidget()
        layout.addWidget(self.table)
        self.message = QLabel('Load defaults from the selected spectrum, then edit values.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText(text('Apply', language))
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.buttons.button(QDialogButtonBox.Cancel).setText(text('Cancel', language))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.circuit.editTextChanged.connect(lambda _: self.buttons.button(QDialogButtonBox.Save).setEnabled(
            self.prepared_circuit == self.circuit.currentText()))
        self.set_language(language)

    def set_language(self, language):
        self.language = language
        translate_widgets(self, language)

    def set_busy(self, busy):
        self.prepare_button.setEnabled(not busy)
        self.circuit.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(not busy and self.prepared_circuit is not None)

    def load_parameters(self, payload):
        self.prepared_circuit = payload['circuit']
        fill_table(self.table, payload['parameters'], [('name', 'Parameter'), ('unit', 'Unit'),
            ('initial', 'Initial value'), ('lower', 'Lower bound'), ('upper', 'Upper bound')], language=self.language)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed | QTableWidget.AnyKeyPressed)
        # Keep round-trip precision of numerical defaults and permit comma decimal input.
        for row, values in enumerate(payload['parameters']):
            for column, key in ((2, 'initial'), (3, 'lower'), (4, 'upper')):
                item = self.table.item(row, column)
                item.setText(repr(values[key]))
                item.setFlags(item.flags() | Qt.ItemIsEditable)
        set_text(self.message, 'Changes apply to this circuit. Other circuits retain their settings.', self.language)
        self.set_busy(False)

    def accept(self):
        if not self.prepared_circuit or self.prepared_circuit != self.circuit.currentText():
            return
        overrides = {}
        try:
            for row in range(self.table.rowCount()):
                name = self.table.item(row, 0).text()
                values = {key: float(self.table.item(row, column).text().strip().replace(',', '.'))
                          for column, key in ((2, 'initial'), (3, 'lower'), (4, 'upper'))}
                if not all(math.isfinite(value) for value in values.values()):
                    raise ValueError('Parameter values must be finite')
                if values['lower'] >= values['upper']:
                    raise ValueError('Lower bound must be below upper bound')
                if not values['lower'] <= values['initial'] <= values['upper']:
                    raise ValueError('Initial value must be inside bounds')
                overrides[name] = values
        except (ValueError, OverflowError) as exc:
            set_text(self.message, str(exc), self.language)
            return
        self.parameters_saved.emit(self.prepared_circuit, overrides)
        super().accept()
