import time
from importlib.metadata import version
from chem_suite.core.artifacts import JobCancelled, write_json


def dataset_payload(dataset, kk, provenance, predicted=None):
    from .legacy.eis_pipeline import _json_value, kk_result_dict
    from .views import spectrum_plots
    return {
        'sources': [provenance], 'dataset_type': 'eis.spectrum.v1',
        'point_count': len(dataset.frequencies), 'selected_channel': dataset.metadata.get('selected_channel', 'Z'),
        'source_format': dataset.source_format, 'columns': dataset.columns, 'metadata': _json_value(dataset.metadata),
        'kk': kk_result_dict(kk), 'plots': spectrum_plots(dataset.frequencies, dataset.z, predicted, kk),
    }


def load_files(request, context):
    from .legacy.eis_core import lin_kk_check
    from .legacy.eis_io import load_eis_file
    from .legacy.eis_pipeline import discover_input_files
    from .views import save_spectrum
    paths = discover_input_files(request.inputs, recursive=bool(request.config.get('recursive', True)))
    if not paths:
        raise ValueError('No supported spectrum files found')
    entries = []
    for index, path in enumerate(paths):
        context.progress(index / len(paths), f'Loading spectrum {index + 1}/{len(paths)}: {path}')
        try:
            source, provenance = context.snapshot(path)
            identity = request.config.get('source_aliases', {}).get(str(path))
            if identity:
                if identity['sha256'] != provenance['sha256']:
                    raise ValueError('Saved source hash does not match its provenance')
                provenance['path'] = identity['path']
                path = identity['path']
            dataset = load_eis_file(source, channel=request.config.get('channel') or None)
            kk = lin_kk_check(dataset.frequencies, dataset.z)
            payload = dataset_payload(dataset, kk, provenance)
            payload['summary'] = {
                'point_count': len(dataset.frequencies), 'selected_channel': payload['selected_channel'],
                'source_format': dataset.source_format, 'kk_status': kk.status,
            }
            preview = context.artifact(f'loaded/{index}/preview.json')
            write_json(preview, {'payload': payload})
            save_spectrum(context.artifact(f'loaded/{index}/spectrum.npz'), dataset, kk)
            entries.append({
                'path': path, 'success': True, 'preview_path': str(preview),
                'point_count': len(dataset.frequencies), 'kk': kk.status, 'channel': payload['selected_channel'],
                'channels': dataset.metadata.get('available_channels', [payload['selected_channel']]),
                'source_format': dataset.source_format,
            })
        except JobCancelled:
            raise
        except Exception as exc:
            entries.append({'path': path, 'success': False, 'error': str(exc)})
    return {'entries': entries, 'loaded_count': sum(entry['success'] for entry in entries)}


def fit_file(request, context):
    from .legacy.eis_core import lin_kk_check, family_bic_evidence
    from .legacy.eis_io import load_eis_file
    from .legacy.eis_pipeline import fit_result_dict, fit_spectrum
    from .views import save_spectrum
    if len(request.inputs) != 1:
        raise ValueError('EIS needs exactly one spectrum file')
    from .advanced import validate_overrides
    validate_overrides(request.config.get("parameter_overrides_by_circuit", {}))
    source, provenance = context.snapshot(request.inputs[0])
    identity = request.config.get('source_identity')
    if identity:
        if identity['sha256'] != provenance['sha256']:
            raise ValueError('Saved source hash does not match its provenance')
        provenance['path'] = identity['path']
    context.progress(0.05, 'Reading spectrum')
    dataset = load_eis_file(source, channel=request.config.get('channel') or None)
    context.progress(0.15, 'Checking Kramers–Kronig consistency')
    kk = lin_kk_check(dataset.frequencies, dataset.z)
    context.check_cancelled()
    circuit = str(request.config.get('circuit', 'adaptive'))
    restarts = int(request.config.get('restarts', 1))
    budget = int(request.config.get('max_evaluations', 5000))
    if not 1 <= restarts <= 100 or not 10 <= budget <= 1_000_000:
        raise ValueError('Invalid restart count or optimization budget')
    fitted_count = 0
    def report(result):
        nonlocal fitted_count
        fitted_count += 1
        context.progress(min(0.9, 0.2 + fitted_count * 0.025), f'{result.circuit_string}: {result.status}')
    started = time.monotonic()
    candidates = request.config.get('candidate_circuits') or None
    if isinstance(candidates, str):
        candidates = [value.strip() for value in candidates.split(';') if value.strip()]
    if candidates and (len(candidates) > 40 or not all(isinstance(value, str) for value in candidates)):
        raise ValueError('Invalid candidate circuit list')
    outcome = fit_spectrum(
        dataset.frequencies, dataset.z, circuits=candidates or (None if circuit == 'adaptive' else (circuit,)),
        parameter_overrides_by_circuit=request.config.get('parameter_overrides_by_circuit'),
        fit_restarts=restarts, restart_seed=int(request.config.get('seed', 0)), max_fit_evaluations=budget,
        on_result=report, should_cancel=context.cancel_event.is_set,
    )
    context.check_cancelled()
    if outcome.best is None or not outcome.best.success or outcome.best.model is None:
        raise ValueError('No circuit converged: ' + '; '.join(item.error_message for item in outcome.fits))
    best = outcome.best
    predicted = best.model.predict(dataset.frequencies)
    save_spectrum(context.artifact('spectrum.npz'), dataset, kk, predicted)
    context.progress(0.95, 'Saving results')
    payload = dataset_payload(dataset, kk, provenance, predicted)
    fit_records = []
    for fitted in outcome.fits:
        row = fit_result_dict(fitted, is_best=fitted is best)
        if fitted.success and fitted.model is not None:
            names, units = fitted.model.get_param_names()
            for parameter, name, unit in zip(row['parameters'], names, units):
                if parameter['name'] != name:
                    raise ValueError('Unsupported circuit parameter naming')
                parameter['unit'] = unit
        fit_records.append(row)
    best_record = next(row for row in fit_records if row['is_best'])
    payload.update({
        'summary': {'circuit': best.circuit_string, 'point_count': len(dataset.frequencies),
                    'mean_fit_error_percent': round(best.mean_fit_error, 5), 'model_status': best.status,
                    'kk_status': kk.status, 'selected_channel': payload['selected_channel']},
        'dependencies': {p: version(p) for p in ('numpy', 'scipy', 'impedance')},
        'best': best_record, 'parameter_error_method': 'local_covariance_1sigma',
        'diagnostics': list(best.flags) + ([f'KK:{kk.status}'] if not kk.success or kk.status != 'PASS' else []),
        'routing': outcome.routing_metadata, 'fit_seconds': time.monotonic() - started,
        'model_evidence': family_bic_evidence(outcome.fits),
        'tables': [{'title': 'Parameters', 'rows': best_record['parameters']}],
        'fits': fit_records, 'artifacts': ['spectrum.npz'],
    })
    from .research import save_candidates
    save_candidates(payload, dataset, kk, context)
    return payload


def preserve_identity(provenance, config):
    identity = config.get('source_identity')
    if identity:
        if not isinstance(identity.get('path'), str) or identity['sha256'] != provenance['sha256']:
            raise ValueError('Saved source hash does not match its provenance')
        provenance['path'] = identity['path']
