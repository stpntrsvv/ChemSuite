"""Versioned, portable bundles. Copies and checksum validation run outside Qt."""
import copy
import hashlib
import json
import math
from pathlib import Path

from chem_suite.core.artifacts import write_json
from chem_suite.core.plots import validate_plot
from chem_suite.exports.actions import copy_file

RESULT_ACTIONS = {'eis': {'fit', 'reliable', 'statistics', 'import_reliable', 'drt', 'series', 'joint', 'resolution', 'research', 'preview'},
                  'cycling': {'analyze'}}


def digest(path, context):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024*1024):
            context.check_cancelled()
            result.update(chunk)
    return result.hexdigest()


def contained(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Bundle reference must be relative')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Bundle reference escapes its directory')
    return path


def validate_result(result):
    if not isinstance(result, dict) or result.get('schema_version') != 1 or result.get('action') not in RESULT_ACTIONS.get(result.get('module'), set()):
        raise ValueError('Unsupported scientific result')
    payload = result.get('payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('sources'), list):
        raise ValueError('Invalid scientific result payload')
    for spec in payload.get('plots', []):
        validate_plot(spec)
    if result['module'] == 'eis' and result['action'] in {'fit', 'reliable', 'statistics', 'import_reliable'}:
        if not payload.get('best', {}).get('success'):
            raise ValueError('Saved fit did not converge')
    for source in payload['sources']:
        if not isinstance(source.get('sha256'), str) or len(source['sha256']) != 64 or not source.get('snapshot'):
            raise ValueError('Source snapshots and provenance are required to reopen a result')
    if not isinstance(payload.get('artifacts', []), list):
        raise ValueError('Invalid artifact list')
    if result['module'] == 'eis' and result['action'] == 'joint':
        spectra = next((table['rows'] for table in payload.get('tables', []) if table['title'] == 'Series spectra'), None)
        if not spectra or any(row.get('artifact') not in payload['artifacts'] for row in spectra):
            raise ValueError('Joint spectrum references are missing from the bundle')


def copy_bundle(raw, destination, context):
    path = Path(raw).resolve(strict=True)
    root = path.parent
    before = digest(path, context)
    result = json.loads(path.read_text(encoding='utf-8'))
    if path.name == 'preview.json' and set(result) == {'payload'} and path.parent.parent.name == 'loaded':
        root = path.parents[2]
        result.update(schema_version=1, module='eis', module_version='0.1.0', action='preview')
        result['payload']['artifacts'] = [str((path.parent/'spectrum.npz').relative_to(root))]
    validate_result(result)
    destination.mkdir(parents=True, exist_ok=False)
    for source in result['payload']['sources']:
        file = contained(root, source['snapshot'])
        if digest(file, context) != source['sha256'].lower():
            raise ValueError('Source checksum differs from provenance: ' + source['path'])
        target = destination / source['snapshot']
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_file(file, target, context)
        if digest(target, context) != source['sha256'].lower():
            raise ValueError('Source changed while copying the bundle')
    for artifact in result['payload'].get('artifacts', []):
        file = contained(root, artifact)
        target = destination / artifact
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Artifact destination escapes the bundle')
        copy_file(file, target, context)
    if digest(path, context) != before:
        raise ValueError('Scientific result changed while importing')
    write_json(destination / 'result.json', result)
    return destination / 'result.json'


def walk(value, replace):
    if isinstance(value, dict):
        if set(value) == {'$bundle'}:
            return {'$bundle': replace(value['$bundle'])}
        return {key: walk(item, replace) for key, item in value.items()}
    if isinstance(value, list):
        return [walk(item, replace) for item in value]
    return value


def validate_state(state):
    if not isinstance(state, dict) or state.get('language') not in {'ru', 'en'} or not isinstance(state.get('panels'), list):
        raise ValueError('Invalid session state')
    modules = [panel.get('module') for panel in state['panels'] if isinstance(panel, dict)]
    if len(modules) != len(state['panels']) or len(set(modules)) != len(modules) or any(m not in RESULT_ACTIONS for m in modules):
        raise ValueError('Unsupported session module')
    from chem_suite.bootstrap import builtins
    registry = builtins()
    for panel in state['panels']:
        spec = registry.module(panel['module'])
        declared = {option.id: option for option in spec.options}
        if set(panel.get('controls', {})) - set(declared):
            raise ValueError('Unsupported session control')
        for name, value in panel.get('controls', {}).items():
            option = declared[name]
            if option.kind == 'choice' and name != 'circuit' and value not in option.choices:
                raise ValueError('Unsupported choice session control')
            if option.kind == 'integer':
                if isinstance(value, bool) or not isinstance(value, int) or not option.minimum <= value <= option.maximum:
                    raise ValueError('Invalid integer session control')
            elif value is not None and not isinstance(value, str):
                raise ValueError('Invalid text session control')
        if panel.get('action') not in RESULT_ACTIONS[panel['module']] - {'preview', 'import_reliable', 'research'}:
            raise ValueError('Unsupported session action')
        if not isinstance(panel.get('controls'), dict) or not isinstance(panel.get('datasets', {}), dict):
            raise ValueError('Invalid session controls or datasets')
        if not isinstance(panel.get('path', ''), str) or panel.get('selected_source') not in {None, *panel.get('datasets', {}).keys()}:
            raise ValueError('Invalid selected source in session')
        if any(not isinstance(data, dict) or not isinstance(data.get('state'), str) or not isinstance(data.get('file'), str) for data in panel.get('datasets', {}).values()):
            raise ValueError('Invalid session dataset')
        if panel['module'] == 'cycling':
            for data in panel.get('datasets', {}).values():
                if not isinstance(data.get('inputs', []), list) or any(not isinstance(v, str) for v in data.get('inputs', [])):
                    raise ValueError('Invalid cycling experiment inputs')
                if data.get('view'):
                    view = data['view']
                    if not isinstance(view, dict) or not isinstance(view.get('plots'), list) or len(view['plots']) > 10:
                        raise ValueError('Invalid cycling view')
                    for plot_spec in view['plots']:
                        validate_plot(plot_spec)
        if set(panel.get('aggregates', {})) - {'series', 'joint', 'resolution'}:
            raise ValueError('Unsupported aggregate session result')
        for value in [panel.get('result'), *panel.get('aggregates', {}).values(),
                      *(value for data in panel.get('datasets', {}).values() for key, value in data.items()
                        if key in {'result_path', 'preview_path'} or key.endswith('_result_path'))]:
            if value is not None and (not isinstance(value, dict) or set(value) != {'$bundle'} or not isinstance(value['$bundle'], str)):
                raise ValueError('Invalid session result reference')
        sizes = panel.get('splitter', [420, 800])
        if not isinstance(sizes, list) or len(sizes) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 20000 for v in sizes):
            raise ValueError('Invalid session splitter sizes')
        if not isinstance(panel.get('pro', False), bool) or panel.get('result_tab') is not None and not isinstance(panel['result_tab'], str):
            raise ValueError('Invalid session view state')
        overrides = panel.get('parameter_overrides', {})
        if not isinstance(overrides, dict):
            raise ValueError('Invalid session parameter overrides')
        for circuit, parameters in overrides.items():
            if not isinstance(circuit, str) or not isinstance(parameters, dict):
                raise ValueError('Invalid session circuit overrides')
            for name, bounds in parameters.items():
                if not isinstance(name, str) or not isinstance(bounds, dict) or set(bounds) - {'initial', 'lower', 'upper'}:
                    raise ValueError('Invalid session parameter bounds')
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds.values()):
                    raise ValueError('Invalid numerical session bounds')
        for data in panel.get('datasets', {}).values():
            views = data.get('view_circuits', {})
            if not isinstance(views, dict) or any(key not in {'fit', 'drt', 'reliable', 'statistics'} or not isinstance(value, str) for key, value in views.items()):
                raise ValueError('Invalid viewed circuit in session')
    size = state.get('size', [1280, 820])
    if not isinstance(size, list) or len(size) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 200 <= v <= 20000 for v in size):
        raise ValueError('Invalid session window size')
    if state.get('active_module') not in modules:
        raise ValueError('Invalid active session module')


