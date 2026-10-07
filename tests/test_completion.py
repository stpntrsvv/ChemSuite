import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from tests.test_isolation import wait
from tests.test_research import completed
from tests.test_advanced import rc_spectrum


def session_state(result_path, source):
    return {'language': 'en', 'active_module': 'eis', 'size': [1280, 820], 'panels': [
        {'module': 'eis', 'action': 'fit', 'controls': {'circuit': 'R0-p(R1,C1)'},
         'path': str(source), 'selected_source': str(source), 'result': {'$bundle': result_path},
         'datasets': {str(source): {'file': source.name, 'state': 'Completed', 'kk': 'PASS',
                       'fit_result_path': {'$bundle': result_path}, 'view_circuits': {'fit': 'R0-p(R1,C1)'}}}},
        {'module': 'cycling', 'action': 'analyze', 'controls': {}, 'path': '', 'result': None},
    ]}


def run_platform(jobs, module, action, inputs=(), config=None):
    record = jobs.submit(JobRequest(module, action, tuple(str(p) for p in inputs), config or {}))
    wait(jobs, lambda: record.state.terminal, 60)
    assert record.state == JobState.SUCCEEDED, record.error
    return record, json.loads(Path(record.result_path).read_text())['payload']


def test_portable_session_survives_move_and_missing_sources_and_preserves_export(tmp_path):
    source = rc_spectrum(tmp_path / 'source.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path / 'work') as jobs:
        fit, result = completed(jobs, 'fit', [source], {'circuit': 'R0-p(R1,C1)'})
        state = session_state(fit.result_path, source)
        destination = tmp_path / 'session'
        _, payload = run_platform(jobs, 'sessions', 'save', config={'state': state, 'destination': str(destination)})
        assert payload['bundle_count'] == 1
        moved = tmp_path / 'moved-session'
        destination.rename(moved)
        source.unlink()
        shutil.rmtree(Path(fit.result_path).parent)
        restored, payload = run_platform(jobs, 'sessions', 'restore', [moved/'session.json'])
        raw = payload['state']['panels'][0]['result']['$bundle']
        restored_result = json.loads(Path(raw).read_text())
        assert restored_result['payload']['best'] == result['payload']['best']
        assert payload['state']['panels'][0]['datasets'][str(source)]['view_circuits'] == {'fit': 'R0-p(R1,C1)'}
        assert Path(raw).is_relative_to(Path(restored.result_path).parent)
        export = tmp_path/'export'
        run_platform(jobs, 'exports', 'export', [raw], {'destination': str(export), 'outputs': {'excel': False}, 'formats': []})
        assert next(export.glob('*/spectrum.csv')).exists()
        reopened, opened = run_platform(jobs, 'sessions', 'result', [next(export.glob('*/result.json'))])
        assert opened['module'] == 'eis'
        from chem_suite.desktop.sessions import prepare_state
        prepared, results = prepare_state(payload['state'])
        data = prepared['panels'][0]['datasets'][str(source)]
        assert Path(data['runtime_source']).is_file()
        _, refit = completed(jobs, 'fit', [data['runtime_source']], {
            'circuit': 'R0-p(R1,C1)', 'source_identity': data['source_identity']})
        assert refit['payload']['sources'][0]['path'] == str(source)
        assert refit['payload']['best']['parameters'] == result['payload']['best']['parameters']
        _, drt = completed(jobs, 'drt', [data['runtime_source']], {'source_identity': data['source_identity'],
            'drt_tau_points': 21, 'drt_folds': 2, 'drt_lambda_grid': '.001,.01'})
        assert drt['payload']['sources'][0]['path'] == str(source)


def test_session_detects_corruption_and_path_escape_without_overwriting(tmp_path):
    source = rc_spectrum(tmp_path/'source.csv')
    with JobService(builtins(), tmp_path/'work') as jobs:
        fit, _ = completed(jobs, 'fit', [source], {'circuit': 'R0-p(R1,C1)'})
        destination = tmp_path/'session'
        state = session_state(fit.result_path, source)
        run_platform(jobs, 'sessions', 'save', config={'destination': str(destination), 'state': state})
        manifest = destination/'session.json'
        original = json.loads(manifest.read_text())
        artifact = next(destination.glob('bundles/*/spectrum.npz'))
        artifact.write_bytes(artifact.read_bytes()+b'corruption')
        record = jobs.submit(JobRequest('sessions', 'restore', (str(manifest),)))
        wait(jobs, lambda: record.state.terminal)
        assert record.state == JobState.FAILED and 'checksum mismatch' in record.error
        bad = {**original, 'files': {'../source.csv': '0'*64}}
        manifest.write_text(json.dumps(bad))
        record = jobs.submit(JobRequest('sessions', 'restore', (str(manifest),)))
        wait(jobs, lambda: record.state.terminal)
        assert record.state == JobState.FAILED and 'relative' in record.error
        record = jobs.submit(JobRequest('sessions', 'save', (), {'destination': str(destination), 'state': state}))
        wait(jobs, lambda: record.state.terminal)
        assert record.state == JobState.FAILED and manifest.exists()


def test_unfitted_loaded_spectrum_is_portable(tmp_path):
    source = rc_spectrum(tmp_path/'source.csv')
    with JobService(builtins(), tmp_path/'work') as jobs:
        loaded, result = completed(jobs, 'load', [source])
        state = session_state(None, source)
        state['panels'][0]['result'] = None
        state['panels'][0]['datasets'][str(source)] = {'state': 'Loaded', 'file': source.name,
            'preview_path': {'$bundle': result['payload']['entries'][0]['preview_path']}}
        target = tmp_path/'session'
        run_platform(jobs, 'sessions', 'save', config={'state': state, 'destination': str(target)})
        source.unlink()
        shutil.rmtree(Path(loaded.result_path).parent)
        _, restored = run_platform(jobs, 'sessions', 'restore', [target/'session.json'])
        raw = restored['state']['panels'][0]['datasets'][str(source)]['preview_path']['$bundle']
        preview = json.loads(Path(raw).read_text())
        assert preview['action'] == 'preview'
        assert preview['payload']['point_count'] == 61
        assert (Path(raw).parent/preview['payload']['sources'][0]['snapshot']).is_file()


def make_soc_series(tmp_path, count=5):
    frequencies = np.logspace(3, -1, 31)
    rows = []
    rng = np.random.default_rng(31)
    for index in range(count):
        resistance = 3 + index*.2
        z = 2 + resistance / (1+2j*np.pi*frequencies*resistance*.004)
        z += np.abs(z)*.0005*(rng.normal(size=len(z))+1j*rng.normal(size=len(z)))
        path = tmp_path/f'SOC{index}.csv'
        np.savetxt(path, np.c_[frequencies, z.real, z.imag], delimiter=',', header='frequency,real,imag', comments='')
        rows.append({'file': path.name, 'soc': 10+20*index})
    manifest = tmp_path/'soc.csv'
    manifest.write_text('file,soc\n'+'\n'.join(f"{r['file']},{r['soc']}" for r in rows))
    return manifest, rows


def test_joint_soc_fit_matches_original_numerics_and_exports_all_spectra(tmp_path):
    from chem_suite.modules.eis.legacy.eis_joint import joint_fit, load_manifest, cross_validate_smoothness
    manifest, rows = make_soc_series(tmp_path)
    expected = joint_fit(load_manifest(manifest), 'R0-p(R1,C1)', smoothness=1, restarts=1, max_evaluations=1000)
    cv = cross_validate_smoothness(load_manifest(manifest), 'R0-p(R1,C1)', [0, 1], max_folds=1,
                                  max_evaluations=1000, restarts=1)
    with JobService(builtins(), tmp_path/'work') as jobs:
        fit, result = completed(jobs, 'joint', [manifest], {'circuit': 'R0-p(R1,C1)', 'restarts': 1,
            'max_evaluations': 1000, 'joint_smoothness': 1}, timeout=60)
        report = result['payload']['analysis']
        assert report['cost'] == pytest.approx(expected['cost'], rel=1e-10)
        for actual, old in zip(report['joint_results'], expected['joint_results']):
            assert [p['value'] for p in actual['parameters']] == pytest.approx([p['value'] for p in old['parameters']], rel=1e-10)
            assert all(p['unit'] for p in actual['parameters'])
        fitcv, resultcv = completed(jobs, 'joint', [manifest], {'circuit': 'R0-p(R1,C1)', 'restarts': 1,
            'max_evaluations': 1000, 'joint_cv_grid': '0,1', 'joint_cv_folds': 1}, timeout=60)
        assert resultcv['payload']['analysis']['cross_validation']['selected_smoothness'] == cv['selected_smoothness']
        assert len(list(Path(fit.result_path).parent.glob('spectra/*/spectrum.npz'))) == 5
        target = tmp_path/'export'
        run_platform(jobs, 'exports', 'export', [fit.result_path], {
            'destination': str(target), 'outputs': {'excel': True, 'study_plots': False}, 'formats': []})
        assert (target/'results.xlsx').is_file()
        assert len(list(target.glob('*/spectra/*/spectrum.npz'))) == 5
        assert len(list(target.glob('*/sources/*/*.csv'))) == 6
        manifest.write_text(manifest.read_text().replace('SOC1.csv,30', 'SOC1.csv,10'))
        failed = jobs.submit(JobRequest('eis', 'joint', (str(manifest),), {'circuit': 'R0-p(R1,C1)'}))
        wait(jobs, lambda: failed.state.terminal)
        assert failed.state == JobState.FAILED and 'distinct finite' in failed.error


def test_pooled_series_uses_declared_metadata_and_does_not_change_fit_winners(tmp_path):
    manifest, rows = make_soc_series(tmp_path, count=3)
    with JobService(builtins(), tmp_path/'work') as jobs:
        fits = [completed(jobs, 'fit', [tmp_path/row['file']], {'circuit': 'R0-p(R1,C1)'}) for row in rows]
        _, result = completed(jobs, 'series', [record.result_path for record, _ in fits], {'series_manifest': str(manifest)})
        report = result['payload']['analysis']['series'][0]
        assert report['observations'] == 3
        assert report['soc_values'] == [10., 30., 50.]
        assert report['pooled_topology'] == 'R0-p(R1,C1)'
        assert result['payload']['plots']
        assert all(json.loads(Path(record.result_path).read_text())['payload']['best'] == result['payload']['best']
                   for record, result in fits)


def test_resolution_map_and_research_generator_are_isolated_and_portable(tmp_path):
    with JobService(builtins(), tmp_path/'work') as jobs:
        record, result = completed(jobs, 'resolution', config={'resolution_min_frequencies': '.1,.01',
            'resolution_noise_fractions': '.01', 'resolution_replicates': 1, 'resolution_points': 31}, timeout=60)
        payload = result['payload']
        assert len(payload['analysis']['cells']) == 2
        assert 'Synthetic' in payload['diagnostics'][0]
        destination = tmp_path/'resolution'
        run_platform(jobs, 'exports', 'export', [record.result_path], {
            'destination': str(destination), 'outputs': {'excel': False}, 'formats': ['svg']})
        assert next(destination.glob('*/study_*.svg')).is_file()
        destination = tmp_path/'corpus'
        generated, result = completed(jobs, 'research', config={'procedure': 'synthetic',
            'settings': {'circuits': ['R0-p(R1,C1)'], 'samples_per_circuit': 1, 'points': 21, 'seed': 7},
            'destination': str(destination)})
        assert len(list(destination.glob('spectra/*.csv'))) == 1
        _, benchmark = completed(jobs, 'research', [destination/'truth.jsonl'], {'procedure': 'interval-benchmark',
            'settings': {'bootstrap_samples': 2, 'parametric_bootstrap_samples': 2, 'max_evaluations': 100}}, timeout=60)
        assert benchmark['payload']['sources'][0]['sha256']
        assert len(benchmark['payload']['sources']) == 2
        research = jobs.submit(JobRequest('eis', 'resolution', (), {'resolution_replicates': 1000}))
        wait(jobs, lambda: research.state == JobState.RUNNING)
        jobs.cancel(research.id)
        wait(jobs, lambda: research.state.terminal)
        assert research.state == JobState.CANCELLED and research.result_path is None


def test_default_controller_export_produces_both_models_with_original_provenance(tmp_path):
    source = rc_spectrum(tmp_path/'source.csv', low=-.5, high=2)
    with JobService(builtins(), tmp_path/'work') as jobs:
        fit, result = completed(jobs, 'fit', [source], {'circuit': 'R0-p(R1,C1)'})
        destination = tmp_path/'controller'
        _, payload = completed(jobs, 'controller', [fit.result_path], {'destination': str(destination),
            'controller_sample_period_s': 1e-6, 'controller_current_full_scale_a': 10,
            'controller_max_frequency_hz': 100}, timeout=60)
        passport = json.loads((destination/'passport.json').read_text())
        assert passport['source']['file'] == str(source)
        assert passport['source']['sha256'].lower() == result['payload']['sources'][0]['sha256']
        assert passport['status'] == 'validated'
        assert payload['payload']['section_count'] > 0
        assert (destination/'eis_model_q31.c').is_file()
        source_copy = Path(fit.result_path).parent/result['payload']['sources'][0]['snapshot']
        source_copy.write_text('corrupted')
        rejected = jobs.submit(JobRequest('eis', 'controller', (fit.result_path,), {
            'destination': str(tmp_path/'rejected'), 'controller_max_frequency_hz': 100}))
        wait(jobs, lambda: rejected.state.terminal)
        assert rejected.state == JobState.FAILED and not (tmp_path/'rejected').exists()
