"""Advanced EIS handlers. All scientific libraries are loaded in worker processes."""
import json
import math
from pathlib import Path
from dataclasses import fields

from chem_suite.core.artifacts import write_json
from chem_suite.core.plots import plot, series


def parameter_defaults(request, context):
    from .legacy.eis_core import (build_bounds_and_guess, estimate_dataset_scale, parameter_names,
                                  apply_parameter_overrides)
    from .legacy.eis_io import load_eis_file
    from impedance.models.circuits import CustomCircuit
    if len(request.inputs) != 1:
        raise ValueError('EIS needs exactly one spectrum file')
    circuit = str(request.config['circuit'])
    source, provenance = context.snapshot(request.inputs[0])
    dataset = load_eis_file(source, channel=request.config.get('channel') or None)
    low, high, guess = build_bounds_and_guess(circuit, estimate_dataset_scale(dataset.frequencies, dataset.z))
    low, high, guess = apply_parameter_overrides(circuit, low, high, guess,
        request.config.get('parameter_overrides_by_circuit', {}).get(circuit))
    model = CustomCircuit(circuit=circuit, initial_guess=guess)
    names, units = model.get_param_names()
    if names != parameter_names(circuit):
        raise ValueError('Unsupported circuit parameter naming')
    return {'circuit': circuit, 'sources': [provenance], 'parameters': [
        {'name': name, 'unit': unit, 'initial': initial, 'lower': lower, 'upper': upper}
        for name, unit, initial, lower, upper in zip(names, units, guess, low, high)]}


def validate_overrides(overrides):
    if not isinstance(overrides, dict):
        raise ValueError('Parameter overrides must be a mapping')
    for circuit, parameters in overrides.items():
        if not isinstance(circuit, str) or not isinstance(parameters, dict):
            raise ValueError('Parameter overrides must be a mapping')
        from .legacy.eis_core import parameter_names
        unknown = set(parameters) - set(parameter_names(circuit))
        if unknown:
            raise ValueError('Unknown circuit parameters: ' + ', '.join(sorted(unknown)))
        for name, bounds in parameters.items():
            if not isinstance(bounds, dict) or set(bounds) - {'initial', 'lower', 'upper'}:
                raise ValueError(f'Invalid bounds for {name}')
            values = {key: float(value) for key, value in bounds.items()}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError('Parameter values must be finite')
            if 'lower' in values and 'upper' in values and values['lower'] >= values['upper']:
                raise ValueError('Lower bound must be below upper bound')
            if 'initial' in values and not values.get('lower', -math.inf) <= values['initial'] <= values.get('upper', math.inf):
                raise ValueError('Initial value must be inside bounds')


def drt_plots(result, *, limit=2000):
    fitted = result['fit']
    return [plot('DRT distribution', 'Time constant, s', 'γ, Ω', [
        series('DRT', fitted['tau_seconds'], fitted['gamma_ohm'], limit=limit),
    ], xscale='log')]


def analyze_drt(request, context):
    import numpy as np
    from .legacy.eis_io import load_eis_file
    from .legacy.eis_core import lin_kk_check
    from .legacy.eis_drt import select_regularization, fit_drt, drt_peak_stability, _basis
    from .actions import dataset_payload, preserve_identity
    from .views import save_spectrum
    if len(request.inputs) != 1:
        raise ValueError('EIS needs exactly one spectrum file')
    config = request.config
    grid = config.get('drt_lambda_grid', '0.0001,0.001,0.01,0.1,1')
    candidates = [float(v.strip()) for v in grid.split(',')] if isinstance(grid, str) else list(grid)
    points, folds = int(config.get('drt_tau_points', 81)), int(config.get('drt_folds', 5))
    samples = int(config.get('drt_stability_samples', 0))
    if not 21 <= points <= 401 or not 2 <= folds <= 10 or not 0 <= samples <= 200:
        raise ValueError('Invalid DRT grid, folds or sample count')
    if not 1 <= len(candidates) <= 12 or any(not math.isfinite(v) or v < 0 for v in candidates):
        raise ValueError('DRT regularization must be finite and non-negative')
    source, provenance = context.snapshot(request.inputs[0])
    preserve_identity(provenance, config)
    dataset = load_eis_file(source, channel=config.get('channel') or None)
    if len(dataset.frequencies) < max(8, folds * 2):
        raise ValueError('Not enough spectrum points for DRT validation')
    context.progress(.1, 'Checking Kramers–Kronig consistency')
    kk = lin_kk_check(dataset.frequencies, dataset.z)
    context.progress(.2, 'Selecting DRT regularization')
    selection = select_regularization(dataset.frequencies, dataset.z, candidates, tau_points=points,
                                     folds=folds, check_cancelled=context.check_cancelled)
    context.progress(.5, 'Fitting DRT')
    fitted = fit_drt(dataset.frequencies, dataset.z, tau_points=points, regularization=selection['selected'])
    if not fitted['success']:
        raise ValueError('DRT did not converge: ' + str(fitted['message']))
    context.check_cancelled()
    stability = None
    if samples:
        context.progress(.6, 'Checking DRT peak stability')
        stability = drt_peak_stability(dataset.frequencies, dataset.z, fitted, candidates, samples=samples,
            tau_points=points, seed=int(config.get('seed', 0)), check_cancelled=context.check_cancelled)
    result = {'analysis': 'nonnegative_drt', 'fit': fitted, 'selection': selection, 'stability': stability,
              'interpretation_warning': 'DRT peaks are relaxation features, not automatically chemical mechanisms'}
    predicted = _basis(dataset.frequencies, np.asarray(fitted['tau_seconds'])) @ np.r_[
        fitted['r_infinity_ohm'], fitted['gamma_ohm']]
    save_spectrum(context.artifact('spectrum.npz'), dataset, kk, predicted)
    write_json(context.artifact('drt.json'), result)
    payload = dataset_payload(dataset, kk, provenance, predicted)
    payload.update(drt=result, artifacts=['spectrum.npz', 'drt.json'], fits=[],
        summary={'analysis': 'DRT', 'point_count': len(dataset.frequencies), 'selected_channel': payload['selected_channel'],
                 'drt_peak_count': fitted['resolved_peak_count'], 'drt_error_percent': fitted['relative_rmse'] * 100,
                 'regularization': selection['selected'], 'kk_status': kk.status},
        diagnostics=[result['interpretation_warning']] + (['DRT regularization reached the grid edge'] if selection['selected_hits_grid_edge'] else []))
    payload['plots'] += drt_plots(result)
    return payload


