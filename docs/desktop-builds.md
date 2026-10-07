# Chem Suite desktop builds

The application is called **Chem Suite**, with one icon and the existing light teal interface.
The `Help / About Chem Suite` dialog shows its version and GPL license in Russian or English.

## GitHub Actions

`Desktop builds` builds native **macOS Apple Silicon (arm64)** on `macos-15` and
**Windows x64** on `windows-2025`, using Python 3.14 and PyInstaller.
Pushes to `main`, pull requests and manual workflow dispatch run the same checks.
Intel macOS is not currently built. Windows supports Windows 10 build 19041 or newer.

Each successful matrix job uploads an artifact for 30 days:

- macOS: drag-to-Applications `.dmg` and a `.zip` preserving app symlinks.
- Windows: per-user `-setup.exe`, a portable `.zip`, GUI `ChemSuite.exe` and console `chem-suite-cli.exe`.
- Both: corresponding GPL source, `acceptance.json`, dependency versions, build metadata and SHA256 checksums.

Download the matching artifact from the workflow's summary page. On macOS open the
DMG and drag Chem Suite to Applications. On Windows run the setup file, or extract
all portable files together before starting `ChemSuite.exe`.

These are unsigned preview builds. Apple signing/notarization and Windows publisher
signing require developer credentials; CI does not claim a signed public release.
The normal Qt graphs, BioLogic readers, EIS/cycling calculations and export are bundled.
**Julia and Makie are optional external installations**, configured as in [makie.md](makie.md).

## State and isolation

Frozen applications use a writable user workspace rather than their launch directory:
`~/Library/Application Support/Chem Suite/workspace` on macOS and
`%LOCALAPPDATA%/Chem Suite/workspace` on Windows. Explicit CLI `--workspace` overrides it.
Source launches retain `.chem-suite` for compatibility.
Workers use POSIX process groups or Windows Job Objects. Cancellation and timeouts
stop subprocesses belonging to that worker without stopping the desktop or another job.
`freeze_support()` runs before imports and argument parsing to prevent worker launch loops.

## Local build

Install the lock file and `.[desktop,eis,cycling,biologic,export,dev,build]` into a venv.
Run `python tools/build_desktop.py` natively on the target OS. Windows installer builds
also require Inno Setup 6. Use `--no-installer` for a portable-only build.
Icons are committed exports of the SVG; regenerate with `python tools/generate_icons.py`.

Before producing archives, the actual frozen executable starts outside the project
and checks both Qt panels and languages, the icon, EIS numerical fitting and provenance,
CSV and BioLogic MPT/MPR cycling, CSV/XLSX/PNG batch export, recovery after exceptions
and native worker crashes, cancellation, timeout, descendant cleanup and Qt heartbeat.
The Windows installer is also silently installed into a temporary build folder and
its installed executable repeats the acceptance checks. Failure prevents normal artifact upload.
The included examples are synthetic, including the tiny MPR fixture; experiment recordings,
`.runtime`, virtual environments and caches are never distributed.
