"""Series and resolution workflows. No scientific state crosses the worker boundary."""
import csv
import json
import math
from pathlib import Path

from chem_suite.core.artifacts import write_json
from chem_suite.core.plots import plot, series


def numbers(raw, *, minimum=0, maximum=math.inf):
    values = [float(v.strip()) for v in raw.split(',') if v.strip()] if isinstance(raw, str) else list(raw)
    if not values or len(values) > 30 or any(not math.isfinite(v) or not minimum <= v <= maximum for v in values):
        raise ValueError('Invalid numeric grid')
    return sorted(set(values))


def finish(context, report, sources, plots, tables, diagnostics=()):
    write_json(context.artifact('analysis.json'), report)
    return {'sources': sources, 'analysis': report, 'plots': plots, 'tables': tables,
            'artifacts': ['analysis.json'], 'dataset_type': 'eis.study.v1',
            'summary': {'analysis': report['analysis'], 'source_count': len(sources)},
            'diagnostics': list(diagnostics)}


def pooled_series(request, context):
    from .legacy.eis_series import analyze_result_series
    from .research import fast_contract
    if len(request.inputs) < 2:
        raise ValueError('Series analysis needs at least two completed fits')
    metadata = {}
    manifest = request.config.get('series_manifest')
    sources, rows = [], []
    if manifest:
        saved, provenance = context.snapshot(manifest)
        identity = request.config.get('series_manifest_identity')
        if identity:
            if provenance['sha256'] != identity['sha256']:
                raise ValueError('Saved manifest checksum mismatch')
            provenance['path'] = identity['path']
        sources.append(provenance)
        with saved.open(encoding='utf-8-sig', newline='') as stream:
            for item in csv.DictReader(stream):
                path = (Path(provenance['path']).parent / item['file']).resolve()
                soc = float(item['soc'])
                if not math.isfinite(soc):
                    raise ValueError('SOC must be finite')
                if str(path) in metadata:
                    raise ValueError('Duplicate source in series manifest')
                metadata[str(path)] = {'series_id': item.get('series_id') or 'declared-series',
                    'cell_id': item.get('cell_id') or 'declared-cell', 'soc': soc,
                    'direction': item.get('direction') or 'unspecified'}
    for index, raw in enumerate(request.inputs):
        context.progress(index / len(request.inputs) * .8, 'Reading completed series fits')
        saved, provenance = context.snapshot(raw)
        result = json.loads(saved.read_text())
        if result.get('schema_version') != 1 or result.get('module') != 'eis' or result.get('action') not in {'fit', 'reliable', 'statistics', 'import_reliable'}:
            raise ValueError('Series analysis requires completed EIS fits')
        payload = result['payload']
        if not payload.get('best', {}).get('success'):
            raise ValueError('Series contains an unsuccessful fit')
        source = payload['sources'][0]['path']
        row = fast_contract(payload)
        row['file_name'] = Path(source).name
        if manifest:
            if source not in metadata:
                raise ValueError('Source missing from series manifest: ' + source)
            row['series_metadata'] = metadata[source]
        rows.append(row)
        sources.append(provenance)
    report = analyze_result_series(rows)
    plots, tables = [], []
    summaries = []
    for group in report['series']:
        summaries.append({k: v for k, v in group.items() if not isinstance(v, (dict, list))})
        tables.append({'title': group['series_id'] + ' · Topology evidence', 'rows': group['pooled_topology_evidence']})
        for trajectory in group['pooled_parameter_trajectories']:
            points = trajectory['curve']
            unit = next((p.get('unit', '') for row in rows for fit in row['fits']
                if fit['circuit'] == group['pooled_topology'] for p in fit.get('parameters', [])
                if p['name'] == trajectory['parameter']), '')
            trajectory['unit'] = unit
            tables.append({'title': group['series_id'] + ' · ' + trajectory['parameter'], 'rows': points})
            plots.append(plot(group['series_id'] + ' · ' + trajectory['parameter'], 'SOC',
                trajectory['parameter'] + (', ' + unit if unit else ''),
                [series('Smoothed trajectory', [p['soc'] for p in points], [p['value'] for p in points])]))
    tables.insert(0, {'title': 'Series summary', 'rows': summaries})
    return finish(context, report, sources, plots, tables,
        [] if manifest else ['Series metadata inferred from filenames; use an explicit SOC manifest for your own naming scheme'])


