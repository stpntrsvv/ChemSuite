"""Allowlisted original research procedures, supervised by the platform job service."""
import importlib
import inspect
import json
from pathlib import Path

from chem_suite.core.artifacts import write_json
from chem_suite.exports.actions import copy_file

# Function names are platform declarations, never executable names read from a report.
CATALOG = {
    'synthetic': ('eis_synthetic', 'generate_corpus', 'generate'),
    'interval-corpus': ('eis_interval_corpus', 'generate_interval_corpus', 'generate'),
    'window-corpus': ('eis_window_replication', 'generate_window_replication_corpus', 'generate'),
    'density-corpus': ('eis_grid_density', 'generate_density_corpus', 'generate'),
    'diffusion-corpus': ('eis_diffusion_map', 'generate_observability_corpus', 'generate'),
    'negative-controls': ('eis_diffusion_map', 'generate_diverse_negative_controls', 'generate'),
    'guardband-corpus': ('eis_wo_guardband', 'generate_wo_guardband_corpus', 'generate'),
    'family-benchmark': ('eis_family_benchmark', 'run_benchmark', 'truth'),
    'interval-benchmark': ('eis_interval_benchmark', 'run_interval_benchmark', 'truth'),
    'window-benchmark': ('eis_window_benchmark', 'run_window_benchmark', 'window'),
    'window-replication': ('eis_window_replication', 'run_window_replication', 'truth'),
    'density-benchmark': ('eis_grid_density', 'run_density_benchmark', 'truth'),
    'guardband-benchmark': ('eis_wo_guardband', 'run_wo_guardband_benchmark', 'truth'),
    'spice-benchmark': ('eis_spice_benchmark', 'run_benchmark', 'manifest'),
    'inference-batch': ('eis_inference_batch', 'run_batch', 'batch'),
    'gate-regression': ('eis_gate_regression', 'run_gate_regression', 'gate'),
}


def run_research(request, context):
    task = request.config.get('procedure')
    if task not in CATALOG:
        raise ValueError('Unknown research procedure; choose: ' + ', '.join(CATALOG))
    module, name, mode = CATALOG[task]
    function = getattr(importlib.import_module('.legacy.' + module, __package__), name)
    options = request.config.get('settings', {})
    if not isinstance(options, dict):
        raise ValueError('Research settings must be an object')
    signature = inspect.signature(function)
    allowed = {key for key, p in signature.parameters.items() if p.kind == p.KEYWORD_ONLY}
    if set(options) - allowed:
        raise ValueError('Unsupported research settings: ' + ', '.join(sorted(set(options)-allowed)))
    options = dict(options)
    output = context.artifact('research-output')
    output.mkdir()
    report_file = output / 'results.jsonl'
    sources = []
    def snapshot(raw, target=None):
        saved, provenance = context.snapshot(raw)
        sources.append(provenance)
        if target:
            copy_file(saved, target, context)
            return target
        return saved
    args = []
    if mode == 'generate':
        if request.inputs:
            raise ValueError('Corpus generation does not take source spectra')
        args = [output]
    elif mode in {'truth', 'window'}:
        expected = 2 if mode == 'window' else 1
        if len(request.inputs) != expected:
            raise ValueError('Provide a truth.jsonl' + (' and interval results.jsonl' if expected == 2 else ''))
        truth = snapshot(request.inputs[0], context.artifact('corpus/truth.jsonl'))
        records = [json.loads(line) for line in truth.read_text().splitlines() if line.strip()]
        if not records:
            raise ValueError('Truth corpus is empty')
        for row in records:
            name = row['file_name']
            if not isinstance(name, str) or Path(name).name != name or name in {'.', '..'}:
                raise ValueError('Spectrum filename escapes the corpus')
            snapshot(Path(request.inputs[0]).resolve().parent / 'spectra' / name,
                     context.artifact('corpus/spectra/' + name))
        args = [truth]
        if mode == 'window':
            args.append(snapshot(request.inputs[1]))
        args.append(report_file)
    elif mode == 'manifest':
        if len(request.inputs) != 1:
            raise ValueError('SPICE benchmark needs one frozen manifest')
        args = [snapshot(request.inputs[0])]
        options.setdefault('ngspice_executable', None)
    else:
        from .legacy.eis_pipeline import discover_input_files
        if mode == 'gate':
            if len(request.inputs) < 2:
                raise ValueError('Provide spectra and a directory of bootstrap reports')
            originals = discover_input_files(request.inputs[:-1], recursive=True)
            directory = context.artifact('bootstrap')
            directory.mkdir()
            saved = []
            for index, original in enumerate(originals):
                path = snapshot(original, context.artifact(f'research-input/{index}/' + Path(original).name))
                snapshot(Path(request.inputs[-1]) / (Path(original).name+'.json'), directory/(path.name+'.json'))
                saved.append(path)
            args = [saved, directory, report_file]
        else:
            originals = discover_input_files(request.inputs, recursive=bool(options.get('recursive', False)))
            saved = [str(snapshot(p)) for p in originals]
            args = [saved, report_file]
        if not originals:
            raise ValueError('No research input spectra found')
    context.progress(.1, 'Running research procedure: ' + task)
    report = function(*args, **options)
    context.check_cancelled()
    if isinstance(report, Path):
        report = {'truth_manifest': str(report.relative_to(context.directory))}
    write_json(output / 'summary.json', report)
    artifacts = [str(path.relative_to(context.directory)) for path in output.rglob('*') if path.is_file()]
    if request.config.get('destination'):
        root = context.publication(request.config['destination'])
        for relative in artifacts:
            source = context.directory / relative
            copy_file(source, root / source.relative_to(output), context)
        write_json(root / 'provenance.json', {'sources': sources, 'procedure': task, 'settings': options})
    return {'dataset_type': 'eis.research.v1', 'sources': sources, 'artifacts': artifacts,
            'summary': {'analysis': task, 'artifact_count': len(artifacts)}, 'analysis': report,
            'plots': [], 'tables': [], 'diagnostics': ['Research calibration results do not change production inference thresholds']}
