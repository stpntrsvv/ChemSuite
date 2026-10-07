from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from chem_suite.desktop.i18n import translate_widgets, set_text


class PresetDialog(QDialog):
    requested = Signal(str, str)

    def __init__(self, parent, language):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle('User presets')
        self.resize(540, 280)
        layout = QVBoxLayout(self)
        self.names = QComboBox()
        self.names.setProperty("literal_items", True)
        layout.addWidget(QLabel('Saved presets'))
        layout.addWidget(self.names)
        self.name = QLineEdit()
        self.name.setPlaceholderText('New preset name')
        self.name.setMaxLength(128)
        layout.addWidget(self.name)
        buttons = QGridLayout()
        self.commands = []
        for operation, title in [('load', 'Load preset'), ('save', 'Save new preset'), ('replace', 'Update selected preset'), ('delete', 'Delete preset')]:
            button = QPushButton(title)
            button.clicked.connect(lambda _, op=operation: self.requested.emit(op, self.name.text().strip() if op == 'save' else self.names.currentText()))
            buttons.addWidget(button, len(self.commands) // 2, len(self.commands) % 2)
            self.commands.append(button)
        layout.addLayout(buttons)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.set_language(language)

    def set_language(self, language):
        self.language = language
        translate_widgets(self, language)

    def set_busy(self, busy):
        self.names.setEnabled(not busy)
        self.name.setEnabled(not busy)
        for button in self.commands:
            button.setEnabled(not busy)
        if not busy:
            for button in (self.commands[0], self.commands[2], self.commands[3]):
                button.setEnabled(self.names.count() > 0)

    def load_result(self, payload):
        selected = payload.get('name') or self.names.currentText()
        self.names.clear()
        for name in payload['presets']:
            self.names.addItem(name, name)
            # User names must never be translated.
            from PySide6.QtCore import Qt
            self.names.setItemData(self.names.count() - 1, name, Qt.UserRole + 17)
        if selected in payload['presets']:
            self.names.setCurrentText(selected)
        set_text(self.message, 'Preset loaded' if payload.get('selected') else 'Preset list updated', self.language)
        self.set_busy(False)
