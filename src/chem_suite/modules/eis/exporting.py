from types import SimpleNamespace
import numpy as np
from .views import spectrum_plots


def export_data(path, result, options, context):
    payload = result['payload']
    if result['action'] in {'series', 'joint', 'resolution', 'research'}:
        tables = {f'table_{index}': table['rows'] for index, table in enumerate(payload.get('tables', []))}
        specs = payload.get('plots', [])
        if result['action'] == 'joint':
            specs = [spec for spec in specs if spec['title'].startswith('SOC · ')]
            spectra = next(table['rows'] for table in payload['tables'] if table['title'] == 'Series spectra')
            for index, spectrum in enumerate(spectra):
                context.check_cancelled()
                artifact = (path.parent/spectrum['artifact']).resolve(strict=True)
                if not artifact.is_relative_to(path.parent) or spectrum['artifact'] not in payload['artifacts']:
                    raise ValueError('Joint spectrum artifact escapes its result bundle')
                with np.load(artifact, allow_pickle=False) as data:
                    frequency = data['frequency_hz']
                    measured = data['z_real_ohm'] + 1j*data['z_imag_ohm']
                    predicted = data['fit_real_ohm'] + 1j*data['fit_imag_ohm']
                    kk = None
                    if 'kk_frequency_hz' in data:
                        kk = SimpleNamespace(success=True, frequencies=data['kk_frequency_hz'],
                            z_fit=data['kk_real_ohm']+1j*data['kk_imag_ohm'],
                            residual_real=data['kk_residual_real'], residual_imag=data['kk_residual_imag'])
                    full = spectrum_plots(frequency, measured, predicted, kk, limit=len(frequency))
                    for spec in full:
                        spec['title'] = f"SOC {spectrum['soc']:g} · " + spec['title']
                    specs.extend(full)
                    tables[f'joint_spectrum_{index}'] = [{'frequency_hz': float(f), 'soc': spectrum['soc'],
                        'z_real_ohm': float(z.real), 'z_imag_ohm': float(z.imag),
                        'z_magnitude_ohm': float(abs(z)), 'phase_deg': float(np.angle(z, deg=True)),
                        'fit_real_ohm': float(p.real), 'fit_imag_ohm': float(p.imag),
                        'fit_magnitude_ohm': float(abs(p)), 'fit_phase_deg': float(np.angle(p, deg=True))}
                        for f, z, p in zip(frequency, measured, predicted)]
        # Each study plot gets a separate file; a large SOC series must not create a metre-high figure.
        groups = {f'study_{index:03d}': [spec] for index, spec in enumerate(specs)}
        if not options.get('study_plots', True):
            groups = {}
        return payload['summary'], tables, groups
    is_drt = result['action'] == 'drt' and bool(payload.get('drt', {}).get('fit', {}).get('success'))
    if not is_drt and (result['action'] not in {'fit', 'reliable', 'statistics', 'import_reliable'} or not payload.get('best', {}).get('success')):
        raise ValueError('Only successful EIS fits can be exported')
    with np.load(path.parent / 'spectrum.npz', allow_pickle=False) as data:
        f = data['frequency_hz']
        z = data['z_real_ohm'] + 1j * data['z_imag_ohm']
        fitted = data['fit_real_ohm'] + 1j * data['fit_imag_ohm']
        kk = None
        if 'kk_frequency_hz' in data:
            kk = SimpleNamespace(success=True, frequencies=data['kk_frequency_hz'],
                                 z_fit=data['kk_real_ohm'] + 1j * data['kk_imag_ohm'],
                                 residual_real=data['kk_residual_real'], residual_imag=data['kk_residual_imag'])
        specs = spectrum_plots(f, z, fitted, kk, limit=len(f))
        spectrum = []
        for index in range(len(f)):
            if index % 1000 == 0:
                context.check_cancelled()
            spectrum.append({
                'frequency_hz': float(f[index]), 'z_real_ohm': float(z[index].real),
                'z_imag_ohm': float(z[index].imag), 'z_magnitude_ohm': float(abs(z[index])),
                'phase_deg': float(np.angle(z[index], deg=True)),
                'fit_real_ohm': float(fitted[index].real), 'fit_imag_ohm': float(fitted[index].imag),
                'fit_magnitude_ohm': float(abs(fitted[index])), 'fit_phase_deg': float(np.angle(fitted[index], deg=True)),
            })
    if is_drt:
        from .advanced import drt_plots
        drt = payload['drt']
        specs += drt_plots(drt, limit=len(drt['fit']['tau_seconds']))
        summary = {'source_file': payload['sources'][0]['path'], **payload['summary']}
        tables = {'spectrum': spectrum, 'drt_distribution': [
            {'tau_seconds': tau, 'gamma_ohm': gamma} for tau, gamma in zip(drt['fit']['tau_seconds'], drt['fit']['gamma_ohm'])],
            'drt_peaks': drt['fit']['peaks'], 'drt_regularization': drt['selection']['ranking'], 'kk': [payload['kk']]}
        if drt.get('stability'):
            tables['drt_stability'] = drt['stability']['reference_peaks']
        return summary, tables, group_plots(specs)
    parameters = [{
        'parameter': row['name'], 'unit': row.get('unit', ''), 'value': row['value'], 'standard_error_1sigma': row['confidence'],
        'relative_error_percent': row['relative_error_percent'],
    } for row in payload['best']['parameters']]
    fits = [{key: value for key, value in fit.items() if key not in {'parameters', 'plots'}} for fit in payload['fits']]
    parser = [{'key': key, 'value': str(value)} for key, value in payload.get('metadata', {}).items()]
    if payload.get('drt'):
        from .advanced import drt_plots
        specs += drt_plots(payload['drt'], limit=len(payload['drt']['fit']['tau_seconds']))
    specs += [p for p in payload.get('plots', []) if p['title'] == 'Profile likelihood']
    groups = group_plots(specs)
    summary = {
        'source_file': payload['sources'][0]['path'], 'circuit': payload['best']['circuit'],
        'point_count': payload['point_count'], 'mean_fit_error_percent': payload['best']['mean_fit_error_percent'],
        'model_status': payload['best']['status'], 'kk_status': payload['kk']['status'],
        'selected_channel': payload['selected_channel'],
    }
    tables = {'spectrum': spectrum, 'parameters': parameters, 'models': fits, 'parser': parser, 'kk': [payload['kk']]}
    if payload.get('inference'):
        inference = payload['inference']
        summary.update(verdict=inference['decision']['verdict'], recommended_family=inference['decision'].get('recommended_family'),
                       recommended_topology=inference['decision'].get('recommended_topology'))
        if options.get('reliability_tables', True):
            tables['decision'] = [{'field': key, 'value': value} for key, value in inference['decision'].items()]
            tables['topology_stability'] = (inference.get('topology_bootstrap') or {}).get('ranking', [])
            tables['family_stability'] = (inference.get('topology_bootstrap') or {}).get('family_ranking', [])
            tables['topology_samples'] = (inference.get('topology_bootstrap') or {}).get('samples', [])
            if inference.get('drt', {}).get('stability'):
                tables['drt_stability'] = inference['drt']['stability']['reference_peaks']
    if payload.get('statistics') and options.get('statistics_tables', True):
        from .presentation import statistics_tables
        keys = {'Parameter intervals': 'parameter_intervals', 'Bootstrap summary': 'bootstrap_summary',
                'Profile summary': 'profile_summary', 'Profile points': 'profile_points', 'Window stability': 'window_stability',
                'Frequency windows': 'frequency_windows', 'Characteristic frequency support': 'characteristic_support'}
        tables.update({keys[name]: rows for name, rows in statistics_tables(payload['statistics']).items()})
        summary.update(bootstrap_method=payload['statistics']['bootstrap']['method'], bootstrap_accepted=payload['statistics']['bootstrap']['accepted'])
    return summary, tables, groups


def group_plots(specs):
    return {key: [spec for spec in specs if spec['title'] in titles] for key, titles in {
        'nyquist': ('Nyquist',), 'bode': ('Bode magnitude', 'Bode phase'),
        'residuals': ('Fit residuals', 'Relative residuals'), 'kk_plot': ('KK Nyquist', 'KK residuals'),
        'drt_plot': ('DRT distribution',),
        'profile_plot': ('Profile likelihood',),
    }.items()}


def export_extras(folder, result_path, result, config, context):
    if result['payload'].get('inference') and config.get('outputs', {}).get('reliability_json', True):
        from chem_suite.core.artifacts import write_json
        write_json(folder / 'inference.json', result['payload']['inference'])
    if config.get('outputs', {}).get('spice', False):
        from .advanced import make_spice_package
        make_spice_package(result_path, folder / 'spice', config.get('module_config', {}), context)

    if config.get('outputs', {}).get('controller', False):
        from .controller import make_controller_package
        make_controller_package(result_path, folder / 'controller', config.get('module_config', {}), context)
