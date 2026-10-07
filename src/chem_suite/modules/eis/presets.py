"""Workspace-scoped named configurations; all disk access runs in a worker."""
import copy
import json
import math

from chem_suite.core.artifacts import write_json

KEYS = {'circuit', 'candidate_circuits', 'channel', 'restarts', 'max_evaluations', 'seed',
        'parameter_overrides_by_circuit', 'drt_lambda_grid', 'drt_tau_points', 'drt_folds', 'drt_stability_samples',
        'reliability_samples', 'reliability_drt_samples', 'reliability_restarts', 'statistics_samples',
        'statistics_method', 'statistics_noise_fraction', 'profile_parameter', 'profile_points',
        'profile_span_decades', 'window_check', 'joint_smoothness', 'joint_cv_grid', 'joint_cv_folds',
        'resolution_min_frequencies', 'resolution_noise_fractions', 'resolution_max_frequency', 'resolution_replicates', 'resolution_points'}
ACTIONS = {'fit', 'drt', 'reliable', 'statistics', 'series', 'joint', 'resolution'}


def validate_preset(preset):
    from .advanced import validate_overrides
    from impedance.models.circuits import CustomCircuit
    from .legacy.eis_core import build_bounds_and_guess, DatasetScale
    if not isinstance(preset, dict) or set(preset) != {'action', 'config'} or preset['action'] not in ACTIONS:
        raise ValueError('Invalid preset schema')
    config = preset['config']
    if not isinstance(config, dict) or set(config) - KEYS:
        raise ValueError('Invalid preset settings')
    validate_overrides(config.get('parameter_overrides_by_circuit', {}))
    circuits = [config.get('circuit', 'adaptive')]
    candidates = config.get('candidate_circuits', '')
    if not isinstance(candidates, str):
        raise ValueError('Invalid candidate circuit list')
    circuits += [c.strip() for c in candidates.split(';') if c.strip()]
    if len(circuits) > 41:
        raise ValueError('Invalid candidate circuit list')
    circuits += list(config.get('parameter_overrides_by_circuit', {}))
    for circuit in circuits:
        if not isinstance(circuit, str) or len(circuit) > 500:
            raise ValueError('Invalid circuit in preset')
        if circuit != 'adaptive':
            _, _, guess = build_bounds_and_guess(circuit, DatasetScale(1, 10, .001))
            CustomCircuit(circuit, initial_guess=guess)
    limits = {'restarts': (1, 100), 'max_evaluations': (10, 1_000_000), 'seed': (0, 2_147_483_647),
        'drt_tau_points': (21, 401), 'drt_folds': (2, 10), 'drt_stability_samples': (0, 200),
        'reliability_samples': (1, 1000), 'reliability_drt_samples': (1, 200), 'reliability_restarts': (1, 100),
        'statistics_samples': (1, 1000), 'profile_points': (5, 201), 'joint_cv_folds': (1, 98),
        'resolution_replicates': (1, 1000), 'resolution_points': (21, 401)}
    for key, (low, high) in limits.items():
        if key in config and (isinstance(config[key], bool) or not isinstance(config[key], int) or not low <= config[key] <= high):
            raise ValueError('Invalid preset value: ' + key)
    for key, low, high in [('statistics_noise_fraction', 0, 1), ('profile_span_decades', .000001, 6), ('joint_smoothness', 0, 1e12), ('resolution_max_frequency', 1e-9, 1e15)]:
        if key in config:
            value = float(config[key])
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError('Invalid preset value: ' + key)
    if config.get('statistics_method', 'residual') not in ('residual', 'parametric') or config.get('window_check', 'on') not in ('on', 'off'):
        raise ValueError('Invalid preset diagnostic method')
    for key in ('channel', 'profile_parameter', 'drt_lambda_grid'):
        if key in config and (not isinstance(config[key], str) or len(config[key]) > 500):
            raise ValueError('Invalid preset value: ' + key)
    from .workflows import numbers
    for key in ('resolution_min_frequencies', 'resolution_noise_fractions', 'joint_cv_grid'):
        if config.get(key):
            numbers(config[key], minimum=1e-9 if key == 'resolution_min_frequencies' else 0, maximum=1 if key == 'resolution_noise_fractions' else math.inf)
    return copy.deepcopy(preset)


def manage_presets(request, context):
    if request.inputs:
        raise ValueError('Presets are scoped to this workspace')
    path = context.directory.parent.parent / 'eis-presets.json'
    if path.exists():
        document = json.loads(path.read_text())
        if document.get('schema_version') != 1 or not isinstance(document.get('presets'), dict):
            raise ValueError('Invalid preset store')
    else:
        document = {'schema_version': 1, 'presets': {}}
    presets = document['presets']
    if len(presets) > 64:
        raise ValueError('Too many presets')
    for name, preset in presets.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError('Invalid preset name')
        validate_preset(preset)
    operation = request.config.get('operation', 'list')
    name = str(request.config.get('name', '')).strip()
    if operation != 'list' and (not name or len(name) > 128):
        raise ValueError('Preset name must contain 1–128 characters')
    selected = None
    if operation == 'save':
        if name in presets and not request.config.get('replace', False):
            raise ValueError('A preset with this name already exists')
        if name not in presets and len(presets) >= 64:
            raise ValueError('Too many presets')
        presets[name] = validate_preset(request.config['preset'])
    elif operation == 'load':
        selected = validate_preset(presets[name])
    elif operation == 'delete':
        del presets[name]
    elif operation != 'list':
        raise ValueError('Unknown preset operation')
    if operation in ('save', 'delete'):
        context.check_cancelled()
        write_json(path, document)
    return {'presets': sorted(presets), 'selected': selected, 'operation': operation, 'name': name}