def joint_series(request, context):
    import numpy as np
    from impedance.models.circuits import CustomCircuit
    from .legacy.eis_joint import load_manifest, joint_fit, cross_validate_smoothness
    from .legacy.eis_io import load_eis_file
    from .legacy.eis_core import lin_kk_check
    from .views import spectrum_plots, save_spectrum
    from .research import bounded_integer
    if len(request.inputs) != 1:
        raise ValueError('Joint fitting requires one CSV manifest with file,soc columns')
    manifest, provenance = context.snapshot(request.inputs[0])
    identity = request.config.get('series_manifest_identity')
    if identity:
        if provenance['sha256'] != identity['sha256']:
            raise ValueError('Saved manifest checksum mismatch')
        provenance['path'] = identity['path']
    # Resolve relative paths against the original manifest, while reading its saved content.
    with manifest.open(encoding='utf-8-sig', newline='') as stream:
        raw_rows = list(csv.DictReader(stream))
    normalized = context.artifact('series.csv')
    if not raw_rows or not {'file', 'soc'} <= set(raw_rows[0]):
        raise ValueError('Series manifest must contain file,soc columns')
    sources, identities = [provenance], {}
    for row in raw_rows:
        original = (Path(provenance['path']).parent / row['file']).resolve()
        stored = request.config.get('series_source_map', {}).get(str(original))
        saved, source = context.snapshot(stored['snapshot_path'] if stored else original)
        if stored:
            if source['sha256'] != stored['identity']['sha256']:
                raise ValueError('Saved series spectrum checksum mismatch')
            source['path'] = str(original)
        identities[str(saved)] = str(original)
        sources.append(source)
        row['file'] = str(saved)
        row['channel'] = row.get('channel') or request.config.get('channel') or ''
    with normalized.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in raw_rows for k in row)))
        writer.writeheader()
        writer.writerows(raw_rows)
    rows = load_manifest(normalized)
    if len(rows) > 100 or len({r['soc'] for r in rows}) != len(rows) or any(not math.isfinite(r['soc']) for r in rows):
        raise ValueError('Joint fitting needs 3–100 spectra with distinct finite SOC values')
    circuit = request.config.get('circuit', 'adaptive')
    if circuit == 'adaptive' or request.config.get('candidate_circuits'):
        raise ValueError('Joint fitting requires one explicitly selected shared circuit')
    if request.config.get('parameter_overrides_by_circuit', {}).get(circuit):
        raise ValueError('Joint fitting uses per-spectrum automatic bounds; clear manual overrides first')
    budget = bounded_integer(request.config, 'max_evaluations', 10000, 10, 1_000_000)
    restarts = bounded_integer(request.config, 'restarts', 3, 1, 100)
    smoothness = float(request.config.get('joint_smoothness', 1))
    if not math.isfinite(smoothness) or smoothness < 0:
        raise ValueError('Smoothness must be finite and non-negative')
    validation = None
    if request.config.get('joint_cv_grid'):
        grid = numbers(request.config['joint_cv_grid'])
        if 0 not in grid:
            raise ValueError('Cross-validation grid must include zero regularization')
        context.progress(.1, 'Selecting smoothness with held-out SOC spectra')
        validation = cross_validate_smoothness(rows, circuit, grid, max_evaluations=budget, restarts=restarts,
            max_folds=bounded_integer(request.config, 'joint_cv_folds', 3, 1, 98), check_cancelled=context.check_cancelled)
        smoothness = validation['selected_smoothness']
    context.progress(.5, 'Fitting shared circuit across the SOC series')
    report = joint_fit(rows, circuit, smoothness=smoothness, max_evaluations=budget, restarts=restarts,
                       check_cancelled=context.check_cancelled)
    if not report['success']:
        raise ValueError('Joint fit did not converge: ' + str(report['message']))
    report['cross_validation'] = validation
    parameters, spectra, plots, artifacts = [], [], [], []
    diagnostics = ['Joint smoothing does not establish parameter identifiability or circuit reliability']
    for index, (row, fitted) in enumerate(zip(rows, report['joint_results'])):
        context.check_cancelled()
        dataset = load_eis_file(row['file'], channel=row.get('channel') or None)
        values = [p['value'] for p in fitted['parameters']]
        model = CustomCircuit(circuit, initial_guess=values)
        model.parameters_ = np.asarray(values)
        names, units = model.get_param_names()
        predicted = model.predict(dataset.frequencies)
        kk = lin_kk_check(dataset.frequencies, dataset.z)
        for parameter, name, unit in zip(fitted['parameters'], names, units):
            if parameter['name'] != name:
                raise ValueError('Parameter naming differs from the shared circuit')
            parameter['unit'] = unit
            parameters.append({'file': identities[row['file']], 'soc': row['soc'], **parameter})
        artifact = f'spectra/{index}/spectrum.npz'
        save_spectrum(context.artifact(artifact), dataset, kk, predicted)
        artifacts.append(artifact)
        spectra.append({'file': identities[row['file']], 'soc': row['soc'], 'kk_status': kk.status,
            'point_count': len(dataset.frequencies), 'channel': dataset.metadata.get('selected_channel', 'Z'),
            'relative_rmse': float(np.sqrt(np.mean((np.abs(dataset.z-predicted)/np.maximum(np.abs(dataset.z), 1e-30))**2))),
            'artifact': artifact})
        if kk.status != 'PASS':
            diagnostics.append(identities[row['file']] + ': KK ' + kk.status)
        for spec in spectrum_plots(dataset.frequencies, dataset.z, predicted, kk):
            spec['title'] = f"SOC {row['soc']:g} · " + spec['title']
            plots.append(spec)
    for collection in (report['joint_results'], report['independent_results']):
        for item in collection:
            item['file'] = identities[item['file']]
    if validation:
        for fold in validation['fold_details']:
            fold['held_out_file'] = identities[fold['held_out_file']]
    for name, unit in zip(names, units):
        points = [p for p in parameters if p['name'] == name]
        plots.insert(0, plot('SOC · ' + name, 'SOC', unit,
            [series(name, [p['soc'] for p in points], [p['value'] for p in points])]))
    tables = [{'title': 'Series parameters', 'rows': parameters}, {'title': 'Series spectra', 'rows': spectra},
              {'title': 'Independent fits', 'rows': report['independent_results']}]
    if validation:
        tables.append({'title': 'Smoothness validation', 'rows': validation['ranking']})
    payload = finish(context, report, sources, plots, tables, diagnostics)
    payload['artifacts'] += artifacts
    payload['summary'].update(circuit=circuit, smoothness=smoothness, spectrum_count=len(rows))
    return payload


