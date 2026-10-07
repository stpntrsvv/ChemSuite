import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget, QMenu

from chem_suite.core.plots import validate_plot
from chem_suite.desktop.theme import ACCENT, TEXT
from chem_suite.locale import text


class PlotWidget(QWidget):
    """Bounded interactive preview in Qt. Full-resolution scientific exports are jobs."""

    def __init__(self, specification, parent=None, *, language="ru"):
        super().__init__(parent)
        validate_plot(specification)
        self.specification = specification
        chart = pg.PlotWidget(background="w")
        self.chart = chart
        chart.setMenuEnabled(False)
        chart.setContextMenuPolicy(Qt.CustomContextMenu)
        chart.customContextMenuRequested.connect(self.context_menu)
        layout = QVBoxLayout(self)
        layout.addWidget(chart)
        item = chart.getPlotItem()
        item.setTitle(specification["title"], color=TEXT, size="14pt")
        item.setLabel("bottom", specification["xlabel"], color=TEXT)
        item.setLabel("left", specification["ylabel"], color=TEXT)
        for axis in ("left", "bottom"):
            item.getAxis(axis).setPen("#53616e")
            item.getAxis(axis).setTextPen(TEXT)
        item.showGrid(x=True, y=True, alpha=0.15)
        item.addLegend(offset=(12, 12), labelTextColor=TEXT, brush="#ffffff", pen="#c5ced8")
        item.setLogMode(x=specification["xscale"] == "log", y=specification["yscale"] == "log")
        if specification.get("equal_aspect"):
            item.getViewBox().setAspectLocked(True, ratio=1)
        palette = (ACCENT, "#d27b2c", "#42885b", "#87569c", "#b74e55")
        for index, curve in enumerate(specification["series"]):
            if not curve["x"]:
                continue
            color = palette[index % len(palette)]
            if curve["kind"] == "scatter":
                item.plot(
                    curve["x"],
                    curve["y"],
                    name=text(curve["label"], language),
                    pen=None,
                    symbol="o",
                    symbolSize=6,
                    symbolBrush=color,
                    symbolPen=color,
                )
            else:
                item.plot(curve["x"], curve["y"], name=text(curve["label"], language), pen=pg.mkPen(color, width=2))

        self.set_language(language)

    def context_menu(self, position):
        menu = QMenu(self)
        menu.addAction(text("Fit graph to data", self.language), self.chart.getViewBox().autoRange)
        menu.addAction(text("Pan graph", self.language),
                       lambda: self.chart.getViewBox().setMouseMode(pg.ViewBox.PanMode))
        menu.addAction(text("Zoom by rectangle", self.language),
                       lambda: self.chart.getViewBox().setMouseMode(pg.ViewBox.RectMode))
        menu.exec(self.chart.mapToGlobal(position))

    def set_language(self, language):
        if getattr(self, "language", None) == language:
            return
        self.language = language
        spec = self.specification
        item = self.chart.getPlotItem()
        item.setTitle(text(spec["title"], language), color=TEXT, size="14pt")
        item.setLabel("bottom", text(spec["xlabel"], language), color=TEXT)
        item.setLabel("left", text(spec["ylabel"], language), color=TEXT)
        for (_, label), curve in zip(item.legend.items, [c for c in spec["series"] if c["x"]]):
            label.setText(text(curve["label"], language), color=TEXT)


class PlotGridWidget(QWidget):
    """Linked frequency axes for paired magnitude/phase and residual views."""
    def __init__(self, specs, parent=None, *, language="ru"):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plots = [PlotWidget(spec, language=language) for spec in specs]
        for plot in self.plots:
            layout.addWidget(plot)
        if len(self.plots) > 1 and all(s["xscale"] == specs[0]["xscale"] for s in specs):
            for plot in self.plots[1:]:
                plot.chart.setXLink(self.plots[0].chart)


class LazyPlotPage(QWidget):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory
        self.loaded = False
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

    def ensure_loaded(self):
        if not self.loaded:
            self.layout.addWidget(self.factory())
            self.loaded = True
