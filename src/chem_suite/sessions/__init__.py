"""Portable scientific sessions; declarations remain dependency-free."""
from chem_suite.core.contracts import ActionSpec, ModuleSpec

SPEC = ModuleSpec('sessions', 'Sessions', '0.1.0', (
    ActionSpec('save', 'Save session', 'chem_suite.sessions.actions:save_session', False),
    ActionSpec('restore', 'Open session', 'chem_suite.sessions.actions:restore_session', False),
    ActionSpec('result', 'Open saved result', 'chem_suite.sessions.actions:open_result', False),
), visible=False)
