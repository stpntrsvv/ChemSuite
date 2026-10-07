import csv
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from openpyxl import load_workbook

from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from chem_suite.modules.eis.views import spectrum_plots
from tests.test_isolation import wait, registry

ROOT = Path(__file__).parents[1]


def test_bode_conventions_and_full_export_arrays():
    f = np.geomspace(1e-3, 1e6, 3001)
    z = np.full(3001, 3 - 4j)
    preview = spectrum_plots(f, z, z + 1)
    magnitude, phase = preview[1:3]
    assert magnitude['xscale'] == magnitude['yscale'] == 'log'
    assert phase['xscale'] == 'log' and phase['yscale'] == 'linear'
    assert magnitude['series'][0]['y'][0] == 5
    assert phase['series'][0]['y'][0] == pytest.approx(-53.130102)
    assert len(phase['series'][0]['x']) <= 2000
    assert len(spectrum_plots(f, z, limit=len(f))[2]['series'][0]['x']) == 3001
    assert preview[3]['series'][0]['y'][0] == -1
    assert preview[4]['series'][0]['y'][0] == -20


def test_folder_import_bad_file_isolated_and_complete_batch_export(tmp_path):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    shutil.copy(ROOT / 'examples/eis_double_cpe.txt', inputs / 'one.txt')
    (inputs / 'nested').mkdir()
    shutil.copy(ROOT / 'examples/eis_double_cpe.txt', inputs / 'nested/two.txt')
    (inputs / 'bad.csv').write_text('not a spectrum\n')
    with JobService(builtins(), tmp_path / 'workspace', max_active=1) as jobs:
        loaded = jobs.submit(JobRequest('eis', 'load', (str(inputs),), {'recursive': True}))
        wait(jobs, lambda: loaded.state.terminal, 40)
        assert loaded.state == JobState.SUCCEEDED, loaded.error
        payload = json.loads(Path(loaded.result_path).read_text())['payload']
        assert payload['loaded_count'] == 2 and len(payload['entries']) == 3
        entry = next(e for e in payload['entries'] if e['success'])
        preview = json.loads(Path(entry['preview_path']).read_text())['payload']
        assert {s['title'] for s in preview['plots']} >= {'Bode phase', 'Bode magnitude'}
        records = [jobs.submit(JobRequest('eis', 'fit', (e['path'],),
                   {'circuit': 'R0-p(R1,CPE0)-p(R2,CPE1)'})) for e in payload['entries']]
        wait(jobs, lambda: all(r.state.terminal for r in records), 70)
        assert sum(r.state == JobState.SUCCEEDED for r in records) == 2
        successful = tuple(r.result_path for r in records if r.state == JobState.SUCCEEDED)
        destination = tmp_path / 'bundle'
        record = jobs.submit(JobRequest('exports', 'export', successful,
                            {'destination': str(destination), 'language': 'ru', 'formats': ['png', 'svg', 'pdf']}))
        wait(jobs, lambda: record.state.terminal, 70)
        assert record.state == JobState.SUCCEEDED, record.error
        manifest = json.loads((destination / 'manifest.json').read_text())
        assert len(manifest['experiments']) == 2
        with (destination / 'summary.csv').open(encoding='utf-8-sig') as stream:
            assert len(list(csv.DictReader(stream))) == 2
        book = load_workbook(destination / 'results.xlsx', read_only=True)
        assert len(list(book['Summary'].values)) == 3
        book.close()
        for experiment in manifest['experiments']:
            folder = destination / experiment['directory']
            saved = json.loads((folder / 'result.json').read_text())
            provenance = saved['payload']['sources'][0]
            assert hashlib.sha256((folder / provenance['snapshot']).read_bytes()).hexdigest() == provenance['sha256']
            assert (folder / 'spectrum.npz').exists()
            with (folder / 'spectrum.csv').open(encoding='utf-8-sig') as stream:
                rows = list(csv.DictReader(stream))
            assert len(rows) == 60
            assert float(rows[0]['phase_deg']) < 0
            with (folder / 'parameters.csv').open(encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
            assert 'standard_error_1sigma' in row and 'confidence' not in row
            assert 'Стандартная' in (folder / 'report.txt').read_text() or 'стандартн' in (folder / 'report.txt').read_text()
            for extension in ('png', 'svg', 'pdf'):
                assert (folder / f'bode.{extension}').stat().st_size > 1000
        repeated = jobs.submit(JobRequest('exports', 'export', successful, {'destination': str(destination)}))
        wait(jobs, lambda: repeated.state.terminal)
        assert repeated.state == JobState.FAILED
        assert (destination / 'manifest.json').exists()


@pytest.mark.parametrize('mode', ['ok', 'error', 'crash', 'cancel'])
def test_export_publication_is_atomic_even_when_worker_killed(tmp_path, mode):
    target = tmp_path / 'export'
    with JobService(registry(), tmp_path / 'workspace', cancel_grace=.1) as jobs:
        record = jobs.submit(JobRequest('eis', 'run', (), {
            'mode': mode, 'publication': str(target), 'seconds': 30 if mode == 'cancel' else 0}))
        marker = tmp_path / 'workspace/jobs' / record.id / 'prepared'
        wait(jobs, lambda: marker.exists() or record.state.terminal)
        if mode == 'cancel':
            assert marker.exists() and not target.exists()
            jobs.cancel(record.id)
        wait(jobs, lambda: record.state.terminal)
        assert target.exists() == (mode == 'ok')
        assert not list(tmp_path.glob('.chem-suite-*.tmp'))
        if mode == 'ok':
            assert (target / 'data.txt').read_text() == 'complete'
        else:
            assert record.result_path is None


def test_eis_export_uses_full_spectrum_not_gui_preview(tmp_path):
    from chem_suite.modules.eis.exporting import export_data
    class Context:
        def check_cancelled(self):
            pass
    f = np.geomspace(1, 1e6, 3001)
    np.savez(tmp_path / 'spectrum.npz', frequency_hz=f, z_real_ohm=np.ones(len(f)),
             z_imag_ohm=-np.ones(len(f)), fit_real_ohm=np.ones(len(f)), fit_imag_ohm=-np.ones(len(f)))
    payload = {'sources': [{'path': 'synthetic.txt'}], 'best': {'success': True, 'parameters': [],
               'circuit': 'R0', 'mean_fit_error_percent': 0, 'status': 'OK'}, 'fits': [],
               'point_count': 3001, 'selected_channel': 'Z', 'kk': {'status': 'N/A'}}
    _, tables, groups = export_data(tmp_path / 'result.json', {'action': 'fit', 'payload': payload}, {}, Context())
    assert len(tables['spectrum']) == 3001
    assert len(groups['bode'][1]['series'][0]['x']) == 3001


def test_cycling_export_rest_threshold_and_full_cycles(tmp_path):
    from chem_suite.modules.cycling.exporting import export_data
    class Context:
        def check_cancelled(self):
            pass
    cycles = [{'cycle': i, 'discharge_mAh': 1, 'coulombic_efficiency_percent': 80,
               'voltage_efficiency_percent': 90, 'energy_efficiency_percent': 72} for i in range(1, 202)]
    metrics = {'steps': [{'source_index': ['1', 'ch'], 'cycle': 1, 'step': 'ch'}],
               'cycles': cycles, 'rests': [{'source_index': ['1', 'rest']}]}
    (tmp_path / 'metrics.json').write_text(json.dumps(metrics))
    (tmp_path / 'measurements.csv').write_text('source_index,time_s,voltage_V,current_A\n'
          '1/ch,0,3,1\n1/ch,36,4,1\n1/rest,0,3,0.001\n1/rest,10,3,0.001\n')
    _, tables, groups = export_data(tmp_path / 'result.json',
                    {'payload': {'sources': [{'path': 'cycling.csv'}]}}, {}, Context())
    assert len(tables['cycles']) == 201
    assert len(groups['cycling_plots'][1]['series'][0]['x']) == 201
    curves = groups['cycling_plots'][0]['series']
    assert len(curves) == 1  # custom rest threshold remains respected through metrics
    assert curves[0]['x'][-1] == 10
    assert curves[0]['label'] == 'Cycle 1 · ch'
