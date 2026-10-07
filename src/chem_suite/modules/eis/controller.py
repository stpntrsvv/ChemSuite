"""Validated controller packages, generated only from saved scientific results."""
import json
import math
from pathlib import Path

from chem_suite.core.artifacts import write_json


def positive(config, name, default=None):
    value = float(config.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Value must be positive and finite: ' + name)
    return value


def make_controller_package(result_path, target, config, context):
    from .advanced import restore_analysis
    from .legacy.eis_controller import export_controller_package
    analysis, frequencies, source, provenance = restore_analysis(result_path)
    context.check_cancelled()
    maximum = config.get('controller_max_frequency_hz')
    package = export_controller_package(analysis, frequencies, target, source_file=source,
        sample_period_s=positive(config, 'controller_sample_period_s', 1e-6),
        current_full_scale_a=positive(config, 'controller_current_full_scale_a', 1),
        max_frequency_hz=positive(config, 'controller_max_frequency_hz') if maximum not in (None, '') else None)
    passport_path = Path(package.passport_file)
    passport = json.loads(passport_path.read_text())
    if passport['source']['sha256'].lower() != provenance['sha256'].lower():
        raise ValueError('Saved source hash does not match its provenance')
    passport['source']['file'] = provenance['path']
    passport['chem_suite_result'] = str(Path(result_path).resolve())
    write_json(passport_path, passport)
    context.check_cancelled()
    return package


def controller_package(request, context):
    from chem_suite.exports.actions import copy_file
    if len(request.inputs) != 1:
        raise ValueError('Controller export requires one completed fit')
    root = context.publication(request.config['destination'])
    context.progress(.1, 'Validating controller package')
    package = make_controller_package(request.inputs[0], context.artifact('controller-package'), request.config, context)
    for source in Path(package.package_directory).iterdir():
        copy_file(source, root / source.name, context)
    return {'destination': request.config['destination'], 'experiment_count': 1,
            'selected_order': package.selected_order, 'section_count': package.section_count}
