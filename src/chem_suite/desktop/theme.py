"""Application-wide desktop theme, independent of the system appearance."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

TEXT = "#253542"
ACCENT = "#176d80"


def apply_theme(app):
    # A light background with the macOS dark palette makes labels unreadable.
    # Set both the style and every palette group before constructing any widgets.
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    colors = {
        QPalette.Window: "#f5f7fa",
        QPalette.WindowText: TEXT,
        QPalette.Base: "#ffffff",
        QPalette.AlternateBase: "#eef2f6",
        QPalette.Text: TEXT,
        QPalette.Button: "#edf1f5",
        QPalette.ButtonText: TEXT,
        QPalette.BrightText: "#ffffff",
        QPalette.Light: "#ffffff",
        QPalette.Midlight: "#e4eaf0",
        QPalette.Mid: "#c5ced8",
        QPalette.Dark: "#657482",
        QPalette.Shadow: "#394956",
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#ffffff",
        QPalette.Accent: ACCENT,
        QPalette.Link: ACCENT,
        QPalette.LinkVisited: "#704c8f",
        QPalette.ToolTipBase: "#ffffff",
        QPalette.ToolTipText: TEXT,
        QPalette.PlaceholderText: "#657482",
    }
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, color in colors.items():
            palette.setColor(group, role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.PlaceholderText):
        palette.setColor(QPalette.Disabled, role, QColor("#637083"))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { font-size: 13px; color: palette(window-text); }
        QMainWindow { background: palette(window); }
        QToolBar { background: palette(window); border: 0; border-bottom: 1px solid #c5ced8; spacing: 6px; padding: 5px; }
        QToolButton { color: palette(button-text); padding: 5px 9px; border: 1px solid transparent; border-radius: 4px; }
        QToolButton:hover { background: #e1e9f0; border-color: #b5c1ce; }
        QToolButton:pressed, QToolButton:checked { background: #d3dfe9; }
        QToolButton:disabled { color: #637083; }
        QGroupBox { border: 1px solid #c5ced8; border-radius: 4px; margin-top: 10px; padding-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QSplitter::handle { background: #e4eaf0; }
        QSplitter::handle:horizontal { width: 5px; }
        QSplitter::handle:vertical { height: 5px; }
        QTextEdit { color: palette(text); background: palette(base); border: 1px solid #c5ced8; }
        QLineEdit, QComboBox, QSpinBox {
            color: palette(text); background: palette(base);
            border: 1px solid #b5c1ce; border-radius: 4px; padding: 5px;
            selection-background-color: palette(highlight);
            selection-color: palette(highlighted-text);
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #176d80; }
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { background: palette(button); }
        QComboBox QAbstractItemView {
            color: palette(text); background: palette(base);
            selection-background-color: palette(highlight);
            selection-color: palette(highlighted-text);
        }
        QPushButton {
            color: palette(button-text); background: palette(button);
            border: 1px solid #b5c1ce; border-radius: 5px; padding: 7px 15px;
        }
        QPushButton:hover { background: #e1e9f0; }
        QPushButton:pressed { background: #d3dfe9; }
        QPushButton:focus { border-color: #176d80; }
        QPushButton:disabled { color: #637083; background: #e8edf2; border-color: #ccd5de; }
        QPushButton#primary { background: #176d80; color: white; border-color: #176d80; }
        QPushButton#primary:hover { background: #125b6b; }
        QPushButton#primary:pressed { background: #0f4b59; }
        QPushButton#primary:disabled { color: #637083; background: #e8edf2; border-color: #ccd5de; }
        QTabWidget::pane { border: 1px solid #c5ced8; background: palette(base); }
        QTabBar::tab {
            color: palette(window-text); background: #e9eef3;
            border: 1px solid #c5ced8; padding: 7px 14px;
        }
        QTabBar::tab:selected { color: #125b6b; background: #ffffff; border-bottom-color: #176d80; }
        QTabBar::tab:disabled { color: #637083; }
        QAbstractItemView {
            color: palette(text); background: palette(base);
            alternate-background-color: palette(alternate-base);
            selection-background-color: palette(highlight);
            selection-color: palette(highlighted-text);
            border: 1px solid #c5ced8;
        }
        QTableView { gridline-color: #dce3eb; }
        QHeaderView::section {
            color: palette(button-text); background: palette(button);
            border: 0; border-right: 1px solid #c5ced8;
            border-bottom: 1px solid #c5ced8; padding: 6px;
        }
        QTableCornerButton::section { background: palette(button); border: 1px solid #c5ced8; }
        QProgressBar {
            color: #253542; background: #e9eef3; text-align: center;
            border: 1px solid #c5ced8; border-radius: 4px; min-height: 16px;
        }
        QProgressBar::chunk { background: #b5dce4; border-radius: 3px; }
        QStatusBar { color: palette(window-text); background: palette(window); }
        QToolTip { color: palette(tool-tip-text); background: palette(tool-tip-base); border: 1px solid #b5c1ce; }
    """)
