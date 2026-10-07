"""Build, verify the packaged executable, then create distributable artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*command, **kwargs):
    print(" ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-installer", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from chem_suite import __version__
    build = ROOT / "build/desktop"
    release = build / "release"
    release.mkdir(parents=True, exist_ok=True)
    build_environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(build / "cache"))
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", build / "dist", "--workpath", build / "work", ROOT / "packaging/chemsuite.spec", cwd=ROOT, env=build_environment)
    bundle = build / "dist/Chem Suite.app" if sys.platform == "darwin" else build / "dist/ChemSuite"
    executable = bundle / "Contents/MacOS/ChemSuite" if sys.platform == "darwin" else bundle / "ChemSuite.exe"
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment.pop("PYTHONPATH", None)
    # Start outside the project, with no installed Python environment on the path.
    import tempfile
    report = release / "acceptance.json"
    with tempfile.TemporaryDirectory() as unrelated:
        run(executable, "--self-test", "--report", report, cwd=unrelated, env=environment, timeout=240)
    result = json.loads(report.read_text())
    if result.get("status") != "passed" or result.get("frozen") is not True:
        raise RuntimeError("Packaged acceptance did not pass")
    label = f"ChemSuite-{__version__}-" + ("macos-" + platform.machine() if sys.platform == "darwin" else "windows-x64")
    # Supply corresponding GPL source and notices with every binary distribution.
    source = build / "source/ChemSuite"
    if source.exists():
        shutil.rmtree(source)
    source.mkdir(parents=True)
    for folder in ("src", "tests", "tools", "packaging", "docs", "examples", ".github"):
        if (ROOT / folder).exists():
            shutil.copytree(ROOT / folder, source / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.egg-info"))
    for name in ("pyproject.toml", "requirements-lock.txt", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "AGENTS.md", ".gitignore"):
        shutil.copy2(ROOT / name, source / name)
    shutil.make_archive(str(release / (label + "-source")), "zip", source.parent, source.name)
    if sys.platform == "darwin":
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, release / (label + ".zip"))
        if not args.no_installer:
            volume = build / "dmg"
            volume.mkdir(exist_ok=True)
            run("ditto", bundle, volume / bundle.name)
            applications = volume / "Applications"
            if not applications.exists():
                applications.symlink_to("/Applications")
            run("hdiutil", "create", "-ov", "-volname", "Chem Suite", "-srcfolder", volume,
                "-format", "UDZO", release / (label + ".dmg"))
    else:
        shutil.make_archive(str(release / (label + "-portable")), "zip", bundle.parent, bundle.name)
        if not args.no_installer:
            compiler = shutil.which("ISCC") or str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6/ISCC.exe")
            if not Path(compiler).is_file():
                raise RuntimeError("Inno Setup compiler ISCC.exe is required for the installer")
            env = dict(os.environ, CHEMSUITE_VERSION=__version__, CHEMSUITE_BUILD_ROOT=str(build))
            run(compiler, ROOT / "packaging/windows.iss", env=env)
            # Also verify the installed layout and shortcuts through silent per-user installation.
            installer = release / (label + "-setup.exe")
            installed = build / "installed"
            run(installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={installed}", timeout=120)
            run(installed / "ChemSuite.exe", "--self-test", "--report", release / "installed-acceptance.json",
                env=environment, timeout=240)
    info = {"version": __version__, "platform": sys.platform, "architecture": platform.machine(),
            "python": sys.version, "commit": os.environ.get("GITHUB_SHA", "local"),
            "signed": False, "julia_bundled": False}
    (release / "build-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    freeze = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    (release / "dependencies.txt").write_text(freeze, encoding="utf-8")
    checksum = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                       for p in sorted(release.iterdir()) if p.is_file() and p.name != "SHA256SUMS")
    (release / "SHA256SUMS").write_text(checksum, encoding="utf-8")
    print(release, flush=True)


if __name__ == "__main__":
    main()