def save_session(request, context):
    state = copy.deepcopy(request.config['state'])
    validate_state(state)
    root = context.publication(request.config['destination'])
    bundles = {}
    def save(raw):
        raw = str(Path(raw).resolve(strict=True))
        if raw not in bundles:
            context.progress(.1, 'Saving scientific result bundles')
            target = root / 'bundles' / str(len(bundles))
            saved = copy_bundle(raw, target, context)
            bundles[raw] = str(saved.relative_to(root))
        return bundles[raw]
    state = walk(state, save)
    files = {str(path.relative_to(root)): digest(path, context) for path in root.rglob('*') if path.is_file()}
    write_json(root / 'session.json', {'schema_version': 1, 'kind': 'chem-suite-session', 'state': state, 'files': files})
    context.check_cancelled()
    return {'destination': request.config['destination'], 'bundle_count': len(bundles)}


def restore_session(request, context):
    if len(request.inputs) != 1:
        raise ValueError('Select one session.json')
    manifest, provenance = context.snapshot(request.inputs[0])
    document = json.loads(manifest.read_text(encoding='utf-8'))
    if document.get('schema_version') != 1 or document.get('kind') != 'chem-suite-session':
        raise ValueError('Unsupported session format')
    state = document['state']
    validate_state(state)
    root = Path(request.inputs[0]).resolve().parent
    files = document.get('files')
    if not isinstance(files, dict):
        raise ValueError('Session checksums are missing')
    checksums = {}
    # Verify every declared file before exposing any restored state.
    for index, (relative, expected) in enumerate(files.items()):
        context.progress(index/max(1, len(files))*.5, 'Verifying session checksums')
        file = contained(root, relative)
        if digest(file, context) != expected:
            raise ValueError('Session checksum mismatch: ' + relative)
        checksums[relative] = expected
    bundles = {}
    def restore(relative):
        path = contained(root, relative)
        if relative not in checksums:
            raise ValueError('Result is missing from the session checksums')
        if relative not in bundles:
            expected_result = checksums[relative]
            result = json.loads(path.read_text())
            validate_result(result)
            for item in [s['snapshot'] for s in result['payload']['sources']] + result['payload'].get('artifacts', []):
                full = str(contained(path.parent, item).relative_to(root))
                if full not in checksums:
                    raise ValueError('Artifact is missing from session checksums')
            target = context.artifact('restored/' + str(len(bundles)))
            saved = copy_bundle(path, target, context)
            if digest(path, context) != expected_result:
                raise ValueError('Session result changed during import')
            # Check the copied files too; the source session could change while importing.
            for item in saved.parent.rglob('*'):
                if item.is_file():
                    original = str((path.parent/item.relative_to(saved.parent)).relative_to(root))
                    if item.name != 'result.json' and digest(item, context) != checksums[original]:
                        raise ValueError('Session changed during import')
            bundles[relative] = str(saved)
        return bundles[relative]
    state = walk(state, restore)
    context.check_cancelled()
    return {'state': state, 'sources': [provenance], 'bundle_count': len(bundles)}


def open_result(request, context):
    if len(request.inputs) != 1:
        raise ValueError('Select one result.json')
    path = copy_bundle(request.inputs[0], context.artifact('restored/result'), context)
    result = json.loads(path.read_text())
    return {'module': result['module'], 'action': result['action'], 'result_path': str(path)}
