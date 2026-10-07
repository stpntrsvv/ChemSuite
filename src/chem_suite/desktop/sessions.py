"""UI state adapters. Workers own bundle copying and scientific validation."""
import copy
import json
from pathlib import Path

from PySide6.QtWidgets import QComboBox


def control_value(option, control):
    if option.kind == 'integer':
        return control.value()
    if isinstance(control, QComboBox):
        if control.isEditable() and control.currentText() != control.itemText(control.currentIndex()):
            return control.currentText()
        return control.currentData()
    return control.text()


def capture_panel(panel):
    state = {'module': panel.spec.id, 'action': panel.action.currentData(), 'path': panel.path.text(),
        'controls': {name: control_value(option, control) for name, (option, control) in panel.options.items()},
        'splitter': panel.splitter.sizes(), 'result': {'$bundle': panel.result_path} if panel.result_path else None}
    if hasattr(panel, 'capture_session'):
        state.update(panel.capture_session())
    elif hasattr(panel, 'datasets'):
        state.update(selected_source=panel.selected_source, pro=panel.pro_toggle.isChecked(),
                     parameter_overrides=copy.deepcopy(panel.parameter_overrides),
                     result_tab=panel.results.currentWidget().property('source_tab') if panel.results.currentWidget() else None)
        state['datasets'] = {}
        for source, data in panel.datasets.items():
            saved = {key: copy.deepcopy(value) for key, value in data.items()
                if key != 'result' and not key.endswith('_result') and key != 'runtime_source'}
            for key in list(saved):
                if key in {'result_path', 'preview_path'} or key.endswith('_result_path'):
                    saved[key] = {'$bundle': saved[key]} if saved[key] else None
            state['datasets'][source] = saved
        state['aggregates'] = {key: {'$bundle': item['result_path']} for key, item in panel.aggregate_results.items()}
    return state


def prepare_state(state):
    # Read ready, bounded JSON payloads before touching any live controls.
    prepared = copy.deepcopy(state)
    results = {}
    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {'$bundle'}:
                raw = value['$bundle']
                results[raw] = json.loads(Path(raw).read_text(encoding='utf-8'))
                return raw
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value
    prepared = resolve(prepared)
    snapshots = snapshot_map(results.values(), results.keys())
    for panel in prepared['panels']:
        panel['_runtime_sources'] = snapshots
        for source, data in panel.get('datasets', {}).items():
            for key, raw in list(data.items()):
                if raw and (key == 'result_path' or key.endswith('_result_path')):
                    data[key.removesuffix('_path')] = results[raw]
            for key in ['fit_result_path', 'result_path', 'preview_path']:
                if data.get(key):
                    path = Path(data[key])
                    for provenance in results[str(path)]['payload']['sources']:
                        if provenance['path'] == source:
                            data['runtime_source'] = str(path.parent / provenance['snapshot'])
                            data['source_identity'] = {'path': source, 'sha256': provenance['sha256']}
                            break
                    if data.get('runtime_source'):
                        break
    return prepared, results


def apply_panel(panel, state, results):
    panel.runtime_sources = state.get('_runtime_sources', {})
    panel.action.blockSignals(True)
    for name, value in state['controls'].items():
        option, control = panel.options[name]
        control.blockSignals(True)
        if option.kind == 'integer':
            control.setValue(value)
        elif isinstance(control, QComboBox):
            index = control.findData(value)
            if index >= 0:
                control.setCurrentIndex(index)
            elif control.isEditable():
                control.setEditText(str(value))
        else:
            control.setText(str(value) if value is not None else '')
        control.blockSignals(False)
    panel.action.setCurrentIndex(panel.action.findData(state['action']))
    panel.action.blockSignals(False)
    if hasattr(panel, 'restore_session'):
        panel.restore_session(state, results)
    elif hasattr(panel, 'datasets'):
        panel.datasets = state.get('datasets', {})
        panel.aggregate_results = {key: {'result_path': raw, 'result': results[raw]} for key, raw in state.get('aggregates', {}).items()}
        panel.parameter_overrides = state.get('parameter_overrides', {})
        panel.pro_toggle.setChecked(bool(state.get('pro', False)))
        panel.refresh_datasets()
        panel.selected_source = state.get('selected_source')
        panel.path.setText(state.get('path', ''))
        if panel.selected_source in panel.source_rows:
            panel.cases_table.blockSignals(True)
            panel.cases_table.setCurrentCell(panel.source_rows[panel.selected_source], 0)
            panel.cases_table.blockSignals(False)
        panel.change_mode()
        if panel.selected_source in panel.source_rows and state['action'] not in {'series', 'joint', 'resolution'}:
            panel.select_dataset(panel.source_rows[panel.selected_source])
        raw = state.get('result')
        if raw and results[raw].get('action') == 'research':
            panel.result_path = raw
            panel.display_result(results[raw])
        for index in range(panel.results.count()):
            if panel.results.widget(index).property('source_tab') == state.get('result_tab'):
                panel.results.setCurrentIndex(index)
                break
    else:
        panel.path.setText(state.get('path', ''))
        panel.result_path = state.get('result')
        if panel.result_path:
            panel.show_result(results[panel.result_path])
    panel.splitter.setSizes(state.get('splitter', [420, 800]))
    panel.set_busy(False)
    panel.set_status('Session restored')


def attach_result(panel, path):
    result = json.loads(Path(path).read_text())
    action = result['action']
    panel.runtime_sources = {**getattr(panel, 'runtime_sources', {}), **snapshot_map([result], [path])}
    if hasattr(panel, 'attach_saved_result'):
        panel.attach_saved_result(path, result)
    elif panel.spec.id == 'eis':
        if action in {'series', 'joint', 'resolution', 'research'}:
            if action == 'research':
                # Research JSON opens as a study view without adding arbitrary UI modes.
                panel.result_path = path
                panel.display_result(result)
                panel.set_busy(False)
                return
            if action == 'joint':
                panel.options['series_manifest'][1].setText(result['payload']['sources'][0]['path'])
                panel.options['circuit'][1].setCurrentText(result['payload']['summary']['circuit'])
            panel.aggregate_results[action] = {'result': result, 'result_path': path}
            panel.action.setCurrentIndex(panel.action.findData(action))
            panel.display_aggregate()
            return
        source = result['payload']['sources'][0]
        original = source['path']
        panel.add_sources([original])
        data = panel.datasets[original]
        action = 'reliable' if action == 'import_reliable' else action
        data.update(file=Path(original).name, point_count=result['payload']['point_count'],
            kk=result['payload']['kk']['status'], channel=result['payload']['selected_channel'],
            state='Completed', runtime_source=str(Path(path).parent / source['snapshot']),
            source_identity={'path': original, 'sha256': source['sha256']},
            channels=result['payload']['metadata'].get('available_channels', []))
        if action == 'preview':
            data['preview_path'] = path
            action = 'fit'
        else:
            data.update({action+'_result': result, action+'_result_path': path, action+'_state': 'Completed'})
        panel.refresh_datasets()
        panel.action.setCurrentIndex(panel.action.findData(action))
        panel.select_dataset(panel.source_rows[original])
    else:
        panel.path.setText(result['payload']['sources'][0]['path'])
        panel.result_path = path
        panel.show_result(result)
    panel.set_status('Saved result opened')


def snapshot_map(results, paths):
    snapshots = {}
    for result, raw in zip(results, paths):
        for source in result['payload']['sources']:
            snapshots[source['path']] = {'snapshot_path': str(Path(raw).parent / source['snapshot']),
                'identity': {'path': source['path'], 'sha256': source['sha256']}}
    return snapshots
