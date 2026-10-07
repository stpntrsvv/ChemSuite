import csv
import copy
import json
import re
from importlib import import_module
from pathlib import Path

from chem_suite.core.artifacts import write_json


def write_csv(path, rows, columns=None):
    columns = columns or (list(rows[0]) if rows else [])
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: '; '.join(str(v) for v in value) if isinstance(value, list) else value
                             for key, value in row.items()})


def copy_file(source, target, context):
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, target.open('wb') as outgoing:
        while chunk := incoming.read(1024 * 1024):
            context.check_cancelled()
            outgoing.write(chunk)


def render_plots(folder, groups, formats, language, context):
    from matplotlib.figure import Figure
    from chem_suite.locale import text
    for name, specs in groups.items():
        context.check_cancelled()
        figure = Figure(figsize=(7, 4 * len(specs)), layout='constrained')
        axes = figure.subplots(len(specs), 1, squeeze=False)
        for row, spec in enumerate(specs):
            axis = axes[row, 0]
            palette = ('#087f8c', '#d27b2c', '#42885b', '#87569c', '#b74e55')
            for index, curve in enumerate(spec['series']):
                color = palette[index % len(palette)]
                if curve['kind'] == 'scatter':
                    axis.scatter(curve['x'], curve['y'], s=12, color=color, label=text(curve['label'], language))
                else:
                    axis.plot(curve['x'], curve['y'], color=color, label=text(curve['label'], language))
            axis.set(title=text(spec['title'], language), xlabel=text(spec['xlabel'], language),
                     ylabel=text(spec['ylabel'], language), xscale=spec['xscale'], yscale=spec['yscale'])
            if spec.get('equal_aspect'):
                axis.set_aspect('equal', adjustable='box')
            axis.grid(True, alpha=.2)
            if spec['series']:
                axis.legend(fontsize=8)
        for extension in formats:
            context.check_cancelled()
            if extension not in {'png', 'svg', 'pdf'}:
                raise ValueError('Unsupported plot format')
            figure.savefig(folder / f'{name}.{extension}', dpi=180)
        figure.clear()


def export_results(request, context):
    if not request.inputs:
        raise ValueError('No successful results to export')
    root = context.publication(request.config['destination'])
    options = request.config.get('outputs', {})
    language = request.config.get('language', 'en')
    summaries, sheets, manifest = [], [], {'schema_version': 1, 'language': language,
        'outputs': options, 'formats': request.config.get('formats', ['png']), 'experiments': [], 'files': []}
    handlers = {'eis': 'chem_suite.modules.eis.exporting', 'cycling': 'chem_suite.modules.cycling.exporting'}
    for index, raw in enumerate(request.inputs):
        context.progress(index / len(request.inputs), f'Exporting result {index + 1}/{len(request.inputs)}')
        result_path = Path(raw).resolve(strict=True)
        result = json.loads(result_path.read_text())
        if result.get('schema_version') != 1 or result['module'] not in handlers:
            raise ValueError('Unsupported analysis result')
        payload = result['payload']
        source_name = Path(payload['sources'][0]['path']).stem if payload.get('sources') else payload['summary'].get('analysis', result['action'])
        if result['module'] == 'cycling':
            source_name = payload.get('experiment', {}).get('name', source_name)
        label = f'{index + 1:03d}_' + re.sub(r'[^\w.-]+', '_', source_name)[:80]
        folder = root / label
        folder.mkdir()
        module = import_module(handlers[result['module']])
        adapter_options = {**options, '_module_config': request.config.get('module_config', {})} if result['module'] == 'cycling' else options
        summary, tables, groups = module.export_data(result_path, result, adapter_options, context)
        summaries.append({'experiment': label, 'module': result['module'], **summary})
        for name, rows in tables.items():
            if options.get(name, True):
                write_csv(folder / f'{name}.csv', rows)
            if options.get('excel', True):
                sheets.append((f'{index + 1:03d} {name}'[:31], rows))
        exported = copy.deepcopy(result)
        if options.get('source', True):
            for source_index, provenance in enumerate(payload['sources']):
                source = (result_path.parent / provenance['snapshot']).resolve(strict=True)
                if not source.is_relative_to(result_path.parent):
                    raise ValueError('Source snapshot escapes the result directory')
                target = folder / 'sources' / str(source_index + 1) / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                copy_file(source, target, context)
                exported['payload']['sources'][source_index]['snapshot'] = str(target.relative_to(folder))
        else:
            for source in exported['payload']['sources']:
                source['snapshot'] = None
            exported['payload']['source_snapshots_included'] = False
        if options.get('json', True):
            for artifact in payload.get('artifacts', []):
                source = (result_path.parent / artifact).resolve(strict=True)
                if not source.is_relative_to(result_path.parent):
                    raise ValueError('Artifact escapes the result directory')
                target = (folder / artifact).resolve()
                if not target.is_relative_to(folder):
                    raise ValueError('Artifact destination escapes the export directory')
                copy_file(source, target, context)
            write_json(folder / 'result.json', exported)
        if options.get('report', True):
            from chem_suite.locale import text
            lines = [text('Analysis report', language), source_name]
            lines.extend(f'{text(key, language)}: {value}' for key, value in summary.items())
            lines.extend(str(flag) for flag in payload.get('diagnostics', []))
            if result['module'] == 'eis' and payload.get('best'):
                lines.append(text('Parameter errors are local standard errors (1σ) from the fit covariance.', language))
            if result['action'] == 'drt':
                lines.append(text(payload['drt']['interpretation_warning'], language))
            if payload.get('inference'):
                from chem_suite.modules.eis.presentation import decision_text
                lines.append(decision_text(payload['inference']['decision'], language))
            (folder / 'report.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        if hasattr(module, 'export_extras'):
            module.export_extras(folder, result_path, result, request.config, context)
        selected = {name: specs for name, specs in groups.items() if specs and options.get(name, True)}
        formats = request.config.get('formats', ['png'])
        if formats and selected:
            render_plots(folder, selected, formats, language, context)
        manifest['experiments'].append({
            'directory': label, 'result_source': str(result_path), 'sources': payload['sources'],
            'module_version': result['module_version'], 'request': result['request'],
        })
    if options.get('summary', True):
        columns = list(dict.fromkeys(key for row in summaries for key in row))
        write_csv(root / 'summary.csv', summaries, columns)
    if options.get('excel', True):
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        book = Workbook(write_only=True)
        for name, rows in [('Summary', summaries), *sheets]:
            if len(rows) > 1_048_575:
                raise ValueError('Too many rows for an Excel sheet; select CSV export')
            sheet = book.create_sheet(name)
            columns = list(dict.fromkeys(key for row in rows for key in row))
            sheet.append(columns)
            for row_index, row in enumerate(rows):
                if row_index % 1000 == 0:
                    context.check_cancelled()
                cells = []
                for key in columns:
                    value = row.get(key)
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False)
                    cell = WriteOnlyCell(sheet, value=value)
                    if isinstance(value, str):
                        cell.data_type = 's'
                    cells.append(cell)
                sheet.append(cells)
        book.save(root / 'results.xlsx')
    manifest['files'] = sorted(str(path.relative_to(root)) for path in root.rglob('*') if path.is_file())
    write_json(root / 'manifest.json', manifest)
    context.check_cancelled()
    return {'destination': request.config['destination'], 'experiment_count': len(summaries)}
