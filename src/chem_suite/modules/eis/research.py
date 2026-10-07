"""Selection views and research diagnostics, exclusively in supervised workers."""
import copy
import hashlib
import json
import math
from pathlib import Path
from dataclasses import replace

from chem_suite.core.artifacts import write_json
from chem_suite.core.plots import plot, series


def save_candidates(payload, dataset, kk, context):
    import numpy as np
    from impedance.models.circuits import CustomCircuit
    from .views import spectrum_plots
    responses = []
    for row in payload['fits']:
        context.check_cancelled()
        if not row['success'] or not row.get('parameters'):
            continue
        model = CustomCircuit(row['circuit'], initial_guess=[p['value'] for p in row['parameters']])
        if model.get_param_names()[0] != [p['name'] for p in row['parameters']]:
            raise ValueError('Saved circuit parameter names do not match the circuit')
        model.parameters_ = np.asarray([p['value'] for p in row['parameters']])
        response = model.predict(dataset.frequencies)
        if not np.all(np.isfinite(response)):
            raise ValueError('Candidate prediction must be finite')
        row['prediction_index'] = len(responses)
        row['plots'] = spectrum_plots(dataset.frequencies, dataset.z, response, kk)
        responses.append(response)
    if responses:
        np.savez(context.artifact('candidates.npz'), frequency_hz=dataset.frequencies,
                 real_ohm=np.asarray(responses).real, imag_ohm=np.asarray(responses).imag)
        payload['artifacts'].append('candidates.npz')


def saved_dataset(payload, context):
    from .legacy.eis_io import load_eis_file
    source = context.artifact(payload['sources'][0]['snapshot'])
    return source, load_eis_file(source, channel=payload['selected_channel'])


def fast_contract(payload):
    return {'schema_version': 1, 'success': True, 'file': payload['sources'][0]['path'],
            'stage': 'complete', 'point_count': payload['point_count'], 'selected_channel': payload['selected_channel'],
            'source_format': payload['source_format'], 'columns': payload['columns'], 'metadata': payload['metadata'],
            'best': payload['best'], 'fits': payload['fits'], 'kk': payload['kk'],
            'model_evidence': payload['model_evidence']}


def bounded_integer(config, key, default, low, high):
    value = int(config.get(key, default))
    if not low <= value <= high:
        raise ValueError('Invalid diagnostic sample count or grid size: ' + key)
    return value


def reliability(request, context):
    from .actions import fit_file
    from .legacy.eis_uncertainty import topology_bootstrap
    from .legacy.eis_drt import analyze_drt
    from .legacy.eis_inference import build_inference_decision
    from .advanced import drt_plots
    config = request.config
    samples = bounded_integer(config, 'reliability_samples', 30, 1, 1000)
    drt_samples = bounded_integer(config, 'reliability_drt_samples', 30, 1, 200)
    restarts = bounded_integer(config, 'reliability_restarts', 3, 1, 100)
    payload = fit_file(replace(request, config={**config, 'restarts': restarts}), context)
    source, dataset = saved_dataset(payload, context)
    circuits = [row['circuit'] for row in payload['fits']]
    context.progress(.35, 'Checking circuit selection stability')
    if len(circuits) >= 2:
        topology = topology_bootstrap(dataset.frequencies, dataset.z, circuits,
            samples=samples, seed=int(config.get('seed', 0)), restarts=1,
            max_evaluations=int(config.get('max_evaluations', 5000)), check_cancelled=context.check_cancelled,
            parameter_overrides_by_circuit=config.get('parameter_overrides_by_circuit'))
    else:
        topology = {'stable_recommendation': False, 'stable_family_recommendation': False,
                    'reason': 'topology stability needs at least two candidate circuits'}
    context.progress(.65, 'Checking DRT peak stability')
    drt = analyze_drt(source, stability_samples=drt_samples, seed=int(config.get('seed', 0)),
                      channel=payload['selected_channel'], check_cancelled=context.check_cancelled)
    if not drt['fit']['success']:
        raise ValueError('DRT did not converge: ' + str(drt['fit']['message']))
    drt['file'] = payload['sources'][0]['path']
    inference = {'schema_version': 1, 'analysis': 'unified_eis_inference', 'mode': 'reliable',
                 'file': payload['sources'][0]['path'], 'fast_analysis': fast_contract(payload),
                 'topology_bootstrap': topology, 'drt': drt, 'resolution': None}
    inference['decision'] = build_inference_decision(fast_result=inference['fast_analysis'], topology=topology, drt=drt)
    payload.update(inference=inference, drt=drt)
    payload['summary'].update(verdict=inference['decision']['verdict'],
                              recommended_topology=inference['decision'].get('recommended_topology'),
                              recommended_family=inference['decision'].get('recommended_family'))
    payload['diagnostics'].extend([topology.get('interpretation', 'Circuit selection stability was not evaluated'),
                                   drt['interpretation_warning']])
    if samples < 30 or drt_samples < 30:
        payload['diagnostics'].append('Fewer than 30 resamples: exploratory stability estimate')
    write_json(context.artifact('inference.json'), inference)
    payload['artifacts'].append('inference.json')
    payload['plots'] += drt_plots(drt)
    context.check_cancelled()
    return payload


