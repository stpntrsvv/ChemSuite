"""Opt-in real-feed acceptance; no installer download, launch or settings changes."""
import argparse
import json
from pathlib import Path
import tempfile
import time


def main(argv=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QSslSocket
    from chem_suite.desktop.app import create_application
    from chem_suite.desktop.update_client import UpdateClient
    parser = argparse.ArgumentParser()
    parser.add_argument('--update-smoke-test', action='store_true')
    parser.add_argument('--report', required=True)
    parser.add_argument('--from-version', default='0.1.0')
    parser.add_argument('--target', choices=['macos-arm64', 'windows-x64'])
    parser.add_argument('--expect', choices=['available', 'current', 'none'], default='available')
    args = parser.parse_args(argv)
    app = create_application()
    outcomes, errors, beats = [], [], []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    report = {'status':'failed', 'tls':QSslSocket.supportsSsl()}
    with tempfile.TemporaryDirectory(prefix='ChemSuite-update-smoke-') as folder:
        client = UpdateClient(folder, version=args.from_version, target=args.target)
        client.checked.connect(outcomes.append)
        client.failed.connect(errors.append)
        client.check(manual=True)
        deadline = time.monotonic()+75
        while not outcomes and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        if report['tls'] and outcomes == [args.expect]:
            report['status'] = 'passed'
        report.update(outcomes=outcomes, errors=errors,
                      qt_heartbeat_max_gap_seconds=max((b-a for a,b in zip(beats,beats[1:])),default=0))
        if client.offer:
            report.update(version=client.offer.version, target=client.offer.target,
                          name=client.offer.name, size=client.offer.size, sha256=client.offer.sha256)
        client.cancel()
    timer.stop()
    path = Path(args.report)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['status'] == 'passed' else 1
