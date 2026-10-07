from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QGroupBox, QLabel, QLineEdit, QPushButton, QTabWidget,
                               QTableWidget, QWidget, QDockWidget, QToolBar, QTextEdit, QDialog)
from chem_suite.locale import text


def set_text(widget, source, language='ru', method='setText'):
    widget.setProperty('source_text', source)
    getattr(widget, method)(text(source, language))


def translate_widgets(root, language):
    for widget in [root, *root.findChildren(QWidget), *root.findChildren(QAction)]:
        if isinstance(widget, (QLabel, QPushButton, QAction)):
            source = widget.property('source_text')
            if source is None:
                source = widget.text().strip()
                widget.setProperty('source_text', source)
            widget.setText(text(source, language))
        if isinstance(widget, QGroupBox):
            source = widget.property('source_title') or widget.title()
            widget.setProperty('source_title', source)
            widget.setTitle(text(source, language))
        if isinstance(widget, (QLineEdit, QTextEdit)):
            source = widget.property('source_placeholder') or widget.placeholderText()
            widget.setProperty('source_placeholder', source)
            widget.setPlaceholderText(text(source, language))
        if isinstance(widget, (QToolBar, QDockWidget, QDialog)):
            source = widget.property('source_title') or widget.windowTitle()
            widget.setProperty('source_title', source)
            widget.setWindowTitle(text(source, language))
        if widget.toolTip():
            source = widget.property('source_tooltip') or widget.toolTip()
            widget.setProperty('source_tooltip', source)
            widget.setToolTip(text(source, language))
        if isinstance(widget, QComboBox) and not widget.property("literal_items"):
            free_text = widget.currentText() if widget.isEditable() and widget.currentText() != widget.itemText(widget.currentIndex()) else None
            for index in range(widget.count()):
                source = widget.itemData(index, Qt.UserRole + 17)
                if source is None:
                    source = widget.itemText(index)
                    widget.setItemData(index, source, Qt.UserRole + 17)
                widget.setItemText(index, text(source, language))
            if free_text is not None:
                widget.setEditText(free_text)
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                page = widget.widget(index)
                source = page.property('source_tab') or widget.tabText(index)
                page.setProperty('source_tab', source)
                widget.setTabText(index, text(source, language))
        if isinstance(widget, QTableWidget):
            for column in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(column)
                if item is None:
                    continue
                source = item.data(Qt.UserRole + 17) or item.text()
                item.setData(Qt.UserRole + 17, source)
                item.setText(text(source, language))
                if item.toolTip():
                    tooltip = item.data(Qt.UserRole + 18) or item.toolTip()
                    item.setData(Qt.UserRole + 18, tooltip)
                    item.setToolTip(text(tooltip, language))
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None:
                        source = item.data(Qt.UserRole + 17)
                        if source is not None and not item.flags() & Qt.ItemIsEditable:
                            item.setText(text(source, language))
    from chem_suite.desktop.plots import PlotWidget
    for plot in root.findChildren(PlotWidget):
        plot.set_language(language)