def statistics(request, context):
    from .actions import fit_file
    from .legacy.eis_uncertainty import residual_bootstrap, parametric_bootstrap, profile_likelihood
    from .legacy.eis_identifiability import characteristic_support, frequency_window_stability
    config = request.config
    samples = bounded_integer(config, 'statistics_samples', 100, 1, 1000)
    points = bounded_integer(config, 'profile_points', 41, 5, 201)
    span = float(config.get('profile_span_decades', .5))
    if not math.isfinite(span) or not 0 < span <= 6:
        raise ValueError('Invalid profile span')
    payload = fit_file(request, context)
    _, dataset = saved_dataset(payload, context)
    circuit = payload['best']['circuit']
    values = [p['value'] for p in payload['best']['parameters']]
    kwargs = dict(samples=samples, seed=int(config.get('seed', 0)), restarts=int(config.get('restarts', 1)),
                  max_evaluations=int(config.get('max_evaluations', 5000)), check_cancelled=context.check_cancelled,
                  parameter_overrides_by_circuit=config.get('parameter_overrides_by_circuit'))
    context.progress(.35, 'Checking parameter intervals')
    method = config.get('statistics_method', 'residual')
    if method == 'residual':
        bootstrap = residual_bootstrap(dataset.frequencies, dataset.z, circuit, **kwargs)
    elif method == 'parametric':
        noise = float(config.get('statistics_noise_fraction', .01))
        if not math.isfinite(noise) or not 0 <= noise <= 1:
            raise ValueError('Invalid relative noise fraction')
        bootstrap = parametric_bootstrap(dataset.frequencies, dataset.z, circuit, noise_fraction=noise, **kwargs)
    else:
        raise ValueError('Unknown bootstrap method')
    units = {p['name']: p['unit'] for p in payload['best']['parameters']}
    for row in bootstrap['parameters']:
        row['unit'] = units[row['name']]
    diagnostics = {'schema_version': 1, 'analysis': 'eis_parameter_diagnostics', 'circuit': circuit,
                   'bootstrap': bootstrap, 'profile': None, 'window_stability': None,
                   'characteristic_support': characteristic_support(dataset.frequencies, circuit, values),
                   'interpretation_warning': 'Intervals are conditional on the circuit and noise model; they do not establish a physical mechanism'}
    if bootstrap['accepted'] == 0:
        payload['diagnostics'].append('No bootstrap fits were accepted; parameter intervals are unavailable')
    parameter = str(config.get('profile_parameter', '')).strip()
    if parameter:
        context.progress(.65, 'Calculating profile likelihood')
        profile = profile_likelihood(dataset.frequencies, dataset.z, circuit, parameter, grid_points=points,
            span_decades=span, restarts=kwargs['restarts'], max_evaluations=kwargs['max_evaluations'],
            check_cancelled=context.check_cancelled, parameter_overrides_by_circuit=kwargs['parameter_overrides_by_circuit'])
        profile['unit'] = units[parameter]
        diagnostics['profile'] = profile
        payload['plots'].append(plot('Profile likelihood', parameter + ', ' + units[parameter], 'Δχ²', [
            series('Profile', [p['value'] for p in profile['points']], [p['delta_chi_square'] for p in profile['points']]),
            series('95% threshold', [p['value'] for p in profile['points']], [profile['threshold_delta_chi_square']] * len(profile['points'])),
        ]))
    window_mode = config.get('window_check', 'on')
    if window_mode not in ('on', 'off'):
        raise ValueError('Unknown frequency window setting')
    if window_mode == 'on':
        context.progress(.8, 'Checking frequency window stability')
        diagnostics['window_stability'] = frequency_window_stability(dataset.frequencies, dataset.z, circuit, values,
            restarts=kwargs['restarts'], max_evaluations=kwargs['max_evaluations'], seed=kwargs['seed'],
            check_cancelled=context.check_cancelled, parameter_overrides_by_circuit=kwargs['parameter_overrides_by_circuit'])
    payload['statistics'] = diagnostics
    payload['summary'].update(bootstrap_method=bootstrap['method'], bootstrap_accepted=bootstrap['accepted'], bootstrap_requested=samples)
    payload['diagnostics'].append(diagnostics['interpretation_warning'])
    if samples < 30:
        payload['diagnostics'].append('Fewer than 30 resamples: exploratory interval estimate')
    if diagnostics['profile'] and diagnostics['profile']['interval_hits_grid_edge']:
        payload['diagnostics'].append('Profile interval reached the grid edge; the interval is incomplete')
    write_json(context.artifact('statistics.json'), diagnostics)
    payload['artifacts'].append('statistics.json')
    context.check_cancelled()
    return payload


