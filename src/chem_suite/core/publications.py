"""Commit external output directories only after a job succeeds in the supervisor."""
import json
import shutil
from pathlib import Path
from chem_suite.core.artifacts import write_json


def prepare(directory, destination):
    directory = Path(directory)
    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise FileExistsError(f'Export destination already exists: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f'.chem-suite-{directory.name}.tmp'
    if staging.exists():
        raise FileExistsError(staging)
    write_json(directory / 'publication.json', {'target': str(target), 'staging': str(staging)})
    staging.mkdir()
    return staging


def finish(directory, success):
    directory = Path(directory)
    journal = directory / 'publication.json'
    if not journal.exists():
        return
    data = json.loads(journal.read_text())
    staging, target = Path(data['staging']), Path(data['target'])
    if staging.parent != target.parent or staging.name != f'.chem-suite-{directory.name}.tmp':
        raise ValueError('Invalid publication journal')
    try:
        if success:
            if target.exists():
                raise FileExistsError(f'Export destination already exists: {target}')
            staging.rename(target)
    finally:
        if staging.is_symlink():
            staging.unlink()
        elif staging.exists():
            shutil.rmtree(staging)
