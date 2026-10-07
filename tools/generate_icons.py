"""Export the repository SVG to native app icons. No external image service."""
import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication
from PIL import Image
app = QApplication.instance() or QApplication([])
folder = Path(__file__).resolve().parents[1] / "src/chem_suite/desktop/assets"
image = QImage(1024, 1024, QImage.Format_ARGB32)
image.fill(0)
painter = QPainter(image)
QSvgRenderer(str(folder / "chemsuite.svg")).render(painter)
painter.end()
image.save(str(folder / "chemsuite.png"))
with Image.open(folder / "chemsuite.png") as bitmap:
    bitmap.save(folder / "chemsuite.ico", sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
    bitmap.save(folder / "chemsuite.icns")
print(folder)
