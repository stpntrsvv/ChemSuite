"""Opt-in real-feed acceptance; no installer download, launch or settings changes."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import sys
import time


def acceptance_manager(parent=None):
    """CI-only API authentication; never attach credentials to installer/CDN requests."""
    from PySide6.QtNetwork import QNetworkAccessManager
    from chem_suite.updates.protocol import API_URL
    class AcceptanceManager(QNetworkAccessManager):
        def get(self, request):
            token = os.environ.get('CHEMSUITE_TEST_GITHUB_TOKEN')
            if token and request.url().toString() == API_URL:
                request.setRawHeader(b'Authorization', ('Bearer ' + token).encode('ascii'))
            return super().get(request)
    return AcceptanceManager(parent)


def main(argv=None):
    from chem_suite import __version__
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
    report = {'status':'failed', 'tls':QSslSocket.supportsSsl(),
              'application_version':__version__, 'frozen':bool(getattr(sys,'frozen',False))}
    with tempfile.TemporaryDirectory(prefix='ChemSuite-update-smoke-') as folder:
        manager = acceptance_manager(app)
        client = UpdateClient(folder, version=args.from_version, target=args.target, manager=manager)
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
