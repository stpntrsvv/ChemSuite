"""Exercise the shipped executable, including actual spawned workers and Qt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def fault_job(request, context):
    mode = request.config.get("mode", "ok")
    if mode == "error":
        raise ValueError("acceptance fault")
    if mode == "crash":
        os._exit(23)
    if mode == "child":
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve().parents[3] / "tools/launcher.py"))
        child = subprocess.Popen(command + ["--acceptance-sleep"])
        context.artifact("child.pid").write_text(str(child.pid))
    assert sys.stdout is not None and sys.stderr is not None, "Frozen workers need usable standard streams"
    context.progress(.1, "entered")
    end = time.monotonic() + request.config.get("seconds", 0)
    value = 0
    while time.monotonic() < end:
        value = (value * 17 + 1) % 99991
    return {"summary": {}, "plots": [], "tables": []}


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # process no longer exists
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


def run_checks(folder):
    from PySide6.QtCore import QTimer
    from chem_suite import __version__
    from chem_suite.bootstrap import builtins
    from chem_suite.core.contracts import ActionSpec, JobRequest, JobState, ModuleSpec
    from chem_suite.desktop.app import MainWindow, create_application
    assert not getattr(sys, "frozen", False) or sys.flags.utf8_mode == 1, "Frozen runtime must use UTF-8"
    app = create_application()
    assert app.applicationName() == "Chem Suite"
    assert app.applicationVersion() == __version__
    assert not app.windowIcon().isNull(), "Missing application icon"
    registry = builtins()
    registry.register(ModuleSpec("acceptance", "Acceptance", "1", (
        ActionSpec("run", "Run", "chem_suite.distribution.acceptance:fault_job"),), visible=False))
    window = MainWindow(folder / "workspace", registry=registry, auto_update_check=False,
                        update_directory=folder / 'updates', restore_update_session=False)
    window.show()
    samples = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: samples.append(time.monotonic()))
    timer.start()
    checks = []

    def wait(predicate, timeout=90):
        until = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > until:
                raise AssertionError([(r.state, r.error) for r in window.jobs.records()])
            time.sleep(.005)
        app.processEvents()

    def job(module, action, inputs=(), config=None, timeout=90):
        record = window.jobs.submit(JobRequest(module, action, tuple(map(str, inputs)), config or {}, timeout))
        wait(lambda: record.state.terminal, timeout + 10)
        assert record.state == JobState.SUCCEEDED, record.error or record.message
        return record, json.loads(Path(record.result_path).read_text())["payload"]

    try:
        assert {p.spec.id for p in window.panels} == {"eis", "cycling"}
        for language, clear in (("en", "Clear"), ("ru", "Очистить")):
            window.set_language(language, persist=False)
            assert window.clear_action.text() == clear
        checks.append("qt-panels-ru-en-icon")
        from PySide6.QtNetwork import QSslSocket
        from chem_suite.updates.protocol import DownloadSink, UpdateError, UpdateOffer, platform_key
        assert QSslSocket.supportsSsl(), "Packaged updater needs a working TLS backend"
        for language, label in (("en", "Check for updates…"), ("ru", "Проверить обновления…")):
            window.set_language(language, persist=False)
            assert window.updates.check_action.text() == label
        content = b"offline packaged updater acceptance"
        offer = UpdateOffer("0.1.2", platform_key(), "test-installer", "https://github.com/test", len(content),
                            hashlib.sha256(content).hexdigest(), "https://github.com/test", "Changes")
        sink = DownloadSink(folder / "update-test", offer)
        sink.write(content)
        assert sink.finish().read_bytes() == content
        sink = DownloadSink(folder / "update-test", offer)
        sink.write(content[:5])
        sink.abort()
        try:
            sink.finish()
        except UpdateError:
            pass
        else:
            raise AssertionError("Cancelled download became ready")
        assert not list((folder / "update-test").rglob("*.part"))
        window.session_job = "acceptance-session"
        assert not window.updates.can_install()
        window.session_job = None
        assert window.updates.can_install()
        checks.append("updates-tls-integrity-cancellation-busy-guard-ru-en")
        base = Path(sys._MEIPASS) / "acceptance-data" if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3] / "examples"
        eis_input = base / "eis_double_cpe.txt"
        digest = hashlib.sha256(eis_input.read_bytes()).hexdigest()
        eis, spectrum = job("eis", "fit", [eis_input], {"circuit": "R0-p(R1,CPE0)-p(R2,CPE1)"})
        assert spectrum["point_count"] == 60
        assert spectrum["kk"]["status"] == "PASS"
        assert abs(spectrum["best"]["mean_fit_error_percent"] - .999566) < .0001
        assert spectrum["sources"][0]["sha256"] == digest
        assert hashlib.sha256(eis_input.read_bytes()).hexdigest() == digest
        checks.append("eis-numerics-provenance")
        cycling, payload = job("cycling", "analyze", [base / "cycling.csv"])
        assert payload["cycle_count"] == 2
        first = payload["tables"][0]["rows"][0]
        assert abs(first["coulombic_efficiency_percent"] - 80) < 1e-6
        checks.append("cycling-csv-numerics")
        for suffix in ("mpt", "mpr"):
            _, payload = job("cycling", "analyze", [base / ("biologic_cycling." + suffix)])
            row = payload["tables"][0]["rows"][0]
            assert abs(row["charge_mAh"] - 15) < 1e-5
            assert abs(row["discharge_mAh"] - 8) < 1e-5
            checks.append("biologic-" + suffix)
        intervals, payload = job('cycling', 'analyze', [base / 'biologic_gcpl_intervals.mpr'])
        row = payload['tables'][0]['rows'][0]
        assert row['charge_mAh'] == 3 and row['discharge_mAh'] == 2
        assert row['energy_efficiency_percent'] is None
        assert 'biologic_interval_charge' in payload['diagnostics']
        checks.append('biologic-gcpl-dq-without-measured-current')
        _, export = job("exports", "export", [eis.result_path, cycling.result_path],
                        {"destination": str(folder / "export"), "formats": ["png"], "language": "en"})
        assert list((folder / "export").rglob("*.xlsx")), export
        assert list((folder / "export").rglob("*.csv"))
        assert list((folder / "export").rglob("*.png"))
        checks.append("batch-export-csv-xlsx-png")
        manifest = json.loads((folder / 'export' / 'manifest.json').read_text())
        assert manifest['experiments'][1]['plot_legend'] == 'outside'
        for mode in ('inside', 'hidden'):
            destination = folder / ('legend-' + mode)
            _, exported = job('exports', 'export', [intervals.result_path],
                             {'destination': str(destination), 'formats': ['png', 'svg', 'pdf'],
                              'language': 'ru', 'plot_legend': mode})
            assert json.loads((destination / 'manifest.json').read_text())['experiments'][0]['plot_legend'] == mode
            assert all(list(destination.rglob('*.' + fmt)) for fmt in ('png', 'svg', 'pdf'))
        checks.append('export-legends-outside-inside-hidden')
        from chem_suite.desktop.sessions import attach_result
        attach_result(window.panels[0], eis.result_path)
        attach_result(window.panels[1], intervals.result_path)
        window.module_selector.setCurrentIndex(1)
        recovery = window.updates.recovery
        destination = recovery.destination()
        window.save_session_to(destination)
        wait(lambda: window.session_job is None)
        recovery.remember(destination, __version__)
        saved = recovery.pending()
        assert saved is not None
        for panel in window.panels:
            panel.clear_workspace()
        window.start_session('restore', (str(saved),), {}, on_finished=window.updates._restored)
        wait(lambda: window.session_job is None)
        assert all(panel.result_path for panel in window.panels)
        assert window.module_selector.currentData() == 'cycling'
        assert recovery.pending() is None and saved.is_file()
        checks.append('update-session-backup-restore-both-modules')
        for mode in ("error", "crash"):
            record = window.jobs.submit(JobRequest("acceptance", "run", (), {"mode": mode}))
            wait(lambda: record.state.terminal)
            assert record.state == JobState.FAILED and record.result_path is None
            job("acceptance", "run")
        checks.append("exception-native-crash-recovery")
        for mode in ("cancel", "timeout"):
            record = window.jobs.submit(JobRequest("acceptance", "run", (),
                       {"mode": "child", "seconds": 120}, 8 if mode == "timeout" else 90))
            marker = window.jobs.workspace / "jobs" / record.id / "child.pid"
            wait(marker.exists, 7)
            pid = int(marker.read_text())
            assert process_alive(pid)
            if mode == "cancel":
                window.jobs.cancel(record.id)
            wait(lambda: record.state.terminal, 15)
            assert record.state == (JobState.CANCELLED if mode == "cancel" else JobState.TIMED_OUT)
            assert record.result_path is None
            wait(lambda: not process_alive(pid), 5)
            job("acceptance", "run")
            checks.append(mode + "-kills-descendants")
        assert len(samples) > 20
        gaps = [b-a for a,b in zip(samples, samples[1:])]
        # Full desktop rendering and source font-cache initialization can briefly
        # stall the first Qt tick. CPU-bound workers must never block it for seconds.
        assert max(gaps, default=0) < 2, max(gaps)
        checks.append("qt-heartbeat-during-workers")
        return {"status": "passed", "checks": checks, "version": __version__,
                "platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)),
                "qt_heartbeat_max_gap_seconds": max(gaps, default=0)}
    finally:
        timer.stop()
        window.close()
        app.processEvents()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="ChemSuite-acceptance-") as temporary:
            report = run_checks(Path(temporary))
    except Exception:
        import traceback
        report = {"status": "failed", "traceback": traceback.format_exc(), "platform": sys.platform}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1
