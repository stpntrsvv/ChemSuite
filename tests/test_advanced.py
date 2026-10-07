import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from tests.test_isolation import wait


def rc_spectrum(path, *, low=-2, high=4):
    frequencies = np.logspace(high, low, 61)
    z = 2 + 5 / (1 + 1j * 2 * np.pi * frequencies * .1)
    path.write_text('frequency_hz,z_real_ohm,z_imag_ohm\n' + '\n'.join(
        f'{f:.17g},{v.real:.17g},{v.imag:.17g}' for f, v in zip(frequencies, z)))
    return path


def fit_rc(jobs, path):
    overrides = {'R0-p(R1,C1)': {'R0': {'initial': 2, 'lower': 1.5, 'upper': 2.5},
                  'R1': {'initial': 5, 'lower': 4, 'upper': 6},
                  'C1': {'initial': .02, 'lower': .01, 'upper': .03}}}
    record = jobs.submit(JobRequest('eis', 'fit', (str(path),),
        {'circuit': 'R0-p(R1,C1)', 'parameter_overrides_by_circuit': overrides, 'restarts': 2, 'seed': 17}))
    wait(jobs, lambda: record.state.terminal, 40)
    assert record.state == JobState.SUCCEEDED, record.error
    return record


def test_defaults_units_and_constrained_fit_provenance(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv')
    with JobService(builtins(), tmp_path / 'workspace') as jobs:
        defaults = jobs.submit(JobRequest('eis', 'parameters', (str(source),), {'circuit': 'R0-p(R1,C1)'}))
        wait(jobs, lambda: defaults.state.terminal, 25)
        assert defaults.state == JobState.SUCCEEDED, defaults.error
        data = json.loads(Path(defaults.result_path).read_text())['payload']
        assert [p['name'] for p in data['parameters']] == ['R0', 'R1', 'C1']
        assert [p['unit'] for p in data['parameters']] == ['Ohm', 'Ohm', 'F']
        fitted = fit_rc(jobs, source)
        result = json.loads(Path(fitted.result_path).read_text())
        assert result['request']['config']['seed'] == 17
        best = result['payload']['best']
        assert best['mean_fit_error_percent'] < 1e-6
        assert [p['value'] for p in best['parameters']] == pytest.approx([2, 5, .02], rel=1e-5)
        assert result['payload']['sources'][0]['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_drt_analysis_stability_and_export(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv')
    with JobService(builtins(), tmp_path / 'workspace') as jobs:
        record = jobs.submit(JobRequest('eis', 'drt', (str(source),), {
            'drt_tau_points': 101, 'drt_lambda_grid': '0.001,0.01', 'drt_folds': 3,
            'drt_stability_samples': 2, 'seed': 17}))
        wait(jobs, lambda: record.state.terminal, 60)
        assert record.state == JobState.SUCCEEDED, record.error
        result = json.loads(Path(record.result_path).read_text())
        drt = result['payload']['drt']
        assert drt['fit']['success']
        assert min(drt['fit']['gamma_ohm']) >= 0
        strongest = max(drt['fit']['peaks'], key=lambda p: p['gamma_ohm'])
        assert abs(np.log10(strongest['tau_seconds'] / .1)) < .25
        assert drt['fit']['relative_rmse'] < .02
        assert len(drt['stability']['conditions']) == 4
        assert 'DRT distribution' in [p['title'] for p in result['payload']['plots']]
        assert not result['payload'].get('best')
        destination = tmp_path / 'drt-export'
        exported = jobs.submit(JobRequest('exports', 'export', (record.result_path,), {
            'destination': str(destination), 'outputs': {'excel': False}, 'formats': ['svg'], 'language': 'en'}))
        wait(jobs, lambda: exported.state.terminal, 40)
        assert exported.state == JobState.SUCCEEDED, exported.error
        folder = next(p for p in destination.iterdir() if p.is_dir())
        assert (folder / 'drt_distribution.csv').exists()
        assert (folder / 'drt_stability.csv').exists()
        assert (folder / 'drt_plot.svg').exists()
        assert (folder / 'drt.json').exists()
        assert 'chemical mechanisms' in (folder / 'report.txt').read_text()
        assert not (folder / 'parameters.csv').exists()


def test_drt_hard_cancel_preserves_cycling(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv')
    cycling = Path(__file__).parents[1] / 'examples/cycling.csv'
    with JobService(builtins(), tmp_path / 'workspace', cancel_grace=.1) as jobs:
        record = jobs.submit(JobRequest('eis', 'drt', (str(source),), {
            'drt_tau_points': 401, 'drt_folds': 10, 'drt_stability_samples': 200}))
        peer = jobs.submit(JobRequest('cycling', 'analyze', (str(cycling),)))
        wait(jobs, lambda: record.message == 'Selecting DRT regularization' or record.state.terminal, 30)
        assert not record.state.terminal
        jobs.cancel(record.id)
        wait(jobs, lambda: record.state.terminal and peer.state.terminal, 15)
        assert record.state == JobState.CANCELLED and record.result_path is None
        assert peer.state == JobState.SUCCEEDED


@pytest.mark.parametrize('override', [
    {'R0': {'lower': 3, 'upper': 2}}, {'R0': {'initial': 5, 'lower': 1, 'upper': 2}},
    {'unknown': {'initial': 1}}, {'R0': {'initial': float('inf')}},
])
def test_invalid_parameter_settings_fail_explicitly(override):
    from chem_suite.modules.eis.advanced import validate_overrides
    with pytest.raises(ValueError):
        validate_overrides({'R0-p(R1,C1)': override})


@pytest.mark.skipif(shutil.which('ngspice') is None, reason='real ngspice acceptance requires runtime')
def test_real_spice_package_and_optional_export(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path / 'workspace') as jobs:
        fitted = fit_rc(jobs, source)
        target = tmp_path / 'spice'
        record = jobs.submit(JobRequest('eis', 'spice', (fitted.result_path,), {'destination': str(target)}))
        wait(jobs, lambda: record.state.terminal, 50)
        assert record.state == JobState.SUCCEEDED, record.error
        passport = json.loads((target / 'passport.json').read_text())
        assert passport['status'] == 'validated'
        assert passport['source']['file'] == str(source)
        assert passport['source']['sha256'].lower() == hashlib.sha256(source.read_bytes()).hexdigest()
        assert passport['engineering_model']['selected_attempt']['external_validation']['status'] == 'validated'
        assert passport['engineering_model']['selected_attempt']['external_validation']['max_error_percent'] <= 1e-6
        assert passport['files']['model_sha256'].lower() == hashlib.sha256((target / 'model.lib').read_bytes()).hexdigest()
        destination = tmp_path / 'bundle'
        exported = jobs.submit(JobRequest('exports', 'export', (fitted.result_path,), {
            'destination': str(destination), 'outputs': {'excel': False, 'spice': True}, 'formats': []}))
        wait(jobs, lambda: exported.state.terminal, 50)
        assert exported.state == JobState.SUCCEEDED, exported.error
        assert len(list(destination.rglob('model.lib'))) == 1
        # A corrupted source snapshot must never acquire the original hash in a passport.
        saved = json.loads(Path(fitted.result_path).read_text())['payload']['sources'][0]
        (Path(fitted.result_path).parent / saved['snapshot']).write_text('corrupted')
        invalid = jobs.submit(JobRequest('eis', 'spice', (fitted.result_path,), {'destination': str(tmp_path / 'corrupted')}))
        wait(jobs, lambda: invalid.state.terminal, 50)
        assert invalid.state == JobState.FAILED
        assert not (tmp_path / 'corrupted').exists()


def test_missing_spice_runtime_does_not_publish_package(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path / 'workspace') as jobs:
        fitted = fit_rc(jobs, source)
        record = jobs.submit(JobRequest('eis', 'spice', (fitted.result_path,), {
            'destination': str(tmp_path / 'missing-runtime'), 'ngspice_executable': '/missing/ngspice'}))
        wait(jobs, lambda: record.state.terminal, 50)
        assert record.state == JobState.FAILED
        assert 'ngspice_runtime_missing' in record.error
        assert not (tmp_path / 'missing-runtime').exists()
        assert not list(tmp_path.glob('.chem-suite-*.tmp'))