def restore_analysis(result_path):
    import numpy as np
    from impedance.models.circuits import CustomCircuit
    from .legacy.eis_core import FitResult, KramersKronigResult
    from .legacy.eis_pipeline import AnalysisResult
    result_path = Path(result_path).resolve(strict=True)
    result = json.loads(result_path.read_text())
    if result.get('module') != 'eis' or result.get('action') not in {'fit', 'reliable', 'statistics', 'import_reliable'} or result.get('schema_version') != 1:
        raise ValueError('Export requires a completed circuit fit')
    payload, row = result['payload'], result['payload']['best']
    values = [p['value'] for p in row['parameters']]
    model = CustomCircuit(circuit=row['circuit'], initial_guess=values)
    if model.get_param_names()[0] != [p['name'] for p in row['parameters']]:
        raise ValueError('Saved circuit parameter names do not match the circuit')
    model.parameters_ = np.asarray(values)
    model.conf_ = np.asarray([p['confidence'] if p['confidence'] is not None else np.nan for p in row['parameters']])
    with np.load(result_path.parent / 'spectrum.npz', allow_pickle=False) as data:
        frequencies = data['frequency_hz'].copy()
        predicted = data['fit_real_ohm'] + 1j * data['fit_imag_ohm']
        if not np.allclose(model.predict(frequencies), predicted, rtol=1e-9, atol=1e-12):
            raise ValueError('Saved circuit parameters do not reproduce the fitted spectrum')
    fit = FitResult(row['circuit'], row['success'], model=model, status=row['status'],
        mean_fit_error=row['mean_fit_error_percent'], max_param_error=row['max_parameter_error_percent'] or 0,
        n_params=row['parameter_count'], flags=tuple(row['flags']),
        aic=row['aic'], bic=row['bic'], rss_weighted=row['weighted_rss'])
    keys = {field.name for field in fields(KramersKronigResult)}
    kk = KramersKronigResult(**{k: v for k, v in payload['kk'].items() if k in keys})
    provenance = payload['sources'][0]
    source = (result_path.parent / provenance['snapshot']).resolve(strict=True)
    if not source.is_relative_to(result_path.parent):
        raise ValueError('Source snapshot escapes the result directory')
    analysis = AnalysisResult(provenance['path'], success=True, stage='complete', best=fit, fits=[fit], kk=kk,
            point_count=payload['point_count'], selected_channel=payload['selected_channel'], source_format=payload['source_format'])
    return analysis, frequencies, source, provenance


def make_spice_package(result_path, target, config, context):
    from .legacy.eis_spice_export import export_spice_package
    analysis, frequencies, source, provenance = restore_analysis(result_path)
    context.check_cancelled()
    package = export_spice_package(analysis, frequencies, target, source_file=source,
                                  ngspice_executable=config.get('ngspice_executable') or None)
    context.check_cancelled()
    # Preserve the original source identity, while validation operates on its saved snapshot.
    passport_path = Path(package.passport_file)
    passport = json.loads(passport_path.read_text())
    if passport['source']['sha256'].lower() != provenance['sha256'].lower():
        raise ValueError('Saved source hash does not match its provenance')
    passport['source']['file'] = provenance['path']
    passport['source']['sha256'] = provenance['sha256'].upper()
    passport['chem_suite_result'] = str(Path(result_path).resolve())
    write_json(passport_path, passport)
    return package


def spice_package(request, context):
    from chem_suite.exports.actions import copy_file
    if len(request.inputs) != 1:
        raise ValueError('SPICE requires one completed circuit fit')
    root = context.publication(request.config['destination'])
    context.progress(.1, 'Validating SPICE package')
    package = make_spice_package(request.inputs[0], context.artifact('spice-package'), request.config, context)
    for name in ('model.lib', 'passport.json'):
        copy_file(Path(package.package_directory) / name, root / name, context)
    return {'destination': request.config['destination'], 'experiment_count': 1,
            'selected_order': package.selected_order, 'simulator_version': package.simulator_version}
