"""Small local handoff record; session copying/validation belongs to workers."""
import json
from pathlib import Path
from uuid import uuid4

from chem_suite.core.artifacts import write_json


class SessionRecovery:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.pointer = self.directory / 'pending-session.json'

    def destination(self):
        return self.directory / 'sessions' / uuid4().hex

    def remember(self, destination, version):
        manifest = Path(destination).resolve() / 'session.json'
        if not manifest.is_file():
            raise ValueError('Saved recovery session is missing')
        manifest = manifest.resolve(strict=True)
        relative = manifest.relative_to(self.directory.resolve())
        write_json(self.pointer, {'schema_version': 1, 'session': relative.as_posix(), 'version': version})

    def pending(self):
        if not self.pointer.exists():
            return None
        if self.pointer.stat().st_size > 4096:
            raise ValueError('Invalid recovery session record')
        record = json.loads(self.pointer.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('schema_version') != 1:
            raise ValueError('Invalid recovery session record')
        relative = record.get('session')
        if not isinstance(relative, str):
            raise ValueError('Invalid recovery session record')
        parts = Path(relative).parts
        if len(parts) != 3 or parts[0] != 'sessions' or parts[2] != 'session.json' or parts[1] in {'.', '..'}:
            raise ValueError('Invalid recovery session record')
        manifest = (self.directory / relative).resolve(strict=True)
        if not manifest.is_relative_to(self.directory.resolve()) or not manifest.is_file():
            raise ValueError('Invalid recovery session record')
        return manifest

    def forget(self):
        # Keep the actual session, including source snapshots, for manual opening.
        self.pointer.unlink(missing_ok=True)
