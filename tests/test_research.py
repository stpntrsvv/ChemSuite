import json
from pathlib import Path

import numpy as np
import pytest

from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from tests.test_isolation import wait
from tests.test_advanced import rc_spectrum


def completed(jobs, action, inputs=(), config=None, timeout=60):
    record = jobs.submit(JobRequest('eis', action, tuple(str(p) for p in inputs), config or {}))
    wait(jobs, lambda: record.state.terminal, timeout)
    assert record.state == JobState.SUCCEEDED, record.error
    return record, json.loads(Path(record.result_path).read_text())


def test_candidate_curves_reproduce_each_circuit_and_keep_winner(tmp_path):
    from impedance.models.circuits import CustomCircuit
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path / 'work') as jobs:
        record, result = completed(jobs, 'fit', [source], {'candidate_circuits': 'R0-p(R1,C1);R0'})
        payload = result['payload']
        assert payload['best']['circuit'] == 'R0-p(R1,C1)'
        assert sum(row['is_best'] for row in payload['fits']) == 1
        with np.load(Path(record.result_path).parent / 'candidates.npz') as data:
            assert data['real_ohm'].shape == (2, 61)
            for row in payload['fits']:
                model = CustomCircuit(row['circuit'], initial_guess=[p['value'] for p in row['parameters']])
                expected = model.predict(data['frequency_hz'], use_initial=True)
                index = row['prediction_index']
                assert data['real_ohm'][index] == pytest.approx(expected.real)
                assert data['imag_ohm'][index] == pytest.approx(expected.imag)
                nyquist = next(p for p in row['plots'] if p['title'] == 'Nyquist')
                assert nyquist['series'][1]['y'] == pytest.approx(-expected.imag)
        assert payload['model_evidence']['supported_topologies']