def resolution_map(request, context):
    from .legacy.eis_resolution import build_low_frequency_resolution_map
    from .research import bounded_integer
    config = request.config
    minima = numbers(config.get('resolution_min_frequencies', '0.1,0.03,0.01,0.003,0.001'), minimum=1e-9)
    noise = numbers(config.get('resolution_noise_fractions', '0.005,0.01,0.02'), maximum=1)
    maximum = float(config.get('resolution_max_frequency', 2e6))
    if .01 not in minima or not math.isfinite(maximum) or maximum <= max(minima) or len(minima)*len(noise) > 200:
        raise ValueError('Resolution map requires the 0.01 Hz reference cell and a valid frequency range')
    report = build_low_frequency_resolution_map(min_frequencies=minima, noise_fractions=noise,
        replicates=bounded_integer(config, 'resolution_replicates', 10, 1, 1000), max_frequency=maximum,
        points=bounded_integer(config, 'resolution_points', 81, 21, 401), seed=int(config.get('seed', 0)),
        check_cancelled=context.check_cancelled,
        on_cell=lambda done, total: context.progress(done/total*.95, f'Resolution map {done}/{total}'))
    curves = []
    for value in noise:
        cells = sorted((c for c in report['cells'] if c['noise_fraction'] == value), key=lambda c: c['min_frequency_hz'])
        curves.append(series(f'Noise {value:g}', [c['min_frequency_hz'] for c in cells],
                             [c['slow_peak_detection_fraction'] for c in cells]))
    payload = finish(context, report, [], [plot('Synthetic resolution map', 'Minimum frequency, Hz',
        'Slow peak detection fraction', curves, xscale='log')],
        [{'title': 'Resolution cells', 'rows': report['cells']},
         {'title': 'Measurement recommendations', 'rows': report['recommendations_by_noise']}],
        ['Synthetic two-RC reference; these recommendations are not a calibration of your sample'])
    payload['summary'].update(cell_count=len(report['cells']), replicates=int(config.get('resolution_replicates', 10)))
    return payload
