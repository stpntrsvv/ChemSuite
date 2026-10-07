# PyInstaller native onedir bundles. Scientific handlers remain lazily imported.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata
root = Path(SPECPATH).parent
assets = root / "src/chem_suite/desktop/assets"
datas = collect_data_files("chem_suite")
datas += [(str(root / "examples"), "acceptance-data"), (str(root / "LICENSE"), "legal"),
          (str(root / "THIRD_PARTY_NOTICES.md"), "legal"), (str(root / "docs"), "docs")]
for package in ("chem-suite", "impedance", "galvani", "numpy", "scipy", "pandas", "matplotlib", "PySide6", "openpyxl"):
    datas += copy_metadata(package)
hiddenimports = collect_submodules("chem_suite") + collect_submodules("impedance", filter=lambda name: not name.startswith("impedance.tests")) + ["galvani.BioLogic"]
a = Analysis([str(root / "tools/launcher.py")], pathex=[str(root / "src")],
             binaries=[], datas=datas, hiddenimports=hiddenimports,
             excludes=["PyQt5", "PyQt6", "PySide2", "IPython", "pytest", "tkinter"], noarchive=False)
pyz = PYZ(a.pure)
icon = str(assets / ("chemsuite.icns" if sys.platform == "darwin" else "chemsuite.ico"))
options = [("X utf8", None, "OPTION")]
version = str(root / "packaging/windows-version.txt") if sys.platform == "win32" else None
gui = EXE(pyz, a.scripts, options, exclude_binaries=True, name="ChemSuite", console=False,
          icon=icon, version=version, disable_windowed_traceback=False, target_arch=None, codesign_identity=None)
console = EXE(pyz, a.scripts, options, exclude_binaries=True, name="chem-suite-cli", console=True, icon=icon)
if sys.platform == "darwin":
    collection = COLLECT(gui, a.binaries, a.datas, name="ChemSuite")
    app = BUNDLE(collection, name="Chem Suite.app", icon=icon, bundle_identifier="org.chemsuite.desktop",
                 info_plist={"CFBundleDisplayName": "Chem Suite", "CFBundleShortVersionString": "0.1.0",
                             "CFBundleVersion": "0.1.0", "NSHighResolutionCapable": True})
else:
    collection = COLLECT(gui, console, a.binaries, a.datas, name="ChemSuite")