def import_reliability(request, context):
    """Import a full report paired with its spectrum; never trust a bare verdict."""
    import numpy as np
    from impedance.models.circuits import CustomCircuit
    from .legacy.eis_io import load_eis_file
    from .legacy.eis_core import lin_kk_check
    from .legacy.eis_inference import build_inference_decision
    from .actions import dataset_payload, preserve_identity
    from .views import save_spectrum
    from .advanced import drt_plots
    if len(request.inputs) != 2:
        raise ValueError('Import requires the spectrum and a full inference JSON')
    source, provenance = context.snapshot(request.inputs[0])
    preserve_identity(provenance, request.config)
    report_source, _ = context.snapshot(request.inputs[1])
    report = json.loads(report_source.read_text())
    native = report.get('module') == 'eis' and report.get('schema_version') == 1
    if native:
        native_payload = report['payload']
        if native_payload['sources'][0]['sha256'] != provenance['sha256']:
            raise ValueError('Imported report source hash does not match this spectrum')
        inference = copy.deepcopy(native_payload.get('inference'))
    else:
        inference = report
    if not isinstance(inference, dict) or inference.get('schema_version') != 1 or inference.get('analysis') != 'unified_eis_inference' or inference.get('mode') != 'reliable':
        raise ValueError('A full reliable inference report is required')
    fast = inference.get('fast_analysis') or {}
    if not fast.get('success') or not fast.get('best', {}).get('success') or not fast.get('fits'):
        raise ValueError('Imported inference does not contain completed fits')
    if not native and Path(inference.get('file', '')).resolve() != Path(provenance['path']):
        raise ValueError('Imported report belongs to another spectrum')
    dataset = load_eis_file(source, channel=request.config.get('channel') or fast.get('selected_channel') or None)
    if fast.get('point_count') != len(dataset.frequencies) or fast.get('selected_channel') != dataset.metadata.get('selected_channel', 'Z'):
        raise ValueError('Imported report channel or point count does not match this spectrum')
    kk = lin_kk_check(dataset.frequencies, dataset.z)
    if (fast.get('kk') or {}).get('status') != kk.status:
        raise ValueError('Imported report KK status does not match this spectrum')
    fits = copy.deepcopy(fast['fits'])
    best = next((r for r in fits if r['circuit'] == fast['best']['circuit'] and r.get('success')), None)
    if best is None:
        raise ValueError('Imported best circuit is missing from its candidates')
    for row in fits:
        row['is_best'] = row is best
        row.pop('plots', None)
        row.pop('prediction_index', None)
        if not row['success']:
            continue
        model = CustomCircuit(row['circuit'], initial_guess=[p['value'] for p in row['parameters']])
        names, units = model.get_param_names()
        if names != [p['name'] for p in row['parameters']]:
            raise ValueError('Imported parameter names do not match their circuit')
        response = model.predict(dataset.frequencies, use_initial=True)
        error = float(np.mean(np.abs(dataset.z - response) / np.maximum(np.abs(dataset.z), 1e-30)) * 100)
        if not math.isclose(error, row['mean_fit_error_percent'], rel_tol=1e-5, abs_tol=1e-7):
            raise ValueError('Imported parameters do not reproduce the reported fit error')
        for parameter, unit in zip(row['parameters'], units):
            parameter['unit'] = unit
        if row is best:
            predicted = response
    inference['file'] = provenance['path']
    inference['decision'] = build_inference_decision(fast_result=fast, topology=inference.get('topology_bootstrap'),
                                                   drt=inference.get('drt'), resolution=inference.get('resolution'))
    payload = dataset_payload(dataset, kk, provenance, predicted)
    payload.update(best=best, fits=fits, model_evidence=fast.get('model_evidence', {}), inference=inference,
        parameter_error_method='local_covariance_1sigma', artifacts=['spectrum.npz', 'inference.json', 'imported-inference.json'],
        summary={'circuit': best['circuit'], 'point_count': len(dataset.frequencies), 'selected_channel': payload['selected_channel'],
                 'mean_fit_error_percent': best['mean_fit_error_percent'], 'model_status': best['status'],
                 'kk_status': kk.status, 'verdict': inference['decision']['verdict']},
        diagnostics=['Imported evidence; source and fit consistency were checked, diagnostics were not recalculated'])
    payload['import_verification'] = {'source_sha256': native, 'fit_reproduction': True, 'report_sha256': hashlib.sha256(report_source.read_bytes()).hexdigest()}
    if inference.get('drt'):
        payload['drt'] = inference['drt']
        payload['plots'] += drt_plots(payload['drt'])
    save_spectrum(context.artifact('spectrum.npz'), dataset, kk, predicted)
    save_candidates(payload, dataset, kk, context)
    write_json(context.artifact('inference.json'), inference)
    write_json(context.artifact('imported-inference.json'), report)
    return payload