def test_reliability_import_export_and_foreign_source_refusal(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    config = {'candidate_circuits': 'R0-p(R1,C1);R0', 'reliability_restarts': 1,
              'reliability_samples': 3, 'reliability_drt_samples': 2, 'seed': 7}
    with JobService(builtins(), tmp_path / 'work') as jobs:
        record, result = completed(jobs, 'reliable', [source], config)
        payload = result['payload']
        inference = payload['inference']
        assert payload['kk']['status'] == 'PASS'
        assert inference['topology_bootstrap']['requested'] == 3
        assert inference['topology_bootstrap']['candidate_families'] == ['ideal_rc', 'unclassified']
        assert inference['drt']['stability']['reference_peaks']
        assert inference['decision']['best_statistical'] == payload['best']['circuit']
        assert inference['file'] == str(source)
        _, imported = completed(jobs, 'import_reliable', [source, record.result_path])
        assert imported['payload']['inference']['decision'] == inference['decision']
        assert imported['payload']['import_verification']['source_sha256']
        _, old_import = completed(jobs, 'import_reliable', [source, Path(record.result_path).parent / 'inference.json'])
        assert not old_import['payload']['import_verification']['source_sha256']
        assert old_import['payload']['best']['parameters'] == payload['best']['parameters']
        destination = tmp_path / 'export'
        exported = jobs.submit(JobRequest('exports', 'export', (record.result_path,), {
            'destination': str(destination), 'outputs': {'excel': False}, 'formats': []}))
        wait(jobs, lambda: exported.state.terminal, 40)
        assert exported.state == JobState.SUCCEEDED, exported.error
        folder = next(p for p in destination.iterdir() if p.is_dir())
        assert json.loads((folder / 'inference.json').read_text())['decision'] == inference['decision']
        assert (folder / 'topology_stability.csv').exists()
        assert (folder / 'family_stability.csv').exists()
        assert 'physical' not in inference['decision'].get('reason', '')
        assert 'Вердикт' in (folder / 'report.txt').read_text() or 'Verdict' in (folder / 'report.txt').read_text()
        foreign = rc_spectrum(tmp_path / 'other.csv')
        refused = jobs.submit(JobRequest('eis', 'import_reliable', (str(foreign), record.result_path)))
        wait(jobs, lambda: refused.state.terminal, 30)
        assert refused.state == JobState.FAILED
        assert 'hash' in refused.error and refused.result_path is None
        # A bare hand-written recommendation is not a valid import.
        bare = tmp_path / 'bare.json'
        bare.write_text(json.dumps({'decision': {'recommended_topology': 'R0'}}))
        refused = jobs.submit(JobRequest('eis', 'import_reliable', (str(source), str(bare))))
        wait(jobs, lambda: refused.state.terminal, 30)
        assert refused.state == JobState.FAILED


def test_reliability_kk_fail_never_recommends(tmp_path):
    source = rc_spectrum(tmp_path / 'wide.csv')
    with JobService(builtins(), tmp_path / 'work') as jobs:
        _, result = completed(jobs, 'reliable', [source], {'circuit': 'R0-p(R1,C1)', 'reliability_restarts': 1,
                                                          'reliability_samples': 1, 'reliability_drt_samples': 1})
        assert result['payload']['kk']['status'] == 'FAIL'
        decision = result['payload']['inference']['decision']
        assert decision['verdict'] == 'insufficient_information'
        assert decision['recommended_reliable'] is None


def test_intervals_profile_windows_units_and_export(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    overrides = {'R0-p(R1,C1)': {'R0': {'lower': 1.5, 'upper': 2.5, 'initial': 2},
                               'R1': {'lower': 4, 'upper': 6, 'initial': 5},
                               'C1': {'lower': .01, 'upper': .03, 'initial': .02}}}
    with JobService(builtins(), tmp_path / 'work') as jobs:
        record, result = completed(jobs, 'statistics', [source], {'circuit': 'R0-p(R1,C1)', 'statistics_samples': 3,
            'profile_parameter': 'R1', 'profile_points': 7, 'seed': 11, 'parameter_overrides_by_circuit': overrides})
        stats = result['payload']['statistics']
        assert stats['bootstrap']['accepted'] == 3
        assert [row['unit'] for row in stats['bootstrap']['parameters']] == ['Ohm', 'Ohm', 'F']
        for row, expected in zip(stats['bootstrap']['parameters'], [2, 5, .02]):
            assert row['median'] == pytest.approx(expected, rel=1e-4)
            assert row['ci95_low'] <= row['median'] <= row['ci95_high']
        assert stats['profile']['ci95_low'] - 1e-12 <= stats['profile']['base'] <= stats['profile']['ci95_high'] + 1e-12
        assert all(4 <= row['value'] <= 6 for row in stats['profile']['points'])
        assert len(stats['window_stability']['variants']) == 4
        assert all(row['stable'] for row in stats['window_stability']['parameters'].values())
        destination = tmp_path / 'export'
        exported = jobs.submit(JobRequest('exports', 'export', (record.result_path,), {
            'destination': str(destination), 'outputs': {'excel': False}, 'formats': ['svg']}))
        wait(jobs, lambda: exported.state.terminal, 40)
        assert exported.state == JobState.SUCCEEDED, exported.error
        folder = next(p for p in destination.iterdir() if p.is_dir())
        assert (folder / 'parameter_intervals.csv').exists()
        assert (folder / 'window_stability.csv').exists()
        assert (folder / 'profile_plot.svg').exists()
        assert (folder / 'statistics.json').exists()
        assert not (folder / 'inference.json').exists()


def test_parametric_bootstrap_noise_model_and_single_parameter_profile(tmp_path):
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path / 'work') as jobs:
        _, result = completed(jobs, 'statistics', [source], {'circuit': 'R0', 'statistics_samples': 3,
            'statistics_method': 'parametric', 'statistics_noise_fraction': .02, 'window_check': 'off',
            'profile_parameter': 'R0', 'profile_points': 5})
        stats = result['payload']['statistics']
        assert stats['bootstrap']['method'] == 'relative_complex_parametric_bootstrap'
        assert stats['bootstrap']['noise_fraction'] == .02
        assert stats['window_stability'] is None
        assert stats['profile']['points'] and stats['profile']['parameter'] == 'R0'


def test_presets_survive_reopen_are_workspace_scoped_and_reject_invalid_update(tmp_path):
    workspace = tmp_path / 'work'
    preset = {'action': 'fit', 'config': {'circuit': 'R0-p(R1,C1)', 'seed': 7,
        'parameter_overrides_by_circuit': {'R0-p(R1,C1)': {'R1': {'initial': 5, 'lower': 4, 'upper': 6}}}}}
    with JobService(builtins(), workspace) as jobs:
        completed(jobs, 'presets', config={'operation': 'save', 'name': 'Мой RC / 1', 'preset': preset})
    with JobService(builtins(), workspace) as jobs:
        _, loaded = completed(jobs, 'presets', config={'operation': 'load', 'name': 'Мой RC / 1'})
        assert loaded['payload']['selected'] == preset
        store = (workspace / 'eis-presets.json').read_bytes()
        invalid = jobs.submit(JobRequest('eis', 'presets', (), {'operation': 'save', 'replace': True,
            'name': 'Мой RC / 1', 'preset': {'action': 'fit', 'config': {'seed': -1}}}))
        wait(jobs, lambda: invalid.state.terminal, 30)
        assert invalid.state == JobState.FAILED
        assert (workspace / 'eis-presets.json').read_bytes() == store
        completed(jobs, 'presets', config={'operation': 'delete', 'name': 'Мой RC / 1'})
        _, listed = completed(jobs, 'presets')
        assert listed['payload']['presets'] == []
    with JobService(builtins(), tmp_path / 'other-work') as jobs:
        _, listed = completed(jobs, 'presets')
        assert listed['payload']['presets'] == []


@pytest.mark.parametrize('action,stage,config', [
    ('reliable', 'Checking circuit selection stability', {'candidate_circuits': 'R0-p(R1,C1);R0',
        'reliability_restarts': 1, 'reliability_samples': 1000, 'reliability_drt_samples': 200}),
    ('statistics', 'Checking parameter intervals', {'circuit': 'R0-p(R1,C1)', 'statistics_samples': 1000}),
])
def test_research_cancel_preserves_peer_and_never_publishes_partial_result(tmp_path, action, stage, config):
    source = rc_spectrum(tmp_path / 'rc.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path / 'work', cancel_grace=.1) as jobs:
        record = jobs.submit(JobRequest('eis', action, (str(source),), config))
        peer = jobs.submit(JobRequest('cycling', 'analyze', (str(Path(__file__).parents[1] / 'examples/cycling.csv'),)))
        wait(jobs, lambda: record.message == stage or record.state.terminal, 40)
        assert not record.state.terminal, record.error
        jobs.cancel(record.id)
        wait(jobs, lambda: record.state.terminal and peer.state.terminal, 20)
        assert record.state == JobState.CANCELLED and record.result_path is None
        assert not (tmp_path / 'work/jobs' / record.id / 'result.json').exists()
        assert peer.state == JobState.SUCCEEDED


def test_reliability_drt_uses_the_requested_channel(tmp_path):
    f = np.logspace(2, -.5, 61)
    z1 = 20 + 50 / (1 + 1j * 2 * np.pi * f * .005)
    z2 = 2 + 5 / (1 + 1j * 2 * np.pi * f * .1)
    source = tmp_path / 'channels.csv'
    source.write_text('freq,Re(Z1)/Ohm,-Im(Z1)/Ohm,Re(Z2)/Ohm,-Im(Z2)/Ohm\n' + '\n'.join(
        f'{frequency:.17g},{a.real:.17g},{-a.imag:.17g},{b.real:.17g},{-b.imag:.17g}'
        for frequency, a, b in zip(f, z1, z2)))
    with JobService(builtins(), tmp_path / 'work') as jobs:
        _, result = completed(jobs, 'reliable', [source], {'channel': 'Z2', 'circuit': 'R0-p(R1,C1)',
            'reliability_restarts': 1, 'reliability_samples': 1, 'reliability_drt_samples': 1})
        payload = result['payload']
        assert payload['selected_channel'] == 'Z2'
        assert [row['value'] for row in payload['best']['parameters']] == pytest.approx([2, 5, .02], rel=1e-4)
        peak = max(payload['inference']['drt']['fit']['peaks'], key=lambda p: p['gamma_ohm'])
        assert abs(np.log10(peak['tau_seconds'] / .1)) < .25
        assert payload['inference']['fast_analysis']['selected_channel'] == 'Z2'
